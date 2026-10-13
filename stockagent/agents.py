"""Trader accounts: cash, holdings, reservations, loans and bankruptcy."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List

from . import config


@dataclass
class Loan:
    amount: float
    loan_type: int
    rate: float          # annualised, fixed at origination
    start_day: int
    due_day: int


@dataclass
class Account:
    agent_id: int
    team: str                          # policy/model label used for competition
    personality: str
    cash: float
    holdings: Dict[str, int]
    loans: List[Loan] = field(default_factory=list)
    initial_wealth: float = 0.0        # net worth at t0 (assets - debt)
    credit_limit: float = 0.0          # max total outstanding loans (paper: <= value of capital)
    reserved_cash: float = 0.0
    reserved_shares: Dict[str, int] = field(default_factory=dict)
    fees_paid: float = 0.0
    interest_paid: float = 0.0
    bankrupt: bool = False
    equity_curve: List[float] = field(default_factory=list)

    # --- reservations -----------------------------------------------------
    def free_cash(self) -> float:
        return self.cash - self.reserved_cash

    def free_shares(self, stock: str) -> int:
        return self.holdings.get(stock, 0) - self.reserved_shares.get(stock, 0)

    def reserve_cash(self, amount: float) -> None:
        self.reserved_cash += amount

    def release_cash(self, amount: float) -> None:
        self.reserved_cash = max(0.0, self.reserved_cash - amount)

    def reserve_shares(self, stock: str, qty: int) -> None:
        self.reserved_shares[stock] = self.reserved_shares.get(stock, 0) + qty

    def release_shares(self, stock: str, qty: int) -> None:
        self.reserved_shares[stock] = max(0, self.reserved_shares.get(stock, 0) - qty)

    # --- valuation --------------------------------------------------------
    def debt(self) -> float:
        return sum(l.amount for l in self.loans)

    def gross_assets(self, prices: Dict[str, float]) -> float:
        return self.cash + sum(q * prices[s] for s, q in self.holdings.items())

    def net_worth(self, prices: Dict[str, float]) -> float:
        return self.gross_assets(prices) - self.debt()

    def short_exposure(self, prices: Dict[str, float]) -> float:
        """Market value of short positions plus resting sells that would open new shorts."""
        held = sum(-q * prices[s] for s, q in self.holdings.items() if q < 0)
        pending = sum(max(0, r - max(0, self.holdings.get(s, 0))) * prices[s]
                      for s, r in self.reserved_shares.items())
        return held + pending

    def short_capacity(self, prices: Dict[str, float], leverage: float) -> float:
        return max(0.0, leverage * max(0.0, self.net_worth(prices)) - self.short_exposure(prices))

    def buy_in(self, prices: Dict[str, float], max_exposure: float) -> float:
        """Forced cover of shorts at the last price until exposure <= max_exposure. Returns notional covered."""
        covered = 0.0
        for stock in sorted(self.holdings, key=lambda s: self.holdings[s] * prices[s]):
            exposure = sum(-q * prices[s] for s, q in self.holdings.items() if q < 0)
            if exposure <= max_exposure:
                break
            q = self.holdings[stock]
            if q >= 0:
                continue
            cover = min(-q, int((exposure - max_exposure) // prices[stock]) + 1)
            self.holdings[stock] += cover
            self.cash -= cover * prices[stock]
            covered += cover * prices[stock]
        return covered

    def max_new_loan(self) -> float:
        return max(0.0, self.credit_limit - self.debt())

    # --- loans (paper 4.2.1) ----------------------------------------------
    def take_loan(self, amount: float, loan_type: int, day: int, rates: List[float]) -> Loan:
        loan = Loan(amount, loan_type, rates[loan_type], day, day + config.LOAN_TERMS_DAYS[loan_type])
        self.loans.append(loan)
        self.cash += amount
        return loan

    def pay_interest(self) -> float:
        """Simple interest, paid monthly on REPAYMENT_DAYS."""
        due = sum(l.amount * l.rate / 12 for l in self.loans)
        self.cash -= due
        self.interest_paid += due
        return due

    def repay_matured(self, day: int) -> float:
        matured = [l for l in self.loans if l.due_day <= day]
        total = sum(l.amount for l in matured)
        self.cash -= total
        self.loans = [l for l in self.loans if l.due_day > day]
        return total

    def liquidate_for_cash(self, prices: Dict[str, float]) -> None:
        """Forced sale at the last price to cover negative cash (orders already cancelled)."""
        for stock in sorted(self.holdings, key=lambda s: -self.holdings[s] * prices[s]):
            if self.cash >= 0:
                return
            qty = self.holdings[stock]
            if qty <= 0:
                continue
            need = -self.cash
            sell = min(qty, int(need // prices[stock]) + 1)
            self.holdings[stock] -= sell
            self.cash += sell * prices[stock]


def random_endowment(rng: random.Random, stocks: List[str], prices: Dict[str, float]):
    """Paper 4.1.2: total assets uniform in [100k, 5M], spread across cash and stocks,
    with an initial loan that does not exceed the agent's assets."""
    total = rng.uniform(config.MIN_INITIAL_PROPERTY, config.MAX_INITIAL_PROPERTY)
    weights = [rng.random() for _ in range(len(stocks) + 1)]
    norm = sum(weights)
    cash = total * weights[0] / norm
    holdings = {}
    for s, w in zip(stocks, weights[1:]):
        holdings[s] = int(total * w / norm / prices[s])
        cash += total * w / norm - holdings[s] * prices[s]
    debt = rng.uniform(0, total * 0.5)
    loan_type = rng.randrange(len(config.LOAN_TERMS_DAYS))
    due = rng.choice(config.REPAYMENT_DAYS[:3])
    return cash, holdings, debt, loan_type, due
