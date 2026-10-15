"""Persistent Market Days mode: PostgreSQL state plus Alpaca paper-market integration.

A Market Day is a long-running game on *real* stocks. Humans and a desk of AI fund managers (the same persona brains as
the party game) each get a virtual bankroll, trade at real Alpaca bid/ask quotes, and compete on a leaderboard.
AI managers also place matching orders on the Alpaca **paper** account. Every fill is stored, so cash, positions and
P&L are always derived from an auditable ledger.

Without DATABASE_URL the store is in-memory, and without Alpaca keys quotes are simulated; both are flagged in
/api/days/config so the UI can say so.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import asyncpg
import httpx

from .personas import BY_KEY, Agent

START_CASH = 100_000.0
MAX_ORDER_QTY = int(os.getenv("MARKET_DAYS_MAX_ORDER_QTY", "25"))  # per AI paper order
AI_DESK = ("wsb", "value", "quant", "whale")  # archetypes managing money in every game
DESK_NAMES = {"wsb": "Chad the Degen", "value": "Warren Jr.", "quant": "Q-Bot 9000", "whale": "Moby Capital"}


class MarketDaysError(RuntimeError):
    """A safe error suitable for returning from the Market Days API."""


class MarketDaysInputError(MarketDaysError):
    """The request itself was invalid (HTTP 400 rather than 503)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_days_games (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    symbols JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE market_days_games ADD COLUMN IF NOT EXISTS start_cash DOUBLE PRECISION NOT NULL DEFAULT 100000;
CREATE TABLE IF NOT EXISTS market_days_events (
    id BIGSERIAL PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES market_days_games(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS market_days_events_game_id_id ON market_days_events(game_id, id DESC);
CREATE TABLE IF NOT EXISTS market_days_players (
    id UUID PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES market_days_games(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    archetype TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS market_days_fills (
    id BIGSERIAL PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES market_days_games(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES market_days_players(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    order_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS market_days_fills_game ON market_days_fills(game_id, id);
"""


def _database_url(url: str) -> str:
    return "postgresql://" + url.removeprefix("postgres://") if url.startswith("postgres://") else url


def _plain(row) -> dict:
    """asyncpg rows -> JSON-friendly dicts (UUIDs and timestamps as strings)."""
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        out[k] = v
    return out


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise MarketDaysInputError("invalid id") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------------------------- quotes & orders

@dataclass(frozen=True)
class AlpacaSettings:
    key: str
    secret: str
    trading_url: str
    data_url: str
    allow_orders: bool = True

    @classmethod
    def from_env(cls) -> "AlpacaSettings | None":
        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
        if not key or not secret:
            return None
        trading = os.getenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
        # Safety: this mode only ever trades paper money. A live endpoint disables orders entirely.
        paper = (urlparse(trading).hostname or "").startswith("paper-api.")
        return cls(key, secret, trading, os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"), paper)


class AlpacaPaper:
    simulated = False

    def __init__(self, settings: AlpacaSettings, client: httpx.AsyncClient | None = None):
        self.settings, self.client = settings, client or httpx.AsyncClient(timeout=10)
        self._owned_client = client is None

    @property
    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.settings.key, "APCA-API-SECRET-KEY": self.settings.secret}

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = await self.client.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise MarketDaysError(f"Alpaca rejected the request ({exc.response.status_code})") from exc
        except httpx.HTTPError as exc:
            raise MarketDaysError("Alpaca is unavailable; try again shortly") from exc

    async def quote(self, symbol: str) -> dict:
        data = await self._request("GET", f"{self.settings.data_url}/v2/stocks/{symbol}/quotes/latest")
        quote = data.get("quote") or {}
        bid, ask = quote.get("bp"), quote.get("ap")
        if bid is None or ask is None:
            raise MarketDaysError(f"Alpaca returned no usable quote for {symbol}")
        return {"symbol": symbol, "bid": float(bid), "ask": float(ask), "at": quote.get("t")}

    async def closes(self, symbol: str, days: int = 10) -> list[float]:
        """Recent daily closes, oldest first (empty if unavailable: decisions still work without them)."""
        try:
            data = await self._request("GET", f"{self.settings.data_url}/v2/stocks/{symbol}/bars",
                                       params={"timeframe": "1Day", "limit": days})
        except MarketDaysError:
            return []
        return [float(b["c"]) for b in data.get("bars") or [] if "c" in b]

    async def clock(self) -> dict:
        return await self._request("GET", f"{self.settings.trading_url}/v2/clock")

    async def paper_order(self, symbol: str, side: str, qty: int = 1) -> dict:
        if side not in ("buy", "sell") or qty < 1:
            raise MarketDaysError("invalid paper order")
        if not self.settings.allow_orders:
            raise MarketDaysError("refusing to trade: ALPACA_PAPER_BASE_URL is not an Alpaca paper endpoint")
        return await self._request("POST", f"{self.settings.trading_url}/v2/orders",
                                   json={"symbol": symbol, "side": side, "type": "market",
                                         "time_in_force": "day", "qty": str(qty)})


class SimulatedMarket:
    """Stand-in quotes when no Alpaca keys are set: a smooth, repeatable random walk per symbol."""

    simulated = True

    def _base(self, symbol: str) -> float:
        h = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
        return 20 + h % 480

    def _price(self, symbol: str, t: float) -> float:
        base, seed = self._base(symbol), int(hashlib.sha256(symbol.encode()).hexdigest()[8:16], 16)
        drift = sum(math.sin(t / period + seed % (k + 7)) * amp
                    for k, (period, amp) in enumerate(((3600 * 24 * 3, 0.06), (3600 * 5, 0.02), (600, 0.006))))
        return round(base * (1 + drift), 2)

    async def quote(self, symbol: str) -> dict:
        mid = self._price(symbol, time.time())
        spread = max(0.01, round(mid * 0.0004, 2))
        return {"symbol": symbol, "bid": round(mid - spread, 2), "ask": round(mid + spread, 2), "at": _now(), "simulated": True}

    async def closes(self, symbol: str, days: int = 10) -> list[float]:
        now = time.time()
        return [self._price(symbol, now - 86400 * (days - i)) for i in range(days)]

    async def clock(self) -> dict:
        return {"is_open": True, "simulated": True}

    async def paper_order(self, symbol: str, side: str, qty: int = 1) -> dict:
        if side not in ("buy", "sell") or qty < 1:
            raise MarketDaysError("invalid paper order")
        return {"id": f"sim-{uuid.uuid4().hex[:12]}", "simulated": True}

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------------------------------- storage

class MarketDaysStore:
    """PostgreSQL storage (Railway)."""

    persistent = True

    def __init__(self, database_url: str):
        self.database_url, self.pool = _database_url(database_url), None

    async def connect(self) -> None:
        async def init(conn):  # JSONB in and out as Python objects, not strings
            await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

        try:
            self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5, init=init)
            async with self.pool.acquire() as conn:
                await conn.execute(SCHEMA)
        except (asyncpg.PostgresError, OSError) as exc:
            raise MarketDaysError("Market Days database is unavailable") from exc

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _pool(self):
        if not self.pool:
            raise MarketDaysError("Market Days is not configured")
        return self.pool

    async def create_game(self, name: str, symbols: list[str], start_cash: float = START_CASH) -> dict:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_games (id, name, symbols, start_cash) VALUES ($1, $2, $3, $4) "
                "RETURNING id, name, symbols, status, start_cash, created_at, updated_at",
                uuid.uuid4(), name, symbols, start_cash)
        return _plain(row)

    async def games(self, limit: int = 20) -> list[dict]:
        async with self._pool().acquire() as conn:
            rows = await conn.fetch("SELECT id, name, symbols, status, start_cash, created_at, updated_at "
                                    "FROM market_days_games ORDER BY created_at DESC LIMIT $1", limit)
        return [_plain(r) for r in rows]

    async def game(self, game_id: str) -> dict:
        gid = _uuid(game_id)
        async with self._pool().acquire() as conn:
            game = await conn.fetchrow("SELECT id, name, symbols, status, start_cash, created_at, updated_at "
                                       "FROM market_days_games WHERE id = $1", gid)
            if not game:
                raise MarketDaysInputError("Market Days game not found")
            events = await conn.fetch("SELECT id, kind, payload, created_at FROM market_days_events "
                                      "WHERE game_id = $1 ORDER BY id DESC LIMIT 100", gid)
        result = _plain(game)
        result["events"] = [_plain(e) for e in reversed(events)]
        return result

    async def event(self, game_id: str, kind: str, payload: dict) -> dict:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_events (game_id, kind, payload) VALUES ($1, $2, $3) "
                "RETURNING id, kind, payload, created_at", _uuid(game_id), kind, payload)
            await conn.execute("UPDATE market_days_games SET updated_at = NOW() WHERE id = $1", _uuid(game_id))
        return _plain(row)

    async def add_player(self, game_id: str, name: str, kind: str, archetype: str | None = None) -> dict:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_players (id, game_id, name, kind, archetype) VALUES ($1, $2, $3, $4, $5) "
                "RETURNING id, game_id, name, kind, archetype, created_at",
                uuid.uuid4(), _uuid(game_id), name, kind, archetype)
        return _plain(row)

    async def players(self, game_id: str) -> list[dict]:
        async with self._pool().acquire() as conn:
            rows = await conn.fetch("SELECT id, game_id, name, kind, archetype, created_at FROM market_days_players "
                                    "WHERE game_id = $1 ORDER BY created_at", _uuid(game_id))
        return [_plain(r) for r in rows]

    async def add_fill(self, game_id: str, player_id: str, symbol: str, side: str, qty: int, price: float,
                       order_id: str | None = None) -> dict:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_fills (game_id, player_id, symbol, side, qty, price, order_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id, player_id, symbol, side, qty, price, order_id, created_at",
                _uuid(game_id), _uuid(player_id), symbol, side, qty, price, order_id)
        return _plain(row)

    async def fills(self, game_id: str) -> list[dict]:
        async with self._pool().acquire() as conn:
            rows = await conn.fetch("SELECT id, player_id, symbol, side, qty, price, order_id, created_at "
                                    "FROM market_days_fills WHERE game_id = $1 ORDER BY id", _uuid(game_id))
        return [_plain(r) for r in rows]


class MemoryStore:
    """Same interface, kept in memory: for local play and tests. Nothing survives a restart."""

    persistent = False

    def __init__(self):
        self._games: dict[str, dict] = {}
        self._events: dict[str, list[dict]] = {}
        self._players: dict[str, list[dict]] = {}
        self._fills: dict[str, list[dict]] = {}
        self._seq = 0

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _get(self, game_id: str) -> dict:
        game = self._games.get(str(_uuid(game_id)))
        if not game:
            raise MarketDaysInputError("Market Days game not found")
        return game

    def _id(self) -> int:
        self._seq += 1
        return self._seq

    async def create_game(self, name: str, symbols: list[str], start_cash: float = START_CASH) -> dict:
        gid = str(uuid.uuid4())
        game = {"id": gid, "name": name, "symbols": list(symbols), "status": "open", "start_cash": start_cash,
                "created_at": _now(), "updated_at": _now()}
        self._games[gid], self._events[gid], self._players[gid], self._fills[gid] = game, [], [], []
        return dict(game)

    async def games(self, limit: int = 20) -> list[dict]:
        return [dict(g) for g in sorted(self._games.values(), key=lambda g: g["created_at"], reverse=True)[:limit]]

    async def game(self, game_id: str) -> dict:
        game = dict(self._get(game_id))
        game["events"] = [dict(e) for e in self._events[game["id"]][-100:]]
        return game

    async def event(self, game_id: str, kind: str, payload: dict) -> dict:
        game = self._get(game_id)
        row = {"id": self._id(), "kind": kind, "payload": payload, "created_at": _now()}
        self._events[game["id"]].append(row)
        game["updated_at"] = row["created_at"]
        return dict(row)

    async def add_player(self, game_id: str, name: str, kind: str, archetype: str | None = None) -> dict:
        game = self._get(game_id)
        row = {"id": str(uuid.uuid4()), "game_id": game["id"], "name": name, "kind": kind, "archetype": archetype,
               "created_at": _now()}
        self._players[game["id"]].append(row)
        return dict(row)

    async def players(self, game_id: str) -> list[dict]:
        return [dict(p) for p in self._players[self._get(game_id)["id"]]]

    async def add_fill(self, game_id: str, player_id: str, symbol: str, side: str, qty: int, price: float,
                       order_id: str | None = None) -> dict:
        game = self._get(game_id)
        row = {"id": self._id(), "player_id": str(player_id), "symbol": symbol, "side": side, "qty": qty,
               "price": price, "order_id": order_id, "created_at": _now()}
        self._fills[game["id"]].append(row)
        return dict(row)

    async def fills(self, game_id: str) -> list[dict]:
        return [dict(f) for f in self._fills[self._get(game_id)["id"]]]


# ----------------------------------------------------------------------------------------------- the ledger

def ledger(players: list[dict], fills: list[dict], start_cash: float, marks: dict[str, float]) -> list[dict]:
    """Cash, positions and equity per player, derived purely from the fill history. Sorted best first."""
    book = {p["id"]: {"cash": start_cash, "positions": {}, "cost": {}} for p in players}
    for f in fills:
        b = book.get(str(f["player_id"]))
        if b is None:
            continue
        qty, px, sym = int(f["qty"]), float(f["price"]), f["symbol"]
        held = b["positions"].get(sym, 0)
        if f["side"] == "buy":
            b["cash"] -= qty * px
            b["positions"][sym] = held + qty
            b["cost"][sym] = b["cost"].get(sym, 0.0) + qty * px
        else:
            b["cash"] += qty * px
            b["positions"][sym] = held - qty
            if held:
                b["cost"][sym] = b["cost"].get(sym, 0.0) * (held - qty) / held
    rows = []
    for p in players:
        b = book[p["id"]]
        positions = []
        for sym, qty in b["positions"].items():
            if not qty:
                continue
            mark = marks.get(sym)
            avg = b["cost"].get(sym, 0.0) / qty
            positions.append({"symbol": sym, "qty": qty, "avg_cost": round(avg, 2),
                              "mark": mark, "value": round(qty * (mark if mark is not None else avg), 2)})
        equity = b["cash"] + sum(x["value"] for x in positions)
        rows.append({**p, "cash": round(b["cash"], 2), "positions": positions, "equity": round(equity, 2),
                     "pnl_pct": round((equity / start_cash - 1) * 100, 3)})
    rows.sort(key=lambda r: -r["equity"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ----------------------------------------------------------------------------------------------- the service

class MarketDays:
    """Isolated application service; party-mode state never depends on it."""

    def __init__(self, database_url: str | None = None, alpaca: AlpacaPaper | SimulatedMarket | None = None,
                 brain=None, store=None, autopilot_minutes: float = 0.0, rng: random.Random | None = None):
        self.store = store or (MarketDaysStore(database_url) if database_url else MemoryStore())
        self.alpaca = alpaca  # None = quotes unavailable (kept for callers that check it)
        self.market = alpaca or SimulatedMarket()
        self.brain = brain
        self.enabled = True
        self.autopilot_minutes = autopilot_minutes
        self.rng = rng or random.Random()
        self.listeners: list = []  # async callbacks: (game_id, event)
        self._task: asyncio.Task | None = None
        self._news: dict[str, tuple[float, list[dict]]] = {}

    @classmethod
    def from_env(cls, brain=None) -> "MarketDays":
        settings = AlpacaSettings.from_env()
        return cls(os.getenv("DATABASE_URL"), AlpacaPaper(settings) if settings else None, brain,
                   autopilot_minutes=float(os.getenv("MARKET_DAYS_AUTOPILOT_MINUTES", "0") or 0))

    def config(self) -> dict:
        return {"enabled": self.enabled, "persistent": self.store.persistent, "paper_trading": self.alpaca is not None,
                "quotes": "simulated" if self.market.simulated else "alpaca",
                "orders_allowed": self.alpaca is None or self.alpaca.settings.allow_orders,
                "autopilot_minutes": self.autopilot_minutes, "max_order_qty": MAX_ORDER_QTY,
                "default_symbols": ["SPY", "AAPL", "NVDA"], "start_cash": START_CASH,
                "ai_desk": [{"key": k, "name": DESK_NAMES[k], "emoji": BY_KEY[k].emoji, "label": BY_KEY[k].label}
                            for k in AI_DESK]}

    async def start(self) -> None:
        await self.store.connect()
        if self.autopilot_minutes > 0:
            self._task = asyncio.get_running_loop().create_task(self._autopilot())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.market.close()
        await self.store.close()

    async def _emit(self, game_id: str, event: dict) -> None:
        for fn in list(self.listeners):
            try:
                await fn(game_id, event)
            except Exception:
                pass

    # --- games ----------------------------------------------------------------------------------------------
    async def create_game(self, name: str, symbols: list[str]) -> dict:
        symbols = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip().isalpha() and len(s.strip()) <= 5))[:8]
        if not symbols:
            raise MarketDaysInputError("choose at least one stock symbol")
        game = await self.store.create_game(name.strip()[:60] or "Market Day", symbols, START_CASH)
        for key in AI_DESK:
            await self.store.add_player(game["id"], DESK_NAMES[key], "ai", key)
        await self.store.event(game["id"], "game_created", {"name": game["name"], "symbols": symbols})
        return game

    async def games(self) -> list[dict]:
        return await self.store.games()

    async def _quotes(self, symbols: list[str]) -> list[dict]:
        results = await asyncio.gather(*(self.market.quote(s) for s in symbols), return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    async def state(self, game_id: str) -> dict:
        game = await self.store.game(game_id)
        quotes = await self._quotes(game["symbols"])
        marks = {q["symbol"]: round((q["bid"] + q["ask"]) / 2, 2) for q in quotes}
        players = await self.store.players(game_id)
        game["quotes"] = quotes
        game["leaderboard"] = ledger(players, await self.store.fills(game_id), game["start_cash"], marks)
        return game

    # --- humans ---------------------------------------------------------------------------------------------
    async def join(self, game_id: str, name: str) -> dict:
        name = " ".join(name.split())[:24]
        if not name:
            raise MarketDaysInputError("enter a name")
        await self.store.game(game_id)
        player = await self.store.add_player(game_id, name, "human")
        await self.store.event(game_id, "player_joined", {"player": name})
        await self._emit(game_id, {"kind": "player_joined", "player": name})
        return player

    async def _book(self, game_id: str, game: dict, player_id: str) -> dict:
        players = await self.store.players(game_id)
        if not any(p["id"] == str(player_id) for p in players):
            raise MarketDaysInputError("unknown player; join the game first")
        rows = ledger(players, await self.store.fills(game_id), game["start_cash"], {})
        return next(r for r in rows if r["id"] == str(player_id))

    async def trade(self, game_id: str, player_id: str, symbol: str, side: str, qty: int) -> dict:
        """A human trade: filled virtually at the real ask (buy) or bid (sell). No Alpaca order."""
        game = await self.store.game(game_id)
        symbol = symbol.upper()
        if symbol not in game["symbols"]:
            raise MarketDaysInputError("symbol is not in this game")
        if side not in ("buy", "sell") or qty < 1:
            raise MarketDaysInputError("invalid order")
        me = await self._book(game_id, game, player_id)
        q = await self.market.quote(symbol)
        price = q["ask"] if side == "buy" else q["bid"]
        held = next((p["qty"] for p in me["positions"] if p["symbol"] == symbol), 0)
        if side == "buy" and qty * price > me["cash"]:
            raise MarketDaysInputError(f"not enough cash: {qty} {symbol} costs ${qty * price:,.2f}")
        if side == "sell" and qty > held:
            raise MarketDaysInputError(f"you only hold {held} {symbol}")
        fill = await self.store.add_fill(game_id, player_id, symbol, side, qty, price)
        ev = await self.store.event(game_id, "human_trade", {"trader": me["name"], "symbol": symbol, "side": side,
                                                             "qty": qty, "price": price})
        await self._emit(game_id, ev)
        return fill

    # --- the AI desk ------------------------------------------------------------------------------------------
    async def _headlines(self, symbol: str) -> list[dict]:
        if not os.getenv("TAVILY_API_KEY"):
            return []
        at, items = self._news.get(symbol, (0.0, []))
        if time.time() - at > 600:
            from .news import tavily_headlines

            try:
                items = [{"text": t, "age": 0} for t in await tavily_headlines(f"{symbol} stock news", 2)]
            except Exception:
                items = []
            self._news[symbol] = (time.time(), items)
        return items

    async def ai_cycle(self, game_id: str, symbol: str | None = None) -> list[dict]:
        """Every AI manager looks at a stock, decides with its persona brain, and trades on the paper account."""
        if self.brain is None:
            raise MarketDaysError("no AI brain configured")
        game = await self.store.game(game_id)
        if symbol:
            symbol = symbol.upper()
            if symbol not in game["symbols"]:
                raise MarketDaysInputError("symbol is not in this game")
        players = await self.store.players(game_id)
        desk = [p for p in players if p["kind"] == "ai"]
        quotes = {q["symbol"]: q for q in await self._quotes(game["symbols"])}
        if not quotes:
            raise MarketDaysError("no quotes available right now")
        book = {r["id"]: r for r in ledger(players, await self.store.fills(game_id), game["start_cash"],
                                            {s: (q["bid"] + q["ask"]) / 2 for s, q in quotes.items()})}
        closes = {s: await self.market.closes(s) for s in quotes}
        timeline: list[dict] = []
        events = []
        for p in desk:  # one at a time so each manager can react to the ones before it
            sym = symbol if symbol in quotes else self.rng.choice(sorted(quotes))
            events.append(await self._ai_turn(game, p, book[p["id"]], sym, quotes[sym], closes[sym], timeline))
        return events

    async def _ai_turn(self, game: dict, p: dict, me: dict, sym: str, q: dict, closes: list[float],
                       timeline: list[dict]) -> dict:
        arch = BY_KEY.get(p.get("archetype") or "quant", BY_KEY["quant"])
        agent = Agent(p["id"], p["name"], p["name"].lower().replace(" ", "_").replace(".", ""), arch,
                      {sym: 1.0}, 1.0, quirk="")
        mid = (q["bid"] + q["ask"]) / 2
        held = next((x["qty"] for x in me["positions"] if x["symbol"] == sym), 0)
        hist = (closes or [mid])[-10:] + [mid]
        ctx = {
            "ticker": sym, "tickers": list(game["symbols"]), "company": sym, "price": mid,
            "listing": "\n".join(f"- ${s}: a real US-listed stock" for s in game["symbols"]),
            "change_pct": (mid / hist[-2] - 1) * 100 if len(hist) > 1 and hist[-2] else 0.0,
            "anchor": sum(hist) / len(hist), "fair": 1.0, "ref_price": sum(hist) / len(hist),
            "bid": q["bid"], "ask": q["ask"], "closes": hist,
            "cash": me["cash"], "shares": held, "holdings": {x["symbol"]: x["qty"] for x in me["positions"]},
            "pnl_pct": me["pnl_pct"], "max_buy": int(max(0.0, me["cash"]) // (q["ask"] * 1.01)), "max_sell": held,
            "news": await self._headlines(sym), "timeline": list(timeline[-3:]),
        }
        try:
            d = await self.brain.decide(agent, ctx)
        except Exception as exc:  # a flaky provider shouldn't break the whole cycle
            return await self.store.event(game["id"], "ai_error", {"trader": p["name"], "symbol": sym,
                                                                    "error": type(exc).__name__})
        qty = min(d.qty, MAX_ORDER_QTY, ctx["max_buy"] if d.action == "buy" else held)
        payload = {"trader": p["name"], "archetype": arch.key, "emoji": arch.emoji, "symbol": sym,
                   "action": d.action if qty > 0 else "hold", "qty": max(0, qty), "thought": d.thought,
                   "model": d.model, "quote": q}
        if d.action in ("buy", "sell") and qty > 0:
            try:
                order = await self.market.paper_order(sym, d.action, qty)
            except MarketDaysError as exc:
                payload.update(action="hold", qty=0, error=str(exc))
            else:
                price = q["ask"] if d.action == "buy" else q["bid"]
                await self.store.add_fill(game["id"], p["id"], sym, d.action, qty, price, order.get("id"))
                payload.update(price=price, order_id=order.get("id"), simulated=bool(order.get("simulated")))
        timeline.append({"handle": agent.handle, "ticker": sym, "text": d.thought,
                         "side": 1 if payload["action"] == "buy" else -1})
        ev = await self.store.event(game["id"], "ai_decision", payload)
        await self._emit(game["id"], ev)
        return ev

    async def _autopilot(self) -> None:
        """Run an AI cycle for every open game every few minutes while the real market is open."""
        while True:
            await asyncio.sleep(self.autopilot_minutes * 60)
            try:
                if not (await self.market.clock()).get("is_open"):
                    continue
                for g in await self.store.games():
                    if g["status"] == "open":
                        await self.ai_cycle(g["id"])
            except Exception:
                continue
