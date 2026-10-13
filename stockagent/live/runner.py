"""Stages 2 and 3 of the pipeline.

Stage 2 - paper league: every qualified candidate gets an identical virtual
portfolio and trades the same real (or replayed) daily bars, forward only. A
buy-and-hold benchmark runs alongside. State lives in one JSON file so a daily
cron job can call ``step``.

Promotion: after ``PromotionRules.min_days`` the best candidate that clears every
rule is written to ``champion.json`` with status ``awaiting_approval``. A human
must set ``approved_by`` before any live order is possible.

Stage 3 - live: the champion trades a capped budget through a broker, every
order passes the RiskGate, and by default every order needs confirmation.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import random
import uuid
import zlib
from typing import Callable, Dict, List, Optional

from .. import config, metrics
from ..llm import LLMClient
from ..policies import LLMPolicy, Observation, Policy, make_policy, validate_order
from .brokers import VirtualPortfolio
from .feeds import Feed
from .risk import RiskGate, RiskLimits, RiskState

log = logging.getLogger("stockagent")

BACKGROUND = ("You trade real listed equities using daily data. Decisions are made after the close; "
              "orders are day limit orders for the next session (fill at the open if marketable, otherwise "
              "at your limit if the day's range reaches it). No shorting, no leverage. Hard risk limits may "
              "reduce or reject your orders.")


def parse_candidate(spec: str):
    """``model[@Personality]`` -> (team, personality)."""
    team, _, personality = spec.partition("@")
    return team, personality or "Balanced"


@dataclasses.dataclass
class PromotionRules:
    min_days: int = 40                 # ~2 months of forward paper trading
    min_excess_return: float = 0.0     # vs equal-weight buy-and-hold, total over the period
    min_sharpe: float = 0.5
    max_drawdown: float = -0.10
    max_risk_rejections_pct: float = 0.25
    max_llm_error_rate: float = 0.05


class PaperLeague:
    def __init__(self, state_path: str, feed: Feed, candidates: Optional[List[str]] = None,
                 capital: float = 100_000.0, limits: Optional[dict] = None, temperature: Optional[float] = None,
                 orders_per_day: int = 3):
        self.state_path = state_path
        self.feed = feed
        self.temperature = temperature
        self.clients: Dict[str, LLMClient] = {}
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        else:
            if not candidates:
                raise ValueError("new paper league needs candidates")
            self.state = {
                "created": dt.datetime.now(dt.timezone.utc).isoformat(), "capital": capital, "symbols": feed.symbols,
                "orders_per_day": orders_per_day, "last_date": None, "days": 0, "forum": [],
                "limits": limits or {}, "candidates": {}, "benchmark": None,
            }
            for spec in candidates + ["baseline:buy_and_hold"]:
                self.state["candidates"][spec] = {
                    "portfolio": dataclasses.asdict(VirtualPortfolio(cash=capital)),
                    "risk": dataclasses.asdict(RiskState(capital, capital)),
                    "memory": [], "rejections": 0, "orders": 0, "llm_errors": 0, "llm_calls": 0,
                }
        self.policies: Dict[str, Policy] = {}
        for spec, c in self.state["candidates"].items():
            team, personality = parse_candidate(spec)
            policy = make_policy(team, personality, random.Random(zlib.crc32(spec.encode())), competitive=True,
                                 temperature=temperature, clients=self.clients)
            if isinstance(policy, LLMPolicy):
                policy.memory = list(c["memory"])
            if hasattr(policy, "done"):
                policy.done = set(c.get("bh_done", []))
            self.policies[spec] = policy

    def save(self) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------
    def pending_dates(self) -> List[str]:
        last = self.state["last_date"]
        return [d for d in self.feed.dates() if last is None or d > last]

    def step(self, date: str) -> dict:
        st = self.state
        symbols = st["symbols"]
        bars_today = {s: self.feed.bar(s, date) for s in symbols}
        prices = {}
        for s in symbols:
            hist = self.feed.bars(s, date, 1)
            if not hist:
                raise ValueError(f"no data for {s} on or before {date}")
            prices[s] = hist[-1].close
        history = {s: [round(b.close, 2) for b in self.feed.bars(s, date, 20)] for s in symbols}
        st["days"] += 1
        new_posts = []

        for spec, c in st["candidates"].items():
            pf = VirtualPortfolio(**c["portfolio"])
            pf.settle(date, bars_today)
            equity = pf.value(prices)
            pf.equity.append([date, round(equity, 2)])
            limits = RiskLimits(budget=st["capital"], allowed_symbols=symbols,
                                max_orders_per_day=st["orders_per_day"], **st["limits"])
            gate = RiskGate(limits, RiskState(**c["risk"]))
            gate.start_day(equity)
            policy = self.policies[spec]
            policy.new_day(st["days"])
            for session in range(1, st["orders_per_day"] + 1):
                free_cash = pf.cash - pf.reserved_cash()
                obs = Observation(
                    day=st["days"], session=session, sessions_per_day=st["orders_per_day"], stocks=symbols,
                    prices={s: round(p, 2) for s, p in prices.items()}, price_history=history, books={},
                    cash=pf.cash, free_cash=free_cash, holdings=dict(pf.positions),
                    free_holdings={s: pf.positions.get(s, 0) - pf.reserved_shares(s) for s in symbols},
                    debt=0.0, loans=[], max_new_loan=0.0, loan_rates=[], personality=parse_candidate(spec)[1],
                    net_worth=equity, initial_wealth=st["capital"], forum=st["forum"],
                    background=BACKGROUND if session == 1 else "",
                    leaderboard=self.standing(spec), allow_loans=False,
                    fee_note=f"Fees: {config.FEE_PER_SHARE}/share, min {config.FEE_MIN}, max {config.FEE_MAX}.",
                )
                raw = policy.decide_order(obs)
                order, err = validate_order(raw, obs)
                if order is None:
                    continue    # invalid after retries; give it another session
                if order["action_type"] == "no":
                    break       # the agent passed; don't keep asking
                c["orders"] += 1
                approved, reason = gate.check(order, equity, free_cash, pf.positions, prices)
                if approved is None:
                    c["rejections"] += 1
                    log.info("%s %s rejected: %s", date, spec, reason)
                    continue
                pf.queue(approved)
            if isinstance(policy, LLMPolicy):
                post = policy.post(obs)
                c["memory"] = policy.memory[-30:]
                c["llm_errors"] += policy.errors
                c["llm_calls"] = self.clients[parse_candidate(spec)[0]].usage.calls
                policy.errors = 0
                if post:
                    new_posts.append(post[:300])
            if hasattr(policy, "done"):
                c["bh_done"] = sorted(policy.done)
            c["portfolio"] = dataclasses.asdict(pf)
            c["risk"] = dataclasses.asdict(gate.state)

        random.shuffle(new_posts)
        st["forum"] = new_posts[:10]
        st["last_date"] = date
        self.save()
        return self.leaderboard()

    def standing(self, spec: str) -> str:
        board = self.leaderboard()["rows"]
        pos = next(i for i, r in enumerate(board) if r["candidate"] == spec)
        return f"rank {pos + 1} of {len(board)} paper portfolios; your return {board[pos]['return']:+.2%}"

    def leaderboard(self) -> dict:
        rows = []
        bench = self.state["candidates"]["baseline:buy_and_hold"]["portfolio"]["equity"]
        bench_ret = metrics.total_return([self.state["capital"]] + [v for _, v in bench])
        for spec, c in self.state["candidates"].items():
            curve = [self.state["capital"]] + [v for _, v in c["portfolio"]["equity"]]
            rows.append({
                "candidate": spec, "return": metrics.total_return(curve), "excess": metrics.total_return(curve) - bench_ret,
                "sharpe": metrics.sharpe(curve), "max_drawdown": metrics.max_drawdown(curve),
                "orders": c["orders"], "rejections": c["rejections"], "llm_errors": c["llm_errors"],
                "llm_calls": c["llm_calls"], "halted": c["risk"]["halted"],
            })
        rows.sort(key=lambda r: (r["sharpe"], r["return"]), reverse=True)
        return {"date": self.state["last_date"], "days": self.state["days"], "benchmark_return": bench_ret, "rows": rows}

    def promote(self, rules: PromotionRules, out_path: str) -> Optional[dict]:
        board = self.leaderboard()
        eligible = []
        for r in board["rows"]:
            if r["candidate"].startswith("baseline:"):
                continue
            reasons = []
            if board["days"] < rules.min_days:
                reasons.append(f"only {board['days']} of {rules.min_days} days")
            if r["excess"] <= rules.min_excess_return:
                reasons.append("did not beat buy-and-hold")
            if r["sharpe"] < rules.min_sharpe:
                reasons.append(f"Sharpe {r['sharpe']:.2f} < {rules.min_sharpe}")
            if r["max_drawdown"] < rules.max_drawdown:
                reasons.append(f"drawdown {r['max_drawdown']:.1%}")
            if r["orders"] and r["rejections"] / r["orders"] > rules.max_risk_rejections_pct:
                reasons.append("too many risk rejections")
            if r["llm_calls"] and r["llm_errors"] / r["llm_calls"] > rules.max_llm_error_rate:
                reasons.append("LLM error rate too high")
            if r["halted"]:
                reasons.append("hit kill switch")
            r["promotion_blockers"] = reasons
            if not reasons:
                eligible.append(r)
        if not eligible:
            return None
        best = eligible[0]
        champion = {
            "candidate": best["candidate"], "status": "awaiting_approval", "approved_by": None,
            "approved_budget": None, "evidence": best, "benchmark_return": board["benchmark_return"],
            "paper_days": board["days"], "rules": dataclasses.asdict(rules),
            "created": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(champion, fh, indent=2)
        return champion


# ---------------------------------------------------------------------------
# Stage 3: live (or broker-paper) trading for the approved champion
# ---------------------------------------------------------------------------
def live_step(champion_path: str, state_path: str, feed: Feed, broker, budget: float,
              confirm: Callable[[dict], bool], limits: Optional[dict] = None, temperature: Optional[float] = None,
              orders_per_day: int = 3) -> List[dict]:
    with open(champion_path, encoding="utf-8") as fh:
        champion = json.load(fh)
    if broker.live:
        if not champion.get("approved_by"):
            raise PermissionError("champion.json has no approved_by; a human must sign off before live trading")
        if champion.get("approved_budget") is None or budget > float(champion["approved_budget"]):
            raise PermissionError("budget exceeds approved_budget in champion.json")
        if os.getenv("STOCKAGENT_LIVE") != "1":
            raise PermissionError("set STOCKAGENT_LIVE=1 to enable real-money orders")

    state = {"memory": [], "risk": None, "days": 0, "log": []}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)

    # The champion trades a dedicated account funded with exactly its budget, so the
    # broker itself enforces the cap; refuse to run against a larger account.
    acct = broker.account()
    if acct.get("trading_blocked"):
        raise RuntimeError("broker account is blocked")
    if acct["equity"] > budget * 1.5:
        raise PermissionError(f"account equity {acct['equity']:.2f} is far above the budget {budget:.2f}; "
                              "use a dedicated account funded with the budget")
    symbols = feed.symbols
    today = feed.dates()[-1]
    prices = {s: feed.bars(s, today, 1)[-1].close for s in symbols}
    positions = {s: q for s, q in broker.positions().items() if s in symbols}
    broker.cancel_all()   # yesterday's unfilled day orders are gone anyway; start clean
    cash = max(0.0, min(acct["cash"], acct["buying_power"]))
    equity = acct["equity"]

    limits_obj = RiskLimits(budget=budget, allowed_symbols=symbols, max_orders_per_day=orders_per_day, **(limits or {}))
    gate = RiskGate(limits_obj, RiskState(**state["risk"]) if state["risk"] else None)
    gate.start_day(equity)

    team, personality = parse_candidate(champion["candidate"])
    policy = make_policy(team, personality, random.Random(0), competitive=False, temperature=temperature)
    if isinstance(policy, LLMPolicy):
        policy.memory = state["memory"]
    state["days"] += 1
    policy.new_day(state["days"])
    history = {s: [round(b.close, 2) for b in feed.bars(s, today, 20)] for s in symbols}
    placed = []
    for session in range(1, orders_per_day + 1):
        obs = Observation(
            day=state["days"], session=session, sessions_per_day=orders_per_day, stocks=symbols,
            prices={s: round(p, 2) for s, p in prices.items()}, price_history=history, books={},
            cash=cash, free_cash=cash, holdings=positions, free_holdings=dict(positions), debt=0.0, loans=[],
            max_new_loan=0.0, loan_rates=[], personality=personality, net_worth=equity, initial_wealth=budget,
            background=BACKGROUND if session == 1 else "", allow_loans=False,
        )
        order, err = validate_order(policy.decide_order(obs), obs)
        if not order or order["action_type"] == "no":
            break
        approved, reason = gate.check(order, equity, cash, positions, prices)
        entry = {"date": today, "order": order, "approved": approved, "reason": reason}
        if approved and confirm(approved):
            res = broker.submit_limit(approved["stock"], approved["action_type"], approved["amount"],
                                      approved["price"], client_id=f"sa-{uuid.uuid4().hex[:16]}")
            entry["broker_order_id"] = res.get("id")
            placed.append(entry)
            if approved["action_type"] == "buy":
                cash -= approved["amount"] * approved["price"] + config.FEE_MAX
            else:
                positions[approved["stock"]] -= approved["amount"]
        else:
            entry["skipped"] = True
        state["log"].append(entry)
    if isinstance(policy, LLMPolicy):
        policy.post(obs)
        state["memory"] = policy.memory[-30:]
    state["risk"] = dataclasses.asdict(gate.state)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    return placed
