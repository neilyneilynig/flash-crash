"""A small price-time-priority limit order book with cash/share accounting.

No shorting, no margin: buys are capped by free cash, sells by free shares.
Resting orders reserve the cash/shares they could consume.
"""

from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass, field


@dataclass
class Account:
    cash: float
    shares: int
    reserved_cash: float = 0.0
    reserved_shares: int = 0
    start_equity: float = 0.0

    @property
    def free_cash(self) -> float:
        return self.cash - self.reserved_cash

    @property
    def free_shares(self) -> int:
        return self.shares - self.reserved_shares

    def equity(self, price: float) -> float:
        return self.cash + self.shares * price


@dataclass
class Order:
    id: int
    agent_id: str
    side: str  # "buy" | "sell"
    price: float
    remaining: int
    created: float


@dataclass
class Trade:
    price: float
    qty: int
    buyer: str
    seller: str
    aggressor: str  # side of the incoming order
    ts: float = field(default_factory=time.time)


class Exchange:
    def __init__(self, initial_price: float):
        self.last_price = initial_price
        self.accounts: dict[str, Account] = {}
        self.orders: dict[int, Order] = {}  # live resting orders
        self._bids: list[tuple[float, int, Order]] = []  # (-price, seq, order)
        self._asks: list[tuple[float, int, Order]] = []  # (price, seq, order)
        self._ids = itertools.count(1)
        self.trade_listeners: list = []

    # --- accounts -------------------------------------------------------
    def open_account(self, agent_id: str, cash: float, shares: int) -> Account:
        acct = Account(cash=cash, shares=shares)
        acct.start_equity = acct.equity(self.last_price)
        self.accounts[agent_id] = acct
        return acct

    # --- book views -----------------------------------------------------
    def _clean(self, book):
        while book and book[0][2].id not in self.orders:
            heapq.heappop(book)

    def best_bid(self) -> float | None:
        self._clean(self._bids)
        return self._bids[0][2].price if self._bids else None

    def best_ask(self) -> float | None:
        self._clean(self._asks)
        return self._asks[0][2].price if self._asks else None

    def depth(self, levels: int = 10) -> dict:
        def agg(side):
            out: dict[float, int] = {}
            for o in self.orders.values():
                if o.side == side:
                    out[o.price] = out.get(o.price, 0) + o.remaining
            keys = sorted(out, reverse=(side == "buy"))[:levels]
            return [[k, out[k]] for k in keys]

        return {"bids": agg("buy"), "asks": agg("sell")}

    # --- order lifecycle ------------------------------------------------
    def cancel(self, order_id: int) -> None:
        o = self.orders.pop(order_id, None)
        if o is None:
            return
        acct = self.accounts[o.agent_id]
        if o.side == "buy":
            acct.reserved_cash -= o.price * o.remaining
        else:
            acct.reserved_shares -= o.remaining

    def cancel_agent(self, agent_id: str) -> None:
        for oid in [o.id for o in self.orders.values() if o.agent_id == agent_id]:
            self.cancel(oid)

    def expire(self, older_than: float) -> None:
        for oid in [o.id for o in self.orders.values() if o.created < older_than]:
            self.cancel(oid)

    def place(self, agent_id: str, side: str, qty: int, price: float | None = None) -> list[Trade]:
        """Submit an order. price=None means market order (unfilled remainder is dropped)."""
        acct = self.accounts[agent_id]
        qty = int(qty)
        if price is not None:
            price = round(max(0.01, float(price)), 2)
        if side == "sell":
            qty = min(qty, acct.free_shares)
        if qty <= 0:
            return []

        trades: list[Trade] = []
        book = self._asks if side == "buy" else self._bids
        while qty > 0:
            self._clean(book)
            if not book:
                break
            top = book[0][2]
            if price is not None and (
                (side == "buy" and top.price > price) or (side == "sell" and top.price < price)
            ):
                break
            if top.agent_id == agent_id:  # no self-trading: pull our own resting order
                self.cancel(top.id)
                continue
            fill = min(qty, top.remaining)
            if side == "buy":
                fill = min(fill, int(acct.free_cash // top.price))
                if fill <= 0:
                    break
            buyer, seller = (agent_id, top.agent_id) if side == "buy" else (top.agent_id, agent_id)
            trades.append(self._execute(buyer, seller, top.price, fill, top, side))
            qty -= fill

        if qty > 0 and price is not None:
            if side == "buy":
                qty = min(qty, int(acct.free_cash // price))
            if qty > 0:
                o = Order(next(self._ids), agent_id, side, price, qty, time.time())
                self.orders[o.id] = o
                if side == "buy":
                    acct.reserved_cash += price * qty
                    heapq.heappush(self._bids, (-price, o.id, o))
                else:
                    acct.reserved_shares += qty
                    heapq.heappush(self._asks, (price, o.id, o))
        return trades

    def _execute(self, buyer: str, seller: str, price: float, qty: int, resting: Order, aggressor: str) -> Trade:
        b, s = self.accounts[buyer], self.accounts[seller]
        notional = price * qty
        b.cash -= notional
        b.shares += qty
        s.cash += notional
        s.shares -= qty
        if resting.side == "buy":
            b.reserved_cash -= notional
        else:
            s.reserved_shares -= qty
        resting.remaining -= qty
        if resting.remaining == 0:
            self.orders.pop(resting.id, None)
        self.last_price = price
        t = Trade(price, qty, buyer, seller, aggressor)
        for fn in self.trade_listeners:
            fn(t)
        return t
