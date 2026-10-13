"""Brokers: a virtual paper portfolio and an Alpaca REST adapter.

Live (real-money) access is deliberately awkward: the Alpaca adapter talks to the
paper endpoint unless constructed with ``live=True``, and the CLI only does that
after the champion file has been signed off by a human and STOCKAGENT_LIVE=1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from .. import config
from ..config import env
from ..market import transaction_fee
from .feeds import Bar


@dataclass
class VirtualPortfolio:
    """Paper portfolio with no-lookahead fills.

    Orders are placed after the close of day t and evaluated against the bar of
    day t+1: a buy fills at the open if open <= limit, else at the limit if
    low <= limit; sells mirror this. Fees follow the simulator's schedule and a
    slippage haircut is applied to open fills.
    """
    cash: float
    positions: Dict[str, int] = field(default_factory=dict)
    pending: List[dict] = field(default_factory=list)
    equity: List[list] = field(default_factory=list)     # [[date, value]]
    fills: List[dict] = field(default_factory=list)
    slippage: float = 0.0005

    def value(self, prices: Dict[str, float]) -> float:
        return self.cash + sum(q * prices[s] for s, q in self.positions.items() if q)

    def reserved_cash(self) -> float:
        return sum(o["amount"] * o["price"] + config.FEE_MAX for o in self.pending if o["action_type"] == "buy")

    def reserved_shares(self, sym: str) -> int:
        return sum(o["amount"] for o in self.pending if o["action_type"] == "sell" and o["stock"] == sym)

    def queue(self, order: dict) -> None:
        self.pending.append(dict(order))

    def settle(self, date: str, bars: Dict[str, Optional[Bar]]) -> List[dict]:
        done = []
        for o in self.pending:
            bar = bars.get(o["stock"])
            if bar is None:
                continue
            limit, qty, side = o["price"], o["amount"], o["action_type"]
            px = None
            if side == "buy":
                if bar.open <= limit:
                    px = min(limit, bar.open * (1 + self.slippage))
                elif bar.low <= limit:
                    px = limit
                if px is not None:
                    cost = qty * px + transaction_fee(qty)
                    if cost > self.cash:
                        qty = int((self.cash - config.FEE_MAX) // px)
                        cost = qty * px + transaction_fee(qty) if qty > 0 else 0
                    if qty > 0:
                        self.cash -= cost
                        self.positions[o["stock"]] = self.positions.get(o["stock"], 0) + qty
            else:
                if bar.open >= limit:
                    px = max(limit, bar.open * (1 - self.slippage))
                elif bar.high >= limit:
                    px = limit
                qty = min(qty, self.positions.get(o["stock"], 0))
                if px is not None and qty > 0:
                    self.cash += qty * px - transaction_fee(qty)
                    self.positions[o["stock"]] -= qty
            if px is not None and qty > 0:
                done.append({"date": date, "stock": o["stock"], "side": side, "qty": qty, "price": round(px, 4)})
        self.pending = []   # day orders
        self.fills.extend(done)
        return done


class AlpacaBroker:
    PAPER = "https://paper-api.alpaca.markets"
    LIVE = "https://api.alpaca.markets"

    def __init__(self, live: bool = False):
        self.base = self.LIVE if live else self.PAPER
        self.live = live
        key, secret = env("ALPACA_API_KEY_ID"), env("ALPACA_API_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def _req(self, method: str, path: str, **kw):
        resp = requests.request(method, self.base + path, headers=self.headers, timeout=30, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"Alpaca {method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else None

    def account(self) -> dict:
        a = self._req("GET", "/v2/account")
        return {"cash": float(a["cash"]), "equity": float(a["equity"]), "buying_power": float(a["buying_power"]),
                "trading_blocked": a.get("trading_blocked", False)}

    def positions(self) -> Dict[str, int]:
        return {p["symbol"]: int(float(p["qty"])) for p in self._req("GET", "/v2/positions")}

    def clock(self) -> dict:
        return self._req("GET", "/v2/clock")

    def cancel_all(self) -> None:
        self._req("DELETE", "/v2/orders")

    def submit_limit(self, symbol: str, side: str, qty: int, limit: float, client_id: str) -> dict:
        body = {"symbol": symbol, "qty": str(qty), "side": side, "type": "limit", "time_in_force": "day",
                "limit_price": f"{limit:.2f}", "client_order_id": client_id}
        return self._req("POST", "/v2/orders", json=body)
