import asyncio
import random

import pytest

from flashcrash.brain import MockBrain, parse_decision
from flashcrash.market import Exchange
from flashcrash.sim import Simulation


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


def test_simulation_runs_and_reacts():
    async def run():
        sim = Simulation(MockBrain(random.Random(0)), n_agents=60, workers=12, seed=0)
        sim.start()
        await sim.add_news("GPU CEO arrested over fraud probe", "crowd", "test")
        p = sim.join("Neil")
        await sim.human_trade(p.id, "buy", 10)
        await asyncio.sleep(3)
        snap = sim.snapshot()
        await sim.stop()
        return sim, snap, p

    sim, snap, p = asyncio.run(run())
    assert len(sim.world.feed) > 10
    assert snap["leaderboard"] and any(r["key"] == "human" for r in snap["leaderboard"])
    assert sim.portfolio(p.id)["shares"] >= 50
    sim.reset()
    assert sim.player(p.id) is not None and sim.world.exchange.accounts[p.id].shares == 50
