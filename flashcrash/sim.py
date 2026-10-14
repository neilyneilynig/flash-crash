"""The simulation engine: agent workers, market maker, candles, news, players and analytics."""

from __future__ import annotations

import asyncio
import itertools
import math
import random
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from .brain import Decision, MockBrain, sentiment
from .market import Exchange, Trade
from .personas import ARCHETYPES, Agent, Archetype, spawn_agents

INITIAL_PRICE = 100.0
CANDLE_SECONDS = 2.0
ORDER_TTL = 15.0
MM_ID = "mm"

HUMAN = Archetype("human", "Humans", "🧑", "A human player.", 0, 0, (10_000, 10_000), (50, 50))


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
    price: float

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "source": self.source, "author": self.author, "ts": self.ts, "price": self.price}


@dataclass
class FeedItem:
    id: int
    agent: str
    handle: str
    archetype: str
    emoji: str
    action: str
    qty: int
    price: float | None
    thought: str
    model: str
    ts: float
    filled: int = 0
    human: bool = False


@dataclass
class World:
    """Everything that is reset when a new round starts."""
    exchange: Exchange
    agents: dict[str, Agent]
    candles: list[Candle] = field(default_factory=list)
    current: Candle | None = None
    news: list[NewsItem] = field(default_factory=list)
    feed: deque = field(default_factory=lambda: deque(maxlen=300))
    open_price: float = INITIAL_PRICE
    anchor: float = INITIAL_PRICE  # news-driven fundamental value (used by mock brains)
    volume: int = 0
    started: float = field(default_factory=time.time)


class Simulation:
    def __init__(self, brain, n_agents: int = 300, workers: int = 24, seed: int | None = None):
        self.brain = brain
        self.n_agents = n_agents
        self.n_workers = workers
        self.rng = random.Random(seed)
        self.paused = False
        self.autopilot = False
        self.gen = 0
        self._ids = itertools.count(1)
        self._tasks: list[asyncio.Task] = []
        self.listeners: list = []  # async callbacks for push events
        self.world = self._new_world()

    # --- setup ------------------------------------------------------------
    def _new_world(self) -> World:
        ex = Exchange(INITIAL_PRICE)
        agents = {}
        for agent, cash, shares in spawn_agents(self.n_agents, INITIAL_PRICE, self.rng):
            agents[agent.id] = agent
            ex.open_account(agent.id, cash, shares)
        ex.open_account(MM_ID, 50_000_000, 400_000)
        w = World(exchange=ex, agents=agents)
        ex.trade_listeners.append(self._on_trade)
        now = int(time.time())
        w.current = Candle(now, INITIAL_PRICE, INITIAL_PRICE, INITIAL_PRICE, INITIAL_PRICE)
        return w

    def reset(self) -> None:
        players = [a for a in self.world.agents.values() if a.human]
        self.gen += 1
        self.world = self._new_world()
        for p in players:  # players keep their seat but get a fresh bankroll
            p.memory.clear()
            self.world.agents[p.id] = p
            self.world.exchange.open_account(p.id, *self._human_bankroll())

    def _human_bankroll(self) -> tuple[int, int]:
        return 10_000, 50

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._worker()) for _ in range(self.n_workers)]
        self._tasks += [loop.create_task(self._candle_loop()), loop.create_task(self._mm_loop()),
                        loop.create_task(self._autopilot_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- market data ------------------------------------------------------
    @property
    def price(self) -> float:
        return self.world.exchange.last_price

    def _on_trade(self, t: Trade) -> None:
        c = self.world.current
        c.high, c.low, c.close = max(c.high, t.price), min(c.low, t.price), t.price
        c.volume += t.qty
        self.world.volume += t.qty

    def closes(self, n: int = 12) -> list[float]:
        return [c.close for c in self.world.candles[-n:]] + [self.world.current.close]

    async def _candle_loop(self) -> None:
        while True:
            await asyncio.sleep(CANDLE_SECONDS)
            w = self.world
            done = w.current
            w.candles.append(done)
            del w.candles[:-900]
            t = max(int(time.time()), done.time + 1)
            p = done.close
            w.current = Candle(t, p, p, p, p)
            w.exchange.expire(time.time() - ORDER_TTL)
            await self._emit({"type": "candle", "candle": done.as_dict()})

    async def _mm_loop(self) -> None:
        """A market maker quoting around a slow moving average: it absorbs noise but lets real news through."""
        ref = None
        while True:
            await asyncio.sleep(1.0)
            ex = self.world.exchange
            p = ex.last_price
            ref = p if ref is None or self.world.volume == 0 else ref + 0.08 * (p - ref)
            ex.cancel_agent(MM_ID)
            vol = self.analytics().get("volatility", 0) or 0
            spread = 0.0015 + min(0.006, vol * 0.05)
            inv = ex.accounts[MM_ID].shares / 400_000 - 1  # skew quotes to unload inventory
            mid = (0.5 * p + 0.5 * ref) * (1 - 0.01 * inv)
            for lvl in range(1, 9):
                size = 60 * lvl
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

    # --- agents -----------------------------------------------------------
    def _pick_agent(self) -> Agent | None:
        pool = [a for a in self.world.agents.values() if not a.busy and not a.human]
        if not pool:
            return None
        return self.rng.choices(pool, weights=[a.archetype.weight for a in pool])[0]

    def context_for(self, agent: Agent) -> dict:
        w, ex = self.world, self.world.exchange
        acct = ex.accounts[agent.id]
        price = ex.last_price
        now = time.time()
        recent = [f for f in w.feed if f.action != "hold" and f.agent != agent.id][-40:]
        timeline = self.rng.sample(recent, min(3, len(recent)))
        return {
            "price": price,
            "change_pct": (price / w.open_price - 1) * 100,
            "anchor": w.anchor,
            "bid": ex.best_bid(),
            "ask": ex.best_ask(),
            "closes": self.closes(),
            "cash": acct.cash,
            "shares": acct.shares,
            "pnl_pct": (acct.equity(price) / acct.start_equity - 1) * 100 if acct.start_equity else 0,
            "max_buy": int(acct.free_cash // (price * 1.01)),
            "max_sell": acct.free_shares,
            "news": [{"text": n.text, "age": int(now - n.ts)} for n in reversed(w.news[-3:])],
            "timeline": [{"handle": f.handle, "text": f.thought, "side": 1 if f.action == "buy" else -1} for f in timeline],
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
            agent.busy = True
            try:
                decision = await self.brain.decide(agent, self.context_for(agent))
            except Exception:
                await asyncio.sleep(1.0)  # back off on provider errors
                continue
            finally:
                agent.busy = False
            if gen == self.gen:
                await self.apply(agent, decision)

    async def apply(self, agent: Agent, d: Decision) -> FeedItem:
        ex = self.world.exchange
        price = ex.last_price
        filled = 0
        if d.action in ("buy", "sell") and d.qty > 0:
            limit = d.price
            if limit is not None:  # keep fat-fingered limits within ±20%
                limit = min(max(limit, price * 0.8), price * 1.2)
            ex.cancel_agent(agent.id)
            filled = sum(t.qty for t in ex.place(agent.id, d.action, d.qty, limit))
        if d.thought:
            agent.memory.append(d.thought)
            del agent.memory[:-4]
        item = FeedItem(next(self._ids), agent.name, agent.handle, agent.archetype.key, agent.archetype.emoji,
                        d.action, d.qty, d.price, d.thought, d.model, time.time(), filled, agent.human)
        self.world.feed.append(item)
        await self._emit({"type": "feed", "item": item.__dict__})
        return item

    # --- news -------------------------------------------------------------
    async def add_news(self, text: str, source: str = "crowd", author: str = "anon") -> NewsItem:
        text = " ".join(text.split())[:160]
        item = NewsItem(next(self._ids), text, source, author, time.time(), self.price)
        self.world.anchor *= 1 + 0.15 * sentiment(text)
        self.world.news.append(item)
        del self.world.news[:-50]
        await self._emit({"type": "news", "item": item.as_dict()})
        return item

    async def newsroom(self) -> NewsItem:
        ctx = {"price": self.price, "change_pct": (self.price / self.world.open_price - 1) * 100,
               "news": [{"text": n.text} for n in self.world.news[-3:]]}
        headline = await self.brain.write_headline(ctx)
        return await self.add_news(headline, "newsroom", "AI newsroom")

    # --- human players ----------------------------------------------------
    def join(self, name: str) -> Agent:
        name = (" ".join(name.split()) or "Player")[:20]
        pid = f"p{next(self._ids)}"
        agent = Agent(pid, name, name.lower().replace(" ", "_"), HUMAN, INITIAL_PRICE, 1.0, human=True)
        self.world.agents[pid] = agent
        self.world.exchange.open_account(pid, *self._human_bankroll())
        return agent

    def player(self, pid: str) -> Agent | None:
        a = self.world.agents.get(pid)
        return a if a and a.human else None

    async def human_trade(self, pid: str, side: str, qty: int | str) -> FeedItem:
        agent = self.player(pid)
        if agent is None:
            raise KeyError(pid)
        acct = self.world.exchange.accounts[pid]
        if qty == "all":
            qty = int(acct.free_cash // (self.price * 1.02)) if side == "buy" else acct.free_shares
        d = Decision(side, int(qty), None, f"{'Bought' if side == 'buy' else 'Sold'} {qty} by hand", "human")
        return await self.apply(agent, d)

    def portfolio(self, pid: str) -> dict:
        acct = self.world.exchange.accounts[pid]
        eq = acct.equity(self.price)
        return {"cash": round(acct.cash, 2), "shares": acct.shares, "equity": round(eq, 2),
                "pnl_pct": round((eq / acct.start_equity - 1) * 100, 2), "rank": self._rank(pid)}

    def _rank(self, pid: str) -> int:
        p = self.price
        ex = self.world.exchange
        mine = ex.accounts[pid].equity(p) / ex.accounts[pid].start_equity
        better = sum(1 for aid in self.world.agents if ex.accounts[aid].equity(p) / ex.accounts[aid].start_equity > mine)
        return better + 1

    # --- analytics --------------------------------------------------------
    def leaderboard(self) -> list[dict]:
        p, ex = self.price, self.world.exchange
        groups: dict[str, list[float]] = {}
        meta = {a.key: a for a in ARCHETYPES} | {"human": HUMAN}
        for a in self.world.agents.values():
            acct = ex.accounts[a.id]
            groups.setdefault(a.archetype.key, []).append(acct.equity(p) / acct.start_equity - 1)
        rows = [{"key": k, "label": meta[k].label, "emoji": meta[k].emoji, "count": len(v),
                 "pnl_pct": round(statistics.fmean(v) * 100, 2)} for k, v in groups.items()]
        return sorted(rows, key=lambda r: -r["pnl_pct"])

    def players_board(self) -> list[dict]:
        rows = [{"name": a.name, **self.portfolio(a.id)} for a in self.world.agents.values() if a.human]
        return sorted(rows, key=lambda r: -r["pnl_pct"])

    def analytics(self) -> dict:
        closes = [c.close for c in self.world.candles[-300:]]
        if len(closes) < 10:
            return {}
        r = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
        if len(r) < 8 or statistics.pstdev(r) == 0:
            return {"volatility": 0}
        mu, sd = statistics.fmean(r), statistics.pstdev(r)
        kurt = statistics.fmean([((x - mu) / sd) ** 4 for x in r]) - 3

        def acf(xs):
            m = statistics.fmean(xs)
            den = sum((x - m) ** 2 for x in xs) or 1
            return sum((a - m) * (b - m) for a, b in zip(xs, xs[1:])) / den

        peak, mdd = closes[0], 0.0
        for c in closes:
            peak = max(peak, c)
            mdd = max(mdd, 1 - c / peak)
        recent = [f for f in self.world.feed if f.action != "hold"][-60:]
        herd = abs(sum(1 if f.action == "buy" else -1 for f in recent)) / len(recent) if recent else 0
        return {
            "volatility": round(sd * math.sqrt(len(r)), 4),
            "excess_kurtosis": round(kurt, 2),  # >0 = fat tails, like real markets
            "vol_clustering": round(acf([abs(x) for x in r]), 2),  # >0 = calm/storm regimes
            "return_autocorr": round(acf(r), 2),  # ~0 in efficient markets
            "max_drawdown_pct": round(mdd * 100, 1),
            "herding": round(herd, 2),
        }

    def snapshot(self) -> dict:
        w, ex = self.world, self.world.exchange
        return {
            "price": round(self.price, 2),
            "open": w.open_price,
            "change_pct": round((self.price / w.open_price - 1) * 100, 2),
            "volume": w.volume,
            "book": ex.depth(8),
            "leaderboard": self.leaderboard(),
            "players": self.players_board(),
            "analytics": self.analytics(),
            "brain": {"mode": self.brain.mode, "provider": getattr(self.brain, "provider", ""),
                      "fast_model": self.brain.fast_model, "big_model": self.brain.big_model, **self.brain.stats.snapshot()},
            "agents": sum(1 for a in w.agents.values() if not a.human),
            "paused": self.paused,
            "autopilot": self.autopilot,
            "current": w.current.as_dict(),
        }

    def history(self) -> dict:
        w = self.world
        return {"candles": [c.as_dict() for c in w.candles], "news": [n.as_dict() for n in w.news[-20:]],
                "feed": [f.__dict__ for f in list(w.feed)[-40:]]}

    async def _emit(self, event: dict) -> None:
        for fn in list(self.listeners):
            try:
                await fn(event)
            except Exception:
                pass


def is_mock(brain) -> bool:
    return isinstance(brain, MockBrain)
