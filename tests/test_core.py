import asyncio
import random
import time

import pytest

from flashcrash.brain import MockBrain, parse_decision
from flashcrash.market import Exchange, Wallet
from flashcrash.sim import Simulation, route_news


def make_ex():
    ex = Exchange(100.0)
    ex.open_account("a", 10_000, 100)
    ex.open_account("b", 10_000, 100)
    return ex


def test_limit_orders_cross_at_resting_price():
    ex = make_ex()
    assert ex.place("a", "sell", 10, 101) == []
    trades = ex.place("b", "buy", 15, 102)
    assert [(t.price, t.qty) for t in trades] == [(101, 10)]
    assert ex.best_bid() == 102 and ex.best_ask() is None  # remainder rests
    a, b = ex.accounts["a"], ex.accounts["b"]
    assert a.shares == 90 and a.cash == 10_000 + 1010
    assert b.shares == 110 and b.reserved_cash == pytest.approx(5 * 102)


def test_no_shorting_or_overspending():
    ex = make_ex()
    ex.place("a", "sell", 500, 99)
    assert ex.accounts["a"].reserved_shares == 100
    ex.place("b", "buy", 10_000, None)  # market buy capped by cash
    b = ex.accounts["b"]
    assert b.cash >= 0 and b.shares == 200


def test_cancel_releases_reservations():
    ex = make_ex()
    ex.place("a", "buy", 10, 90)
    ex.place("a", "sell", 10, 110)
    ex.cancel_agent("a")
    a = ex.accounts["a"]
    assert a.reserved_cash == 0 and a.reserved_shares == 0 and not ex.orders


def test_conservation_of_cash_and_shares():
    ex = Exchange(100.0)
    rng = random.Random(1)
    for i in range(20):
        ex.open_account(str(i), 50_000, 200)
    cash0 = sum(a.cash for a in ex.accounts.values())
    shares0 = sum(a.shares for a in ex.accounts.values())
    for _ in range(3000):
        side = rng.choice(["buy", "sell"])
        price = None if rng.random() < 0.2 else round(100 * rng.uniform(0.95, 1.05), 2)
        ex.place(str(rng.randrange(20)), side, rng.randint(1, 50), price)
    assert sum(a.cash for a in ex.accounts.values()) == pytest.approx(cash0)
    assert sum(a.shares for a in ex.accounts.values()) == shares0
    for a in ex.accounts.values():
        assert a.cash >= -1e-6 and a.shares >= 0 and a.free_shares >= 0 and a.free_cash >= -1e-6


@pytest.mark.parametrize("text,action,qty,price", [
    ('{"action":"buy","qty":10,"price":101.5,"thought":"lfg"}', "buy", 10, 101.5),
    ('<think>hmm</think>```json\n{"action":"SELL","qty":"5","price":null,"thought":"out"}\n```', "sell", 5, None),
    ('Sure! {"action":"hold","qty":0,"price":"market","thought":"wait"} {"extra":1}', "hold", 0, None),
    ('{"action":"yolo","qty":-3}', "hold", 0, None),
])
def test_parse_decision(text, action, qty, price):
    d = parse_decision(text)
    assert (d.action, d.qty, d.price) == (action, qty, price)


def test_parse_decision_rejects_prose():
    with pytest.raises(ValueError):
        parse_decision("I think I will buy some")


def test_wallet_is_shared_across_exchanges():
    gpu, meme = Exchange(100.0), Exchange(8.0)
    w = Wallet(1_000)
    for ex in (gpu, meme):
        ex.open_account("me", 0, 0, w)
        ex.open_account("mm", 1_000_000, 10_000)
    gpu.place("mm", "sell", 100, 100)
    meme.place("mm", "sell", 1000, 8)
    gpu.place("me", "buy", 8, None)  # $800 of GPU
    meme.place("me", "buy", 1000, None)  # only $200 left -> 25 MEME
    assert gpu.accounts["me"].shares == 8 and meme.accounts["me"].shares == 25
    assert w.cash == pytest.approx(0)


@pytest.mark.parametrize("text,focus,expected", [
    ("GPU CEO arrested at the airport", None, ["GPU"]),
    ("Doge Burger adds golden fries", "GPU", ["MEME"]),
    ("Robotaxi crashes into a lake", None, ["ROBO"]),
    ("CEO resigns in disgrace", "NOVA", ["NOVA"]),
    ("Fed announces emergency rate cut", None, ["GPU", "ROBO", "NOVA", "MEME"]),
])
def test_news_routing(text, focus, expected):
    assert route_news(text, focus) == expected


def test_mock_chatter_is_varied():
    async def run():
        sim = Simulation(MockBrain(random.Random(1)), n_agents=60, workers=12, seed=1)
        sim.start()
        await sim.add_news("GPU smashes earnings, record profit", "crowd", "test")
        await asyncio.sleep(3)
        await sim.stop()
        return [f.thought for f in sim.world.feed if f.action != "hold"]

    posts = asyncio.run(run())
    assert len(posts) > 20
    assert len(set(posts)) / len(posts) > 0.9  # almost nothing repeats


def test_simulation_runs_and_reacts():
    async def run():
        sim = Simulation(MockBrain(random.Random(0)), n_agents=60, workers=12, seed=0)
        sim.start()
        await sim.add_news("GPU CEO arrested over fraud probe", "crowd", "test")
        p = sim.join("Neil")
        await sim.human_trade(p.id, "buy", 10, "MEME")
        await asyncio.sleep(3)
        snap = sim.snapshot()
        await sim.stop()
        return sim, snap, p

    sim, snap, p = asyncio.run(run())
    assert len(sim.world.feed) > 10
    assert {f.ticker for f in sim.world.feed} > {"GPU"}  # agents trade more than one stock
    assert snap["leaderboard"] and any(r["key"] == "human" for r in snap["leaderboard"])
    pos = {x["ticker"]: x for x in sim.portfolio(p.id)["positions"]}
    assert pos["MEME"]["shares"] == 250 + 10 and pos["MEME"]["avg_cost"] is not None
    sim.reset()
    assert sim.player(p.id) is not None and sim.world.markets["MEME"].exchange.accounts[p.id].shares == 250


def test_round_ends_and_blocks_trading():
    async def run():
        sim = Simulation(MockBrain(random.Random(2)), n_agents=30, workers=4, seed=2)
        events = []

        async def listen(e):
            events.append(e["type"])
        sim.listeners.append(listen)
        sim.start()
        p = sim.join("Ann")
        await sim.start_round(1)
        sim.round_end = time.time() + 0.6
        await asyncio.sleep(1.5)
        with pytest.raises(ValueError):
            await sim.human_trade(p.id, "buy", 1)
        await sim.stop()
        return events

    events = asyncio.run(run())
    assert "round_over" in events


def test_ai_sec_fines_insider_trading():
    async def run():
        sim = Simulation(MockBrain(random.Random(3)), n_agents=60, workers=12, seed=3)
        sim.sec_delay, sim.sec_catch = 3.0, 1.0
        events = []

        async def listen(e):
            if e["type"] == "sec":
                events.append(e["item"])
        sim.listeners.append(listen)
        sim.start()
        crook, honest = sim.join("Crook"), sim.join("Honest")
        await sim.human_trade(crook.id, "buy", "all", "MEME")  # buy, then pump it
        await sim.add_news("Doge Burger smashes earnings, record profit", "crowd", "Crook", None, crook.id)
        await sim.add_news("Doge Burger wins giant mega contract", "crowd", "Honest", None, honest.id)  # no trade first
        await asyncio.sleep(4)
        await sim.stop()
        return sim, events, crook

    sim, events, crook = asyncio.run(run())
    if sim.world.markets["MEME"].change_pct > 0.5:  # the pump worked, so the SEC must rule
        assert [e["player"] for e in events] == ["Crook"]
        assert events[0]["fine"] >= 250 and sim.world.sec_cases
    assert all(e["player"] != "Honest" for e in events)


def test_forensics_names_the_headline_and_drivers():
    async def run():
        sim = Simulation(MockBrain(random.Random(4)), n_agents=120, workers=16, seed=4)
        sim.start()
        await sim.add_news("RoboRide robotaxi drives itself into a lake, recall feared", "crowd", "t")
        await asyncio.sleep(5)
        d = await sim.explain("ROBO", 30)
        res = sim.results()
        recap = await sim.recap(res)
        await sim.stop()
        return d, recap

    d, recap = asyncio.run(run())
    assert d["ticker"] == "ROBO" and "lake" in d["headline"]
    assert d["drivers"] and d["summary"].startswith("$ROBO")
    assert "$" in recap


def test_serial_leakers_lose_credibility():
    async def run():
        sim = Simulation(MockBrain(random.Random(5)), n_agents=80, workers=12, seed=5)
        sim.start()
        p = sim.join("Spammer")
        items = [await sim.add_news(f"Doge Burger record profit #{i}", "crowd", "Spammer", None, p.id) for i in range(4)]
        await asyncio.sleep(3)
        await sim.stop()
        return sim, p, items

    sim, p, items = asyncio.run(run())
    creds = [n.cred for n in items]
    assert creds[0] == 1.0 and creds == sorted(creds, reverse=True) and creds[-1] < 0.2
    assert sim.portfolio(p.id)["cred"] < 0.15


def test_ais_call_out_serial_leakers():
    from flashcrash.brain import sentiment
    from flashcrash.chatter import Chatter
    from flashcrash.personas import ARCHETYPES, Agent

    agent = Agent("a1", "Ava", "ava", ARCHETYPES[1], {}, 1.0, quirk="")
    ctx = {"ticker": "MEME", "company": "Doge Burger", "price": 9.0, "change_pct": 12.0, "timeline": [],
           "news": [{"text": "Doge Burger record profit", "age": 5, "author": "Spammer", "cred": 0.3}]}
    chat = Chatter(random.Random(0))
    sells = [chat.post(agent, "sell", ctx, sentiment) for _ in range(30)]
    buys = [chat.post(agent, "buy", ctx, sentiment) for _ in range(30)]
    assert sum("Spammer" in p for p in sells) > 10  # sellers scoff at the fake good news
    assert not any("Spammer" in p for p in buys)  # buyers don't diss the news they're trading on


def test_circuit_breaker_halts_a_runaway_stock():
    from flashcrash.sim import Candle

    async def run():
        sim = Simulation(MockBrain(random.Random(6)), n_agents=20, workers=2, seed=6)
        events = []

        async def listen(e):
            events.append(e)
        sim.listeners.append(listen)
        m = sim.world.markets["MEME"]
        now = int(time.time())
        m.candles = [Candle(now - 20 + i, 8, 8, 8, 8) for i in range(10)]
        m.current.close = 9.5  # +19% in 20 seconds
        await sim._circuit_breaker(m, time.time())
        p = sim.join("Ann")
        with pytest.raises(ValueError):
            await sim.human_trade(p.id, "buy", 10, "MEME")
        await sim.human_trade(p.id, "buy", 1, "GPU")  # other stocks still trade
        return sim, events

    sim, events = asyncio.run(run())
    assert [e["ticker"] for e in events if e["type"] == "halt"] == ["MEME"]
    assert sim.world.markets["MEME"].halted and not sim.world.markets["MEME"].exchange.orders
