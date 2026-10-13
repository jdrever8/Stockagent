"""Limit order book with price-time priority and reserved funds.

Differences from the original repo, all deliberate:
* A buy at or above the best ask fills (the original required exactly equal prices).
* Trades execute at the resting order's price.
* Cash and shares are reserved when an order rests, so a fill can never fail.
* Fees follow paper 4.1.2 and are charged to both sides.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config


def transaction_fee(shares: int) -> float:
    return min(config.FEE_MAX, max(config.FEE_MIN, shares * config.FEE_PER_SHARE))


@dataclass
class Order:
    agent_id: int
    side: str           # "buy" | "sell"
    stock: str
    qty: int
    price: float
    seq: int = 0
    reserved_cash: float = 0.0


@dataclass
class Trade:
    day: int
    session: int
    stock: str
    buyer: int
    seller: int
    qty: int
    price: float


@dataclass
class OrderBook:
    stock: str
    bids: List[Order] = field(default_factory=list)  # highest price first, then oldest
    asks: List[Order] = field(default_factory=list)  # lowest price first, then oldest

    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    def summary(self, depth: int = 5) -> Dict[str, list]:
        return {
            "bids": [[o.price, o.qty] for o in self.bids[:depth]],
            "asks": [[o.price, o.qty] for o in self.asks[:depth]],
        }

    def _rest(self, order: Order) -> None:
        if order.side == "buy":
            self.bids.append(order)
            self.bids.sort(key=lambda o: (-o.price, o.seq))
        else:
            self.asks.append(order)
            self.asks.sort(key=lambda o: (o.price, o.seq))


class Market:
    def __init__(self, stocks: List[str], initial_prices: Dict[str, float]):
        self.stocks = list(stocks)
        self.prices: Dict[str, float] = dict(initial_prices)
        self.books: Dict[str, OrderBook] = {s: OrderBook(s) for s in stocks}
        self.session_trades: Dict[str, List[Trade]] = {s: [] for s in stocks}
        self.trades: List[Trade] = []
        self.price_history: List[dict] = []
        self._seq = itertools.count()

    # ------------------------------------------------------------------
    def submit(self, order: Order, accounts: Dict[int, "Account"], day: int, session: int) -> List[Trade]:
        """Match an incoming order, rest any remainder. Funds must already be checked."""
        book = self.books[order.stock]
        order.seq = next(self._seq)
        acct = accounts[order.agent_id]
        if order.side == "buy":
            order.reserved_cash = order.qty * order.price + config.FEE_MAX
            acct.reserve_cash(order.reserved_cash)
        else:
            acct.reserve_shares(order.stock, order.qty)

        trades: List[Trade] = []
        opposite = book.asks if order.side == "buy" else book.bids
        while order.qty > 0 and opposite:
            best = opposite[0]
            crosses = best.price <= order.price if order.side == "buy" else best.price >= order.price
            if not crosses:
                break
            if best.agent_id == order.agent_id:
                # Self-trade prevention: cancel the older resting order.
                self._cancel(best, accounts)
                continue
            qty = min(order.qty, best.qty)
            buy, sell = (order, best) if order.side == "buy" else (best, order)
            trade = Trade(day, session, order.stock, buy.agent_id, sell.agent_id, qty, best.price)
            self._settle(trade, buy, sell, accounts)
            trades.append(trade)
            order.qty -= qty
            best.qty -= qty
            if best.qty == 0:
                opposite.pop(0)
                self._release_leftover(best, accounts)

        if order.qty > 0:
            book._rest(order)
        else:
            self._release_leftover(order, accounts)
        self.session_trades[order.stock].extend(trades)
        self.trades.extend(trades)
        return trades

    def _settle(self, t: Trade, buy: Order, sell: Order, accounts) -> None:
        notional = t.qty * t.price
        fee = transaction_fee(t.qty)
        b, s = accounts[buy.agent_id], accounts[sell.agent_id]
        # Release the slice of the buyer's reservation that this fill used (at the
        # limit price); the FEE_MAX buffer stays reserved until the order finishes.
        used = t.qty * buy.price
        buy.reserved_cash -= used
        b.release_cash(used)
        b.cash -= notional + fee
        b.holdings[t.stock] = b.holdings.get(t.stock, 0) + t.qty
        b.fees_paid += fee
        s.release_shares(t.stock, t.qty)
        s.holdings[t.stock] -= t.qty
        s.cash += notional - fee
        s.fees_paid += fee

    def _release_leftover(self, order: Order, accounts) -> None:
        acct = accounts[order.agent_id]
        if order.side == "buy":
            acct.release_cash(order.reserved_cash)
            order.reserved_cash = 0.0
        elif order.qty > 0:
            acct.release_shares(order.stock, order.qty)

    def _cancel(self, order: Order, accounts) -> None:
        book = self.books[order.stock]
        side = book.bids if order.side == "buy" else book.asks
        side.remove(order)
        self._release_leftover(order, accounts)

    def cancel_agent_orders(self, agent_id: int, accounts) -> None:
        for book in self.books.values():
            for order in [o for o in book.bids + book.asks if o.agent_id == agent_id]:
                self._cancel(order, accounts)

    def clear_books(self, accounts) -> None:
        """Day orders expire at the close."""
        for book in self.books.values():
            for order in list(book.bids) + list(book.asks):
                self._cancel(order, accounts)
        for acct in accounts.values():   # no orders left: drop float residue
            acct.reserved_cash = 0.0
            acct.reserved_shares.clear()

    def close_session(self, day: int, session: int) -> None:
        """Paper 4.2.2: price updates to the last trade of the session."""
        for stock in self.stocks:
            trades = self.session_trades[stock]
            volume = sum(t.qty for t in trades)
            if trades:
                self.prices[stock] = trades[-1].price
            self.price_history.append({
                "day": day, "session": session, "stock": stock,
                "price": self.prices[stock], "volume": volume, "trades": len(trades),
                "ideal_mid": round(config.ideal_price_midpoint(stock, day), 4)
                if stock in config.IDEAL_PRICE_UPPER else None,
            })
            self.session_trades[stock] = []
