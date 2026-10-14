"""The simulation engine: agent workers, market makers, candles, news, players, rounds and analytics.

Several stocks trade side by side. Each has its own order book; every trader has one cash wallet
shared across all of them, so money flows from stock to stock as the news moves.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import random
import re
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from .brain import Decision, MockBrain, sentiment
from .market import Exchange, Trade, Wallet
from .personas import ARCHETYPES, BY_TICKER, STOCKS, TICKER, TICKERS, Agent, Archetype, Stock, spawn_agents

CANDLE_SECONDS = 2.0
ORDER_TTL = 15.0
MM_ID = "mm"
MM_NOTIONAL = 40_000_000  # market maker inventory per stock, in dollars at the open

HUMAN = Archetype("human", "Humans", "🧑", "A human player.", 0, 0, (10_000, 10_000), (0, 0))
HUMAN_CASH = 10_000
HUMAN_STAKE = 2_000  # starting dollars of each stock, so players can sell from the first second
PLAYER_TIMEOUT = 60  # seconds without a phone check-in before a player is dropped at the next reset


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def as_dict(self) -> dict:
        return {"time": self.time, "open": self.open, "high": self.high, "low": self.low, "close": self.close, "volume": self.volume}


@dataclass
class NewsItem:
    id: int
    text: str
    source: str  # crowd | voice | newsroom | real | alexa
    author: str
    ts: float
    tickers: list[str]  # stocks this headline is about
    prices: dict[str, float]  # their prices when it broke

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "source": self.source, "author": self.author, "ts": self.ts,
                "tickers": self.tickers, "prices": self.prices}


@dataclass
class FeedItem:
    id: int
    agent: str
    handle: str
    archetype: str
    emoji: str
    ticker: str
    action: str
    qty: int
    price: float | None
    thought: str
    model: str
    ts: float
    filled: int = 0
    human: bool = False
    fill_price: float | None = None  # average price actually paid/received


@dataclass
class Market:
    stock: Stock
    exchange: Exchange
    current: Candle
    candles: list[Candle] = field(default_factory=list)
    anchor: float = 0.0  # news-driven fundamental value (used by mock brains)
    volume: int = 0
    mm_ref: float | None = None

    @property
    def price(self) -> float:
        return self.exchange.last_price

    @property
    def open_price(self) -> float:
        return self.stock.price

    @property
    def change_pct(self) -> float:
        return (self.price / self.stock.price - 1) * 100

    def closes(self, n: int = 12) -> list[float]:
        return [c.close for c in self.candles[-n:]] + [self.current.close]


@dataclass
class World:
    """Everything that is reset when a new round starts."""
    markets: dict[str, Market]
    agents: dict[str, Agent]
    wallets: dict[str, Wallet]
    start_equity: dict[str, float]
    news: list[NewsItem] = field(default_factory=list)
    feed: deque = field(default_factory=lambda: deque(maxlen=300))
    started: float = field(default_factory=time.time)
    fills: dict[str, list[dict]] = field(default_factory=dict)  # player id -> their trades, for chart markers
    cost: dict[str, dict[str, float]] = field(default_factory=dict)  # player id -> ticker -> cost basis held


_WORD = re.compile(r"[a-z$][a-z0-9$\-]*")


def route_news(text: str, focus: str | None = None) -> list[str]:
    """Which stocks a headline is about: named companies first, then industry words, then the
    stock the leaker was looking at; otherwise it's macro news and hits everything."""
    low = " " + " ".join(_WORD.findall(text.lower())) + " "

    def hits(words):
        return [s.ticker for s in STOCKS if any(f" {w} " in low or f" ${w} " in low for w in words(s))]

    named = hits(lambda s: (s.ticker.lower(),) + s.names)
    if named:
        return named
    if focus in BY_TICKER:
        return [focus]
    return hits(lambda s: s.keywords) or list(TICKERS)


class Simulation:
    def __init__(self, brain, n_agents: int = 300, workers: int = 24, seed: int | None = None):
        self.brain = brain
        self.n_agents = n_agents
        self.n_workers = workers
        self.rng = random.Random(seed)
        self.paused = False
        self.autopilot = False
        self.gen = 0
        self.round_end: float | None = None
        self.round_minutes = 0.0
        self._ids = itertools.count(1)
        self._tasks: list[asyncio.Task] = []
        self.listeners: list = []  # async callbacks for push events
        self.world = self._new_world()

    # --- setup ------------------------------------------------------------
    def _new_world(self) -> World:
        now = int(time.time())
        markets = {}
        for st in STOCKS:
            ex = Exchange(st.price)
            m = Market(st, ex, Candle(now, st.price, st.price, st.price, st.price), anchor=st.price)
            ex.trade_listeners.append(lambda t, m=m: self._on_trade(m, t))
            ex.open_account(MM_ID, MM_NOTIONAL * st.liquidity, int(MM_NOTIONAL * st.liquidity / st.price))
            self._quote(m)  # open with a book so the first orders fill
            markets[st.ticker] = m
        w = World(markets=markets, agents={}, wallets={}, start_equity={})
        for agent, cash, shares in spawn_agents(self.n_agents, self.rng):
            self._open(w, agent, cash, shares)
        return w

    def _open(self, w: World, agent: Agent, cash: float, shares: dict[str, int]) -> None:
        wallet = Wallet(cash)
        w.agents[agent.id] = agent
        w.wallets[agent.id] = wallet
        for t, m in w.markets.items():
            m.exchange.open_account(agent.id, 0, shares.get(t, 0), wallet)
        w.start_equity[agent.id] = self._equity(w, agent.id)

    def _seat(self, agent: Agent) -> None:
        stake = {t: int(HUMAN_STAKE / m.open_price) for t, m in self.world.markets.items()}
        self._open(self.world, agent, HUMAN_CASH, stake)
        self.world.fills[agent.id] = []
        # starting shares count as bought at the open
        self.world.cost[agent.id] = {t: q * self.world.markets[t].open_price for t, q in stake.items()}

    def reset(self) -> None:
        # players keep their seat if their phone is still connected; closed tabs drop off
        players = [a for a in self.world.agents.values() if a.human and time.time() - a.seen < PLAYER_TIMEOUT]
        self.gen += 1
        self.round_end, self.round_minutes = None, 0.0
        self.world = self._new_world()
        for p in players:  # players keep their seat but get a fresh bankroll
            p.memory.clear()
            self._seat(p)

    async def start_round(self, minutes: float = 5) -> None:
        self.reset()
        self.paused = False
        self.round_minutes = minutes
        self.round_end = time.time() + minutes * 60
        await self._emit({"type": "reset", "data": {**self.snapshot(), **self.history()}})

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._worker()) for _ in range(self.n_workers)]
        self._tasks += [loop.create_task(self._candle_loop()), loop.create_task(self._mm_loop()),
                        loop.create_task(self._autopilot_loop()), loop.create_task(self._round_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def round_over(self) -> bool:
        return self.round_end is None and self.round_minutes > 0

    # --- market data ------------------------------------------------------
    def market(self, ticker: str | None) -> Market:
        return self.world.markets.get((ticker or TICKER).upper().lstrip("$")) or self.world.markets[TICKER]

    @property
    def price(self) -> float:
        """Price of the headline stock."""
        return self.world.markets[TICKER].price

    def _equity(self, w: World, aid: str) -> float:
        return w.wallets[aid].cash + sum(m.exchange.accounts[aid].shares * m.price for m in w.markets.values())

    def equity(self, aid: str) -> float:
        return self._equity(self.world, aid)

    def pnl_pct(self, aid: str) -> float:
        start = self.world.start_equity.get(aid) or 1
        return (self.equity(aid) / start - 1) * 100

    def _on_trade(self, m: Market, t: Trade) -> None:
        c = m.current
        c.high, c.low, c.close = max(c.high, t.price), min(c.low, t.price), t.price
        c.volume += t.qty
        m.volume += t.qty

    async def _candle_loop(self) -> None:
        while True:
            await asyncio.sleep(CANDLE_SECONDS)
            now = time.time()
            for tk, m in self.world.markets.items():
                done = m.current
                m.candles.append(done)
                del m.candles[:-900]
                t = max(int(now), done.time + 1)
                p = done.close
                m.current = Candle(t, p, p, p, p)
                m.exchange.expire(now - ORDER_TTL)
                await self._emit({"type": "candle", "ticker": tk, "candle": done.as_dict()})

    async def _mm_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for m in self.world.markets.values():
                self._quote(m, self.analytics(m.stock.ticker).get("volatility", 0) or 0)

    def _quote(self, m: Market, vol: float = 0.0) -> None:
        """Market maker quoting around a slow moving average: it absorbs noise but lets real news through."""
        ex, st = m.exchange, m.stock
        p = ex.last_price
        m.mm_ref = p if m.mm_ref is None or m.volume == 0 else m.mm_ref + 0.08 * (p - m.mm_ref)
        ex.cancel_agent(MM_ID)
        spread = 0.0015 + min(0.006, vol * 0.05)
        inv0 = MM_NOTIONAL * st.liquidity / st.price
        inv = ex.accounts[MM_ID].shares / inv0 - 1  # skew quotes to unload inventory
        mid = (0.5 * p + 0.5 * m.mm_ref) * (1 - 0.01 * inv)
        for lvl in range(1, 9):
            size = max(1, int(6000 * lvl * st.liquidity / p))  # ~$6k per level at full liquidity
            ex.place(MM_ID, "buy", size, min(mid, p) * (1 - spread * lvl))
            ex.place(MM_ID, "sell", size, max(mid, p) * (1 + spread * lvl))

    async def _autopilot_loop(self) -> None:
        while True:
            await asyncio.sleep(40)
            if self.autopilot and not self.paused:
                try:
                    await self.newsroom()
                except Exception:
                    pass

    async def _round_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self.round_end and time.time() >= self.round_end:
                self.round_end = None
                self.paused = True
                await self._emit({"type": "round_over", "data": self.results()})

    def results(self) -> dict:
        board = self.leaderboard()
        return {"players": self.players_board(), "best_ai": next((r for r in board if r["key"] != "human"), None),
                "leaderboard": board, "markets": self.markets_summary()}

    # --- agents -----------------------------------------------------------
    def _pick_agent(self) -> Agent | None:
        pool = [a for a in self.world.agents.values() if not a.busy and not a.human]
        if not pool:
            return None
        return self.rng.choices(pool, weights=[a.archetype.weight for a in pool])[0]

    def _pick_ticker(self, agent: Agent) -> str:
        """Attention: traders watch their favourites, but fresh news and big moves pull everyone in."""
        now = time.time()
        heat = {t: (2.0 if t in agent.favorites else 0.6) for t in TICKERS}
        for n in self.world.news[-6:]:
            age = now - n.ts
            if age < 45:
                for t in n.tickers:
                    heat[t] += 9.0 * (1 - age / 45) / len(n.tickers) ** 0.5  # breaking news pulls the crowd in
        for t, m in self.world.markets.items():
            closes = m.closes(6)
            heat[t] += min(3.0, abs(closes[-1] / closes[0] - 1) * 60)
        return self.rng.choices(TICKERS, weights=[heat[t] for t in TICKERS])[0]

    def context_for(self, agent: Agent, ticker: str) -> dict:
        w = self.world
        m = w.markets[ticker]
        ex = m.exchange
        acct = ex.accounts[agent.id]
        price = ex.last_price
        now = time.time()
        recent = [f for f in w.feed if f.action != "hold" and f.agent != agent.id][-60:]
        same = [f for f in recent if f.ticker == ticker]
        timeline = self.rng.sample(same or recent, min(3, len(same or recent)))
        news = [n for n in w.news if ticker in n.tickers][-3:]
        return {
            "ticker": ticker,
            "tickers": TICKERS,
            "company": m.stock.company,
            "price": price,
            "change_pct": m.change_pct,
            "anchor": m.anchor,
            "fair": agent.fair.get(ticker, 1.0),
            "bid": ex.best_bid(),
            "ask": ex.best_ask(),
            "closes": m.closes(),
            "cash": acct.cash,
            "shares": acct.shares,
            "holdings": {t: mm.exchange.accounts[agent.id].shares for t, mm in w.markets.items()},
            "pnl_pct": self.pnl_pct(agent.id),
            "max_buy": int(acct.free_cash // (price * 1.01)),
            "max_sell": acct.free_shares,
            "news": [{"text": n.text, "age": int(now - n.ts)} for n in reversed(news)],
            "timeline": [{"handle": f.handle, "ticker": f.ticker, "text": f.thought,
                          "side": 1 if f.action == "buy" else -1} for f in timeline],
        }

    async def _worker(self) -> None:
        while True:
            if self.paused:
                await asyncio.sleep(0.3)
                continue
            agent = self._pick_agent()
            if agent is None:
                await asyncio.sleep(0.2)
                continue
            gen = self.gen
            ticker = self._pick_ticker(agent)
            agent.busy = True
            try:
                decision = await self.brain.decide(agent, self.context_for(agent, ticker))
            except Exception:
                await asyncio.sleep(1.0)  # back off on provider errors
                continue
            finally:
                agent.busy = False
            if gen == self.gen and not self.paused:
                await self.apply(agent, ticker, decision)

    async def apply(self, agent: Agent, ticker: str, d: Decision) -> FeedItem:
        ex = self.world.markets[ticker].exchange
        price = ex.last_price
        filled, fill_price = 0, None
        if d.action in ("buy", "sell") and d.qty > 0:
            limit = d.price
            if limit is not None:  # keep fat-fingered limits within ±20%
                limit = min(max(limit, price * 0.8), price * 1.2)
            ex.cancel_agent(agent.id)
            trades = ex.place(agent.id, d.action, d.qty, limit)
            filled = sum(t.qty for t in trades)
            if filled:
                fill_price = round(sum(t.price * t.qty for t in trades) / filled, 2)
        if d.thought:
            agent.memory.append(d.thought)
            del agent.memory[:-4]
        item = FeedItem(next(self._ids), agent.name, agent.handle, agent.archetype.key, agent.archetype.emoji, ticker,
                        d.action, d.qty, d.price, d.thought, d.model, time.time(), filled, agent.human, fill_price)
        self.world.feed.append(item)
        await self._emit({"type": "feed", "item": item.__dict__})
        return item

    # --- news -------------------------------------------------------------
    async def add_news(self, text: str, source: str = "crowd", author: str = "anon", ticker: str | None = None) -> NewsItem:
        text = " ".join(text.split())[:160]
        tickers = route_news(text, ticker)
        mood = sentiment(text)
        macro = len(tickers) == len(TICKERS) and len(TICKERS) > 1
        for t in tickers:
            self.world.markets[t].anchor *= 1 + (0.08 if macro else 0.2) * mood
        item = NewsItem(next(self._ids), text, source, author, time.time(), tickers,
                        {t: round(self.world.markets[t].price, 2) for t in tickers})
        self.world.news.append(item)
        del self.world.news[:-50]
        await self._emit({"type": "news", "item": item.as_dict()})
        return item

    async def newsroom(self, ticker: str | None = None) -> NewsItem:
        m = self.world.markets.get(ticker or "") or self.rng.choice(list(self.world.markets.values()))
        ctx = {"ticker": m.stock.ticker, "company": m.stock.company, "blurb": m.stock.blurb, "price": m.price,
               "change_pct": m.change_pct, "news": [{"text": n.text} for n in self.world.news[-3:]]}
        headline = await self.brain.write_headline(ctx)
        return await self.add_news(headline, "newsroom", "AI newsroom", m.stock.ticker)

    # --- human players ----------------------------------------------------
    def join(self, name: str) -> Agent:
        name = (" ".join(name.split()) or "Player")[:20]
        pid = f"p{next(self._ids)}"
        agent = Agent(pid, name, name.lower().replace(" ", "_"), HUMAN, {t: 1.0 for t in TICKERS}, 1.0, human=True,
                      seen=time.time())
        self._seat(agent)
        return agent

    def player(self, pid: str) -> Agent | None:
        a = self.world.agents.get(pid)
        if a and a.human:
            a.seen = time.time()
            return a
        return None

    async def human_trade(self, pid: str, side: str, qty: int | str, ticker: str = TICKER) -> FeedItem:
        agent = self.player(pid)
        if agent is None:
            raise KeyError(pid)
        if self.round_over:
            raise ValueError("the round is over, start a new one on the TV")
        m = self.market(ticker)
        tk = m.stock.ticker
        acct = m.exchange.accounts[pid]
        if qty == "all":
            qty = int(acct.free_cash // (m.price * 1.02)) if side == "buy" else acct.free_shares
        held = acct.shares
        d = Decision(side, int(qty), None, f"{'Bought' if side == 'buy' else 'Sold'} {qty} ${tk} by hand", "human")
        item = await self.apply(agent, tk, d)
        if item.filled:
            w = self.world
            cost = w.cost.setdefault(pid, {})
            if side == "buy":
                cost[tk] = cost.get(tk, 0.0) + item.filled * item.fill_price
            elif held:  # selling keeps the average cost of what's left
                cost[tk] = cost.get(tk, 0.0) * (held - item.filled) / held
            w.fills.setdefault(pid, []).append({"ticker": tk, "time": m.current.time, "side": side,
                                                "qty": item.filled, "price": item.fill_price})
            del w.fills[pid][:-100]
        return item

    def portfolio(self, pid: str, ranks: dict[str, int] | None = None) -> dict:
        w = self.world
        positions = []
        for t, m in w.markets.items():
            sh = m.exchange.accounts[pid].shares
            cost = w.cost.get(pid, {}).get(t, 0.0)
            avg = cost / sh if sh else None
            positions.append({"ticker": t, "shares": sh, "price": round(m.price, 2), "value": round(sh * m.price, 2),
                              "avg_cost": round(avg, 2) if avg else None,
                              "pnl_pct": round((m.price / avg - 1) * 100, 2) if avg else None})
        eq = self.equity(pid)
        return {"cash": round(w.wallets[pid].cash, 2), "equity": round(eq, 2), "pnl_pct": round(self.pnl_pct(pid), 2),
                "rank": (ranks or self._ranks())[pid], "of": len(w.agents), "positions": positions,
                "trades": w.fills.get(pid, [])}

    def _ranks(self) -> dict[str, int]:
        order = sorted(self.world.agents, key=lambda aid: -self.pnl_pct(aid))
        return {aid: i + 1 for i, aid in enumerate(order)}

    # --- analytics --------------------------------------------------------
    def leaderboard(self) -> list[dict]:
        groups: dict[str, list[float]] = {}
        meta = {a.key: a for a in ARCHETYPES} | {"human": HUMAN}
        for a in self.world.agents.values():
            groups.setdefault(a.archetype.key, []).append(self.pnl_pct(a.id))
        rows = [{"key": k, "label": meta[k].label, "emoji": meta[k].emoji, "count": len(v),
                 "pnl_pct": round(statistics.fmean(v), 2)} for k, v in groups.items()]
        return sorted(rows, key=lambda r: -r["pnl_pct"])

    def players_board(self) -> list[dict]:
        ranks = self._ranks()
        rows = [{"id": a.id, "name": a.name, "pnl_pct": round(self.pnl_pct(a.id), 2), "equity": round(self.equity(a.id), 2),
                 "rank": ranks[a.id]} for a in self.world.agents.values() if a.human]
        return sorted(rows, key=lambda r: -r["pnl_pct"])

    def analytics(self, ticker: str = TICKER) -> dict:
        m = self.world.markets[ticker]
        closes = [c.close for c in m.candles[-300:]]
        if len(closes) < 10:
            return {}
        r = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
        if len(r) < 8 or statistics.pstdev(r) == 0:
            return {"volatility": 0}
        mu, sd = statistics.fmean(r), statistics.pstdev(r)
        kurt = statistics.fmean([((x - mu) / sd) ** 4 for x in r]) - 3

        def acf(xs):
            mean = statistics.fmean(xs)
            den = sum((x - mean) ** 2 for x in xs) or 1
            return sum((a - mean) * (b - mean) for a, b in zip(xs, xs[1:])) / den

        peak, mdd = closes[0], 0.0
        for c in closes:
            peak = max(peak, c)
            mdd = max(mdd, 1 - c / peak)
        recent = [f for f in self.world.feed if f.action != "hold" and f.ticker == ticker][-60:]
        herd = abs(sum(1 if f.action == "buy" else -1 for f in recent)) / len(recent) if recent else 0
        return {
            "volatility": round(sd * math.sqrt(len(r)), 4),
            "excess_kurtosis": round(kurt, 2),  # >0 = fat tails, like real markets
            "vol_clustering": round(acf([abs(x) for x in r]), 2),  # >0 = calm/storm regimes
            "return_autocorr": round(acf(r), 2),  # ~0 in efficient markets
            "max_drawdown_pct": round(mdd * 100, 1),
            "herding": round(herd, 2),
        }

    def markets_summary(self) -> list[dict]:
        return [{"ticker": t, "company": m.stock.company, "blurb": m.stock.blurb, "price": round(m.price, 2),
                 "open": m.open_price, "change_pct": round(m.change_pct, 2), "volume": m.volume}
                for t, m in self.world.markets.items()]

    def snapshot(self) -> dict:
        w = self.world
        gpu = w.markets[TICKER]
        return {
            "price": round(gpu.price, 2),  # headline stock, for simple clients
            "change_pct": round(gpu.change_pct, 2),
            "markets": self.markets_summary(),
            "current": {t: m.current.as_dict() for t, m in w.markets.items()},
            "analytics": {t: self.analytics(t) for t in w.markets},
            "leaderboard": self.leaderboard(),
            "players": self.players_board(),
            "brain": {"mode": self.brain.mode, "provider": getattr(self.brain, "provider", ""),
                      "fast_model": self.brain.fast_model, "big_model": self.brain.big_model, **self.brain.stats.snapshot()},
            "agents": sum(1 for a in w.agents.values() if not a.human),
            "paused": self.paused,
            "autopilot": self.autopilot,
            "round_left": max(0, round(self.round_end - time.time())) if self.round_end else None,
            "round_minutes": self.round_minutes,
            "round_over": self.round_over,
        }

    def history(self) -> dict:
        w = self.world
        return {"candles": {t: [c.as_dict() for c in m.candles] for t, m in w.markets.items()},
                "news": [n.as_dict() for n in w.news[-20:]],
                "feed": [f.__dict__ for f in list(w.feed)[-40:]]}

    async def _emit(self, event: dict) -> None:
        for fn in list(self.listeners):
            try:
                await fn(event)
            except Exception:
                pass


def is_mock(brain) -> bool:
    return isinstance(brain, MockBrain)
