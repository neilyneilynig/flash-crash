import asyncio

import httpx
import pytest

from flashcrash.market_days import AlpacaPaper, AlpacaSettings, MarketDaysError


def test_alpaca_paper_quote_and_order():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/quotes/latest"):
            return httpx.Response(200, json={"quote": {"bp": 100.1, "ap": 100.2, "t": "now"}})
        return httpx.Response(200, json={"id": "paper-order"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        alpaca = AlpacaPaper(AlpacaSettings("key", "secret", "https://trade", "https://data"), client)
        return await alpaca.quote("SPY"), await alpaca.paper_order("SPY", "buy")

    quote, order = asyncio.run(run())
    assert quote == {"symbol": "SPY", "bid": 100.1, "ask": 100.2, "at": "now"}
    assert order["id"] == "paper-order"
    assert requests[0].headers["APCA-API-KEY-ID"] == "key"


def test_alpaca_paper_rejects_invalid_order():
    alpaca = AlpacaPaper(AlpacaSettings("key", "secret", "https://trade", "https://data"),
                         httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))))
    with pytest.raises(MarketDaysError, match="invalid"):
        asyncio.run(alpaca.paper_order("SPY", "hold"))


# --- the Market Days service: AI desk, human players, ledger (memory store; Postgres when TEST_DATABASE_URL is set) ---

import os
import random

from flashcrash.brain import Decision, MockBrain
from flashcrash.market_days import (AlpacaSettings, MarketDaysAuthError, MarketDays, MarketDaysInputError, MarketDaysStore, MemoryStore,
                                    SimulatedMarket, ledger)


def _stores():
    stores = [("memory", lambda: MemoryStore())]
    if os.getenv("TEST_DATABASE_URL"):
        stores.append(("postgres", lambda: MarketDaysStore(os.environ["TEST_DATABASE_URL"])))
    return stores


@pytest.fixture(params=_stores(), ids=lambda s: s[0])
def days(request):
    return MarketDays(store=request.param[1](), brain=MockBrain(random.Random(1)), rng=random.Random(1))


def test_game_has_an_ai_desk_and_humans_trade_at_real_quotes(days):
    async def run():
        await days.start()
        try:
            g = await days.create_game("Friday", ["spy", "NVDA", "spy", "bad!"])
            me = await days.join(g["id"], "Neil")
            q = await days.market.quote("NVDA")
            fill = await days.trade(g["id"], me["id"], "nvda", "buy", 10, me["token"])
            with pytest.raises(MarketDaysInputError):
                await days.trade(g["id"], me["id"], "NVDA", "sell", 11, me["token"])  # can't sell what you don't own
            with pytest.raises(MarketDaysInputError):
                await days.trade(g["id"], me["id"], "TSLA", "buy", 1, me["token"])  # not in this game
            return g, me, q, fill, await days.state(g["id"])
        finally:
            await days.stop()

    g, me, q, fill, state = asyncio.run(run())
    assert g["symbols"] == ["SPY", "NVDA"]
    assert len(state["history"]["NVDA"]) == 10
    assert fill["price"] == q["ask"] or abs(fill["price"] - q["ask"]) < 5  # bought at the ask
    board = {r["name"]: r for r in state["leaderboard"]}
    assert {"Chad the Degen", "Warren Jr.", "Q-Bot 9000", "Moby Capital", "Neil"} == set(board)
    neil = board["Neil"]
    assert neil["positions"][0]["symbol"] == "NVDA" and neil["positions"][0]["qty"] == 10
    assert neil["cash"] == pytest.approx(100_000 - 10 * fill["price"])
    assert [e["kind"] for e in state["events"]][:2] == ["game_created", "player_joined"]


def test_ai_cycle_decides_trades_and_logs_thoughts(days):
    class Bull:  # deterministic brain: everyone buys 1000 (capped to MAX_ORDER_QTY)
        async def decide(self, agent, ctx):
            assert ctx["listing"].startswith("- $") and ctx["bid"] < ctx["ask"]
            return Decision("buy", 1000, None, f"{agent.name} likes {ctx['ticker']}", "test-model")

    days.brain = Bull()

    async def run():
        await days.start()
        try:
            g = await days.create_game("Desk", ["AAPL"])
            events = await days.ai_cycle(g["id"])
            return events, await days.state(g["id"])
        finally:
            await days.stop()

    events, state = asyncio.run(run())
    assert len(events) == 4 and all(e["kind"] == "ai_decision" for e in events)
    p = events[0]["payload"]
    assert p["action"] == "buy" and p["qty"] == 25 and p["order_id"].startswith("sim-") and "likes AAPL" in p["thought"]
    ai = [r for r in state["leaderboard"] if r["kind"] == "ai"]
    assert all(r["positions"][0]["qty"] == 25 for r in ai)


def test_mock_brain_runs_the_desk_on_real_symbols():
    days = MarketDays(store=MemoryStore(), brain=MockBrain(random.Random(2)), rng=random.Random(2))

    async def run():
        await days.start()
        g = await days.create_game("Mock", ["SPY", "QQQ"])
        return await days.ai_cycle(g["id"], "QQQ")

    events = asyncio.run(run())
    assert all(e["payload"]["symbol"] == "QQQ" and e["payload"]["thought"] for e in events)


def test_ledger_math():
    players = [{"id": "a", "name": "A", "kind": "human"}]
    fills = [{"player_id": "a", "symbol": "X", "side": "buy", "qty": 10, "price": 10.0},
             {"player_id": "a", "symbol": "X", "side": "buy", "qty": 10, "price": 20.0},
             {"player_id": "a", "symbol": "X", "side": "sell", "qty": 5, "price": 30.0}]
    row = ledger(players, fills, 1000.0, {"X": 25.0})[0]
    assert row["cash"] == 1000 - 100 - 200 + 150
    assert row["positions"] == [{"symbol": "X", "qty": 15, "avg_cost": 15.0, "mark": 25.0, "value": 375.0}]
    assert row["equity"] == 850 + 375


def test_live_trading_endpoint_is_refused(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://api.alpaca.markets")  # live money: never
    settings = AlpacaSettings.from_env()
    alpaca = AlpacaPaper(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))))
    with pytest.raises(MarketDaysError, match="paper"):
        asyncio.run(alpaca.paper_order("SPY", "buy"))
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
    assert AlpacaSettings.from_env().allow_orders


def test_simulated_market_is_stable_and_sane():
    m = SimulatedMarket()
    q1, q2 = asyncio.run(m.quote("SPY")), asyncio.run(m.quote("SPY"))
    assert 0 < q1["bid"] < q1["ask"] and abs(q1["bid"] - q2["bid"]) < 1
    assert len(asyncio.run(m.closes("SPY"))) == 10


def test_vercel_page_matches_backend_page():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / "market-days/index.html").read_text() == (root / "flashcrash/static/market_days.html").read_text(), \
        "copy flashcrash/static/market_days.html to market-days/index.html"


def test_players_cannot_trade_as_someone_else(days):
    async def run():
        await days.start()
        try:
            g = await days.create_game("Auth", ["SPY"])
            alice, bob = await days.join(g["id"], "Alice"), await days.join(g["id"], "Bob")
            assert "token" not in str(await days.state(g["id"]))  # tokens are never published
            with pytest.raises(MarketDaysAuthError):
                await days.trade(g["id"], alice["id"], "SPY", "buy", 1, bob["token"])
            with pytest.raises(MarketDaysAuthError):
                await days.trade(g["id"], alice["id"], "SPY", "buy", 1)
            return await days.trade(g["id"], alice["id"], "SPY", "buy", 1, alice["token"])
        finally:
            await days.stop()

    assert asyncio.run(run())["qty"] == 1


def test_simultaneous_buys_cannot_overspend(days):
    async def run():
        await days.start()
        try:
            g = await days.create_game("Race", ["SPY"])
            me = await days.join(g["id"], "Racer")
            q = await days.market.quote("SPY")
            n = int(60_000 // q["ask"])  # each order alone is affordable, two together are not
            results = await asyncio.gather(*(days.trade(g["id"], me["id"], "SPY", "buy", n, me["token"]) for _ in range(5)),
                                           return_exceptions=True)
            return results, await days.state(g["id"])
        finally:
            await days.stop()

    results, state = asyncio.run(run())
    assert sum(isinstance(r, dict) for r in results) == 1
    racer = next(r for r in state["leaderboard"] if r["name"] == "Racer")
    assert racer["cash"] >= 0


# --- charts: indicators and the plain-English signal ---

from flashcrash import ta
from flashcrash.market_days import MarketDays


def _bars(closes):
    return [{"t": 1_700_000_000 + i * 86400, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1000.0} for i, c in enumerate(closes)]


def test_indicator_math():
    assert ta.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    up = list(range(1, 40))
    assert ta.rsi(up)[-1] == 100.0  # never fell
    assert ta.rsi(list(reversed(up)))[-1] < 1
    upper, lower = ta.bollinger([10.0] * 25)
    assert upper[-1] == lower[-1] == 10.0  # no volatility, no band width


def test_signal_reads_crashes_and_rallies():
    crash = [100 * 0.98 ** i for i in range(80)]
    rally = [100 * 1.02 ** i for i in range(80)]
    assert any("oversold" in r["text"] for r in ta.signal(_bars(crash))["reasons"])
    assert any("overbought" in r["text"] for r in ta.signal(_bars(rally))["reasons"])
    assert ta.signal(_bars([100.0] * 5))["label"] == "NOT ENOUGH DATA"
    for closes in (crash, rally):
        s = ta.signal(_bars(closes))
        assert -100 <= s["score"] <= 100 and s["reasons"]


def test_chart_trims_warmup_and_validates():
    md = MarketDays()
    c = asyncio.run(md.chart("nvda", "3M"))
    assert c["symbol"] == "NVDA" and len(c["bars"]) == 63
    assert len(c["sma50"]) == 63  # warm-up bars mean the long average covers the whole window
    assert c["sma50"][0]["t"] == c["bars"][0]["t"]
    assert c["signal"]["label"] in {"STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"}
    assert asyncio.run(md.chart("NVDA", "3M")) is c  # cached
    for bad in (("NV-DA", "3M"), ("NVDA", "10Y")):
        with pytest.raises(MarketDaysError):
            asyncio.run(md.chart(*bad))


def test_alpaca_bars_use_iex_and_a_start_date():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"bars": [{"t": "2026-10-14T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}]})

    async def run():
        alpaca = AlpacaPaper(AlpacaSettings("k", "s", "https://trade", "https://data"),
                             httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return await alpaca.bars("SPY", "1D")

    bars = asyncio.run(run())
    from datetime import datetime, timezone
    t = int(datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc).timestamp())
    assert bars == [{"t": t, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 9.0}]
    q = seen[0].url.params
    assert q["timeframe"] == "5Min" and q["feed"] == "iex" and q["start"].endswith("Z")
