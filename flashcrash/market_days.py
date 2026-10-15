"""Persistent Market Days mode: PostgreSQL state plus Alpaca paper-market integration."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass

import asyncpg
import httpx


class MarketDaysError(RuntimeError):
    """A safe error suitable for returning from the Market Days API."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_days_games (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    symbols JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS market_days_events (
    id BIGSERIAL PRIMARY KEY,
    game_id UUID NOT NULL REFERENCES market_days_games(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS market_days_events_game_id_id ON market_days_events(game_id, id DESC);
"""


def _database_url(url: str) -> str:
    return "postgresql://" + url.removeprefix("postgres://") if url.startswith("postgres://") else url


@dataclass(frozen=True)
class AlpacaSettings:
    key: str
    secret: str
    trading_url: str
    data_url: str

    @classmethod
    def from_env(cls) -> "AlpacaSettings | None":
        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
        if not key or not secret:
            return None
        return cls(key, secret, os.getenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets"),
                   os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"))


class AlpacaPaper:
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

    async def paper_order(self, symbol: str, side: str, qty: int = 1) -> dict:
        if side not in ("buy", "sell") or qty < 1:
            raise MarketDaysError("invalid paper order")
        return await self._request("POST", f"{self.settings.trading_url}/v2/orders",
                                   json={"symbol": symbol, "side": side, "type": "market",
                                         "time_in_force": "day", "qty": str(qty)})


class MarketDaysStore:
    def __init__(self, database_url: str):
        self.database_url, self.pool = _database_url(database_url), None

    async def connect(self) -> None:
        try:
            self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
            async with self.pool.acquire() as conn:
                await conn.execute(SCHEMA)
        except asyncpg.PostgresError as exc:
            raise MarketDaysError("Market Days database is unavailable") from exc

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _pool(self):
        if not self.pool:
            raise MarketDaysError("Market Days is not configured")
        return self.pool

    async def create_game(self, name: str, symbols: list[str]) -> dict:
        game_id = uuid.uuid4()
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_games (id, name, symbols) VALUES ($1, $2, $3::jsonb) "
                "RETURNING id, name, symbols, status, created_at, updated_at",
                game_id, name, json.dumps(symbols))
        return dict(row)

    async def game(self, game_id: str) -> dict:
        async with self._pool().acquire() as conn:
            game = await conn.fetchrow("SELECT id, name, symbols, status, created_at, updated_at "
                                       "FROM market_days_games WHERE id = $1", uuid.UUID(game_id))
            if not game:
                raise MarketDaysError("Market Days game not found")
            events = await conn.fetch("SELECT id, kind, payload, created_at FROM market_days_events "
                                      "WHERE game_id = $1 ORDER BY id DESC LIMIT 100", uuid.UUID(game_id))
        result = dict(game)
        result["events"] = [dict(event) for event in reversed(events)]
        return result

    async def event(self, game_id: str, kind: str, payload: dict) -> dict:
        async with self._pool().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO market_days_events (game_id, kind, payload) VALUES ($1, $2, $3::jsonb) "
                "RETURNING id, kind, payload, created_at", uuid.UUID(game_id), kind, json.dumps(payload))
        return dict(row)


class MarketDays:
    """Isolated application service; party-mode state never depends on it."""

    def __init__(self, database_url: str | None = None, alpaca: AlpacaPaper | None = None):
        self.store = MarketDaysStore(database_url) if database_url else None
        self.alpaca = alpaca
        self.enabled = self.store is not None

    @classmethod
    def from_env(cls) -> "MarketDays":
        settings = AlpacaSettings.from_env()
        return cls(os.getenv("DATABASE_URL"), AlpacaPaper(settings) if settings else None)

    async def start(self) -> None:
        if self.store:
            await self.store.connect()

    async def stop(self) -> None:
        if self.alpaca:
            await self.alpaca.close()
        if self.store:
            await self.store.close()

    def _store(self) -> MarketDaysStore:
        if not self.store:
            raise MarketDaysError("Set DATABASE_URL to enable Market Days")
        return self.store

    async def create_game(self, name: str, symbols: list[str]) -> dict:
        symbols = [symbol.upper() for symbol in symbols if symbol.isalpha()][:8]
        if not symbols:
            raise MarketDaysError("choose at least one stock symbol")
        return await self._store().create_game(name.strip()[:60] or "Market Day", symbols)

    async def state(self, game_id: str) -> dict:
        game = await self._store().game(game_id)
        if self.alpaca:
            quotes = await asyncio_gather_quotes(self.alpaca, game["symbols"])
            game["quotes"] = quotes
        else:
            game["quotes"] = []
        return game

    async def ai_cycle(self, game_id: str, symbol: str) -> dict:
        if not self.alpaca:
            raise MarketDaysError("Set ALPACA_API_KEY and ALPACA_API_SECRET to run paper AI trades")
        game = await self._store().game(game_id)
        symbol = symbol.upper()
        if symbol not in game["symbols"]:
            raise MarketDaysError("symbol is not in this game")
        quote = await self.alpaca.quote(symbol)
        order = await self.alpaca.paper_order(symbol, "buy")
        return await self._store().event(game_id, "ai_paper_order",
                                         {"trader": "Momentum bot", "symbol": symbol, "side": "buy",
                                          "qty": 1, "quote": quote, "order_id": order.get("id")})


async def asyncio_gather_quotes(alpaca: AlpacaPaper, symbols: list[str]) -> list[dict]:
    results = await asyncio.gather(*(alpaca.quote(symbol) for symbol in symbols), return_exceptions=True)
    return [result for result in results if isinstance(result, dict)]
