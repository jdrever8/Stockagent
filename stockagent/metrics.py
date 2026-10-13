"""Performance and fidelity metrics."""
from __future__ import annotations

import math
import random
import statistics
from typing import Dict, Iterable, List, Sequence, Tuple

from . import config


def returns_from_curve(curve: Sequence[float]) -> List[float]:
    out = []
    for a, b in zip(curve, curve[1:]):
        out.append(b / a - 1 if a > 0 else 0.0)
    return out


def sharpe(curve: Sequence[float], periods_per_year: int = config.TRADING_DAYS_PER_YEAR) -> float:
    rets = returns_from_curve(curve)
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    if sd == 0:
        return 0.0
    return statistics.mean(rets) / sd * math.sqrt(periods_per_year)


def max_drawdown(curve: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def total_return(curve: Sequence[float]) -> float:
    if len(curve) < 2 or curve[0] <= 0:
        return 0.0
    return curve[-1] / curve[0] - 1


def bootstrap_ci(values: Sequence[float], stat=statistics.mean, n: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> Tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    draws = sorted(stat([rng.choice(values) for _ in values]) for _ in range(n))
    return draws[int(alpha / 2 * n)], draws[int((1 - alpha / 2) * n) - 1]


def agent_metrics(curve: Sequence[float]) -> Dict[str, float]:
    return {"return": total_return(curve), "sharpe": sharpe(curve), "max_drawdown": max_drawdown(curve)}


def team_summary(rows: Iterable[dict]) -> List[dict]:
    """rows: one per agent-run with keys team, return, sharpe, max_drawdown, bankrupt, invalid, errors."""
    by_team: Dict[str, List[dict]] = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)
    out = []
    for team, rs in by_team.items():
        rets = [r["return"] for r in rs]
        sharpes = [r["sharpe"] for r in rs]
        lo, hi = bootstrap_ci(rets)
        out.append({
            "team": team,
            "agents": len(rs),
            "mean_return": statistics.mean(rets),
            "median_return": statistics.median(rets),
            "return_ci95": [lo, hi],
            "mean_sharpe": statistics.mean(sharpes),
            "worst_drawdown": min(r["max_drawdown"] for r in rs),
            "bankrupt_rate": sum(1 for r in rs if r["bankrupt"]) / len(rs),
            "invalid_per_agent": statistics.mean(r.get("invalid", 0) for r in rs),
            "llm_errors": sum(r.get("errors", 0) for r in rs),
        })
    out.sort(key=lambda t: (t["mean_sharpe"], t["median_return"]), reverse=True)
    return out


def price_fidelity(price_rows: Iterable[dict]) -> Dict[str, float]:
    """Mean absolute % deviation of session prices from the ideal mid (paper Tables 6/7)."""
    acc: Dict[str, List[float]] = {}
    for r in price_rows:
        if r.get("ideal_mid"):
            acc.setdefault(r["stock"], []).append(abs(r["price"] / r["ideal_mid"] - 1))
    return {s: statistics.mean(v) for s, v in acc.items()}
