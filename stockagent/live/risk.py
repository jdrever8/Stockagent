"""Hard risk limits that sit outside the LLM. Nothing reaches a broker without passing here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class RiskLimits:
    budget: float                          # max capital the agent may control (live) / starting capital (paper)
    max_position_pct: float = 0.25         # of equity, per symbol
    max_order_pct: float = 0.10            # of equity, per order
    max_orders_per_day: int = 3
    max_daily_loss_pct: float = 0.03       # no new buys after this loss today
    kill_drawdown_pct: float = 0.15        # halt all trading below this drawdown from peak
    allowed_symbols: Optional[List[str]] = None
    max_price_deviation: float = 0.05      # limit price vs last close


@dataclass
class RiskState:
    peak_equity: float
    day_start_equity: float
    orders_today: int = 0
    halted: bool = False
    violations: List[str] = field(default_factory=list)


class RiskGate:
    def __init__(self, limits: RiskLimits, state: Optional[RiskState] = None):
        self.limits = limits
        self.state = state or RiskState(limits.budget, limits.budget)

    def start_day(self, equity: float) -> None:
        s = self.state
        s.peak_equity = max(s.peak_equity, equity)
        s.day_start_equity = equity
        s.orders_today = 0
        if equity < s.peak_equity * (1 - self.limits.kill_drawdown_pct):
            if not s.halted:
                s.violations.append(f"kill switch: equity {equity:.2f} < {1 - self.limits.kill_drawdown_pct:.0%} of peak")
            s.halted = True

    def check(self, order: dict, equity: float, cash: float, positions: Dict[str, float],
              prices: Dict[str, float]) -> Tuple[Optional[dict], str]:
        """Return (possibly reduced) order or (None, reason). ``positions`` are share counts."""
        lim, s = self.limits, self.state
        if s.halted:
            return None, "trading halted by kill switch; human review required"
        if s.orders_today >= lim.max_orders_per_day:
            return None, "daily order limit reached"
        sym, side, qty, px = order["stock"], order["action_type"], int(order["amount"]), float(order["price"])
        if lim.allowed_symbols is not None and sym not in lim.allowed_symbols:
            return None, f"{sym} not in allowed symbols"
        last = prices[sym]
        if abs(px / last - 1) > lim.max_price_deviation:
            return None, f"limit {px} more than {lim.max_price_deviation:.0%} from last {last}"
        if side == "sell":
            qty = min(qty, int(positions.get(sym, 0)))  # never short
            if qty <= 0:
                return None, "no position to sell"
        else:
            if equity < s.day_start_equity * (1 - lim.max_daily_loss_pct):
                return None, "daily loss limit hit: no new buys today"
            cap_equity = min(equity, lim.budget * (1 + lim.kill_drawdown_pct))  # gains don't raise exposure fast
            room_position = cap_equity * lim.max_position_pct - positions.get(sym, 0) * last
            room = min(cap_equity * lim.max_order_pct, room_position, cash)
            qty = min(qty, int(max(0.0, room) // px))
            if qty <= 0:
                return None, "order exceeds position/order/cash limits"
        s.orders_today += 1
        out = dict(order, amount=qty)
        return out, "" if qty == int(order["amount"]) else f"reduced to {qty} by risk limits"
