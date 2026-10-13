"""The day loop from paper Fig. 2, run as a competition between teams of traders."""
from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence, Tuple

from . import config, metrics
from .agents import Account, random_endowment
from .config import SimConfig
from .llm import LLMClient
from .market import Market, Order
from .policies import Observation, Policy, make_policy, validate_loan, validate_turn
from .records import Recorder

log = logging.getLogger("stockagent")


def parse_population(spec: str) -> List[Tuple[str, int]]:
    """``"openai:gpt-5.5=20,baseline:trend=10"`` -> [("openai:gpt-5.5", 20), ("baseline:trend", 10)]."""
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        team, _, n = part.rpartition("=")
        if not team:
            team, n = n, "1"
        out.append((team.strip(), int(n)))
    return out


class Simulation:
    def __init__(self, cfg: SimConfig, population: Sequence[Tuple[str, int]],
                 temperature: Optional[float] = None, workers: int = 8, verbose: bool = False):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.workers = workers
        self.recorder = Recorder(cfg.out_dir)
        prices = {s: cfg.initial_price(s) for s in cfg.stocks}
        self.market = Market(cfg.stocks, prices)
        self.loan_rates = list(config.LOAN_RATES)
        self.clients: Dict[str, LLMClient] = {}
        self.accounts: Dict[int, Account] = {}
        self.policies: Dict[int, Policy] = {}
        self.estimates: Dict[int, dict] = {}
        self.forum: List[str] = []
        self.news: List[str] = []
        self.history: Dict[str, List[float]] = {s: [p] for s, p in prices.items()}

        # Mirrored endowments: the j-th agent of every team gets the same starting
        # portfolio and personality, so teams differ only in their policy.
        largest = max(n for _, n in population)
        endowments = [random_endowment(self.rng, cfg.stocks, prices) for _ in range(largest)]
        agent_id = 0
        for team, count in population:
            for j in range(count):
                cash, holdings, debt, loan_type, due = endowments[j]
                personality = config.PERSONALITIES[j % len(config.PERSONALITIES)]
                acct = Account(agent_id, team, personality, cash, dict(holdings))
                gross = acct.gross_assets(prices)
                acct.credit_limit = gross
                if cfg.enable_loans:
                    acct.loans.append(config_loan(debt, loan_type, due))
                acct.initial_wealth = acct.net_worth(prices)
                acct.equity_curve.append(acct.initial_wealth)
                self.accounts[agent_id] = acct
                self.policies[agent_id] = make_policy(team, personality, random.Random(self.rng.random()),
                                                      competitive=cfg.competitive, temperature=temperature,
                                                      clients=self.clients, arena=cfg.arena,
                                                      short_leverage=cfg.short_leverage)
                agent_id += 1
        self.recorder.json("config", {"sim": cfg, "population": population})

    # ------------------------------------------------------------------
    def active(self) -> List[Account]:
        return [a for a in self.accounts.values() if not a.bankrupt]

    def observe(self, acct: Account, day: int, session: int) -> Observation:
        prices = self.market.prices
        background = ""
        if day == 1:
            background = background_text(self.cfg.stocks)
        return Observation(
            day=day, session=session, sessions_per_day=self.cfg.sessions, stocks=self.cfg.stocks,
            prices=dict(prices), price_history={s: h[-20:] for s, h in self.history.items()},
            books={s: b.summary(10 if self.cfg.arena else 5) for s, b in self.market.books.items()},
            cash=acct.cash, free_cash=acct.free_cash(), holdings=dict(acct.holdings),
            free_holdings={s: acct.free_shares(s) for s in self.cfg.stocks},
            debt=acct.debt(), loans=[{"amount": l.amount, "due_day": l.due_day} for l in acct.loans],
            max_new_loan=acct.max_new_loan() if self.cfg.enable_loans else 0.0,
            loan_rates=list(self.loan_rates), personality=acct.personality,
            net_worth=acct.net_worth(prices), initial_wealth=acct.initial_wealth,
            forum=list(self.forum) if self.cfg.enable_bbs else [],
            news=list(self.news), background=background,
            leaderboard=self.standing(acct) if self.cfg.competitive else None,
            fee_note=f"Fee per fill: {config.FEE_PER_SHARE}/share, min {config.FEE_MIN}, max {config.FEE_MAX}.",
            allow_loans=self.cfg.enable_loans,
            arena=self.cfg.arena,
            short_capacity=acct.short_capacity(prices, self.cfg.short_leverage) if self.cfg.arena else 0.0,
            max_orders=self.cfg.orders_per_turn(),
            tape=self.tape() if self.cfg.arena else {},
            has_open_orders=any(o.agent_id == acct.agent_id for b in self.market.books.values()
                                for o in b.bids + b.asks),
        )

    def tape(self, n: int = 8) -> Dict[str, list]:
        out: Dict[str, list] = {s: [] for s in self.cfg.stocks}
        for t in reversed(self.market.trades):
            if len(out[t.stock]) < n:
                out[t.stock].append([t.price, t.qty])
            if all(len(v) >= n for v in out.values()):
                break
        return {s: list(reversed(v)) for s, v in out.items()}

    def standing(self, acct: Account) -> str:
        ranked = sorted(self.active(), key=lambda a: a.equity_curve[-1] / a.initial_wealth, reverse=True)
        pos = next((i for i, a in enumerate(ranked) if a.agent_id == acct.agent_id), len(ranked))
        ret = acct.equity_curve[-1] / acct.initial_wealth - 1
        return f"rank {pos + 1} of {len(ranked)} by return; your return so far {ret:+.2%}"

    def _parallel(self, fn, accounts: List[Account]):
        if self.workers <= 1:
            return [fn(a) for a in accounts]
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(fn, accounts))

    # ------------------------------------------------------------------
    def run(self) -> dict:
        for day in range(1, self.cfg.days + 1):
            self.run_day(day)
        return self.finish()

    def run_day(self, day: int) -> None:
        cfg, prices = self.cfg, self.market.prices
        for pid, policy in self.policies.items():
            if not self.accounts[pid].bankrupt:
                policy.new_day(day)

        # Pre-trading: interest, maturities, bankruptcy (paper 4.2.1)
        for acct in self.active():
            if day in config.REPAYMENT_DAYS:
                acct.pay_interest()
            acct.repay_matured(day)
            if cfg.arena:   # margin: shorts far beyond the limit are bought in at the last price
                limit = cfg.short_leverage * max(0.0, acct.net_worth(prices))
                if acct.short_exposure(prices) > 1.5 * limit:
                    covered = acct.buy_in(prices, limit)
                    self.recorder.row("buy_ins", {"day": day, "agent": acct.agent_id, "team": acct.team,
                                                  "covered": round(covered, 2)})
            if acct.cash < 0:
                acct.liquidate_for_cash(prices)
            if acct.cash < 0 or acct.net_worth(prices) <= 0:
                acct.bankrupt = True
                log.info("day %d: agent %d (%s) bankrupt", day, acct.agent_id, acct.team)

        # Events and quarterly reports (paper 4.3)
        self.news = []
        if cfg.enable_events:
            for ev in cfg.events:
                if ev.day == day:
                    self.news.append(ev.message)
                    if ev.loan_rates:
                        self.loan_rates = list(ev.loan_rates)
        if cfg.enable_reports and day in config.SEASON_REPORT_DAYS:
            q = config.SEASON_REPORT_DAYS.index(day)
            for s in cfg.stocks:
                self.news.append(f"Company {s} quarterly report: {config.QUARTERLY_REPORTS[s][q]}")
        for n in self.news:
            self.recorder.row("news", {"day": day, "news": n})

        # Loan decisions (independent -> parallel)
        if cfg.enable_loans:
            def loan(acct: Account):
                obs = self.observe(acct, day, 0)
                return acct, self.policies[acct.agent_id].decide_loan(obs), obs
            for acct, decision, obs in self._parallel(loan, self.active()):
                decision, _ = validate_loan(decision, obs)
                if decision and decision["loan"] == "yes":
                    acct.take_loan(decision["amount"], decision["loan_type"], day, self.loan_rates)
                self.recorder.row("loans", {"day": day, "agent": acct.agent_id, "team": acct.team,
                                            **{k: (decision or {}).get(k) for k in ("loan", "loan_type", "amount")}})

        # Trading sessions: random order each session, sequential so each agent sees the book.
        for session in range(1, cfg.sessions + 1):
            order = self.active()
            self.rng.shuffle(order)
            for acct in order:
                obs = self.observe(acct, day, session)
                raw = self.policies[acct.agent_id].decide_order(obs)
                turn, error = validate_turn(raw, obs)   # baselines are validated too
                if turn and turn["cancel_open"]:
                    self.market.cancel_agent_orders(acct.agent_id, self.accounts)
                orders = turn["orders"] if turn else []
                if not orders:
                    self.recorder.row("orders", {"day": day, "session": session, "agent": acct.agent_id,
                                                 "team": acct.team, "action": "no", "stock": None, "qty": None,
                                                 "price": None, "error": error, "reason": ""})
                for action in orders:
                    self.recorder.row("orders", {"day": day, "session": session, "agent": acct.agent_id,
                                                 "team": acct.team, "action": action["action_type"],
                                                 "stock": action["stock"], "qty": action["amount"],
                                                 "price": action["price"], "error": "",
                                                 "reason": action.get("reason", "")})
                    trades = self.market.submit(
                        Order(acct.agent_id, action["action_type"], action["stock"], action["amount"], action["price"]),
                        self.accounts, day, session)
                    self.recorder.rows("trades", [t.__dict__ for t in trades])
            self.market.close_session(day, session)
            for s in cfg.stocks:
                self.history[s].append(self.market.prices[s])
        self.recorder.rows("prices", self.market.price_history[-len(cfg.stocks) * cfg.sessions:])
        self.market.clear_books(self.accounts)

        # Post-trading: equity, estimates, forum (paper 4.2.3)
        for acct in self.accounts.values():
            acct.equity_curve.append(acct.net_worth(prices) if not acct.bankrupt else 0.0)

        def post(acct: Account):
            obs = self.observe(acct, day, cfg.sessions)
            policy = self.policies[acct.agent_id]
            return acct, policy.estimate(obs), (policy.post(obs) if cfg.enable_bbs else "")
        posts = []
        for acct, estimate, text in self._parallel(post, self.active()):
            self.recorder.row("agent_days", {
                "day": day, "agent": acct.agent_id, "team": acct.team, "personality": acct.personality,
                "cash": round(acct.cash, 2), "debt": round(acct.debt(), 2),
                "net_worth": round(acct.equity_curve[-1], 2),
                **{f"hold_{s}": acct.holdings.get(s, 0) for s in cfg.stocks},
                "est_buy": "|".join(estimate.get("buy", [])), "est_sell": "|".join(estimate.get("sell", [])),
                "est_loan": estimate.get("loan"),
            })
            if text:
                posts.append(text)
                self.recorder.jsonl("forum", {"day": day, "agent": acct.agent_id, "team": acct.team, "post": text})
        self.rng.shuffle(posts)  # anonymous BBS
        self.forum = [p[: cfg.forum_chars] for p in posts[: cfg.forum_sample]]
        log.info("day %d closed: %s", day, {s: self.market.prices[s] for s in cfg.stocks})

    def finish(self) -> dict:
        rows = []
        for acct in self.accounts.values():
            m = metrics.agent_metrics(acct.equity_curve) if not acct.bankrupt else {
                "return": -1.0, "sharpe": metrics.sharpe(acct.equity_curve),
                "max_drawdown": -1.0}
            policy = self.policies[acct.agent_id]
            rows.append({"agent": acct.agent_id, "team": acct.team, "personality": acct.personality,
                         "seed": self.cfg.seed, "initial": round(acct.initial_wealth, 2),
                         "final": round(acct.equity_curve[-1], 2), "bankrupt": acct.bankrupt,
                         "fees": round(acct.fees_paid, 2), "interest": round(acct.interest_paid, 2),
                         "invalid": getattr(policy, "invalid", 0), "errors": getattr(policy, "errors", 0), **m})
        self.recorder.rows("agents_final", rows)
        summary = {
            "seed": self.cfg.seed,
            "teams": metrics.team_summary(rows),
            "price_fidelity": metrics.price_fidelity(self.market.price_history),
            "final_prices": dict(self.market.prices),
            "trades": len(self.market.trades),
            "llm_usage": {k: vars(c.usage) for k, c in self.clients.items()},
        }
        self.recorder.json("summary", summary)
        self.recorder.close()
        summary["agents"] = rows
        return summary


def config_loan(amount: float, loan_type: int, due: int):
    from .agents import Loan
    return Loan(amount, loan_type, config.LOAN_RATES[loan_type], 0, due)


def background_text(stocks: List[str]) -> str:
    lines = ["Companies (fictional; do not assume any real-world identity):"]
    for s in stocks:
        h = config.HISTORY[s]
        lines.append(f"Company {s}: {config.COMPANY_PROFILES[s]}")
        lines.append(f"  Last 12 quarters revenue (M): {h['revenue']}")
        lines.append(f"  Net profit (M): {h['net_profit']}")
        lines.append(f"  Cash flow (M): {h['cash_flow']}")
        v = config.VALUATION_INPUTS.get(s)
        if v:
            lines.append(f"  WACC: {v['wacc'][0]:.2%}; sustainable growth rate: {v['growth']:.1%}")
    return "\n".join(lines)
