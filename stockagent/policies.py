"""Decision policies: LLM traders and rule-based baselines.

A policy only sees an ``Observation`` and returns plain dicts, so the same policy
runs in the simulator, in paper trading and (once promoted) live.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config
from .llm import LLMClient, LLMError, extract_json

NO_ORDER = {"action_type": "no"}
NO_LOAN = {"loan": "no"}


@dataclass
class Observation:
    day: int
    session: int
    sessions_per_day: int
    stocks: List[str]
    prices: Dict[str, float]
    price_history: Dict[str, List[float]]          # recent session/daily closes, oldest first
    books: Dict[str, dict]                         # {"bids": [[p, q]], "asks": [[p, q]]}; empty in real markets
    cash: float
    free_cash: float
    holdings: Dict[str, int]
    free_holdings: Dict[str, int]
    debt: float
    loans: List[dict]
    max_new_loan: float
    loan_rates: List[float]
    personality: str
    net_worth: float
    initial_wealth: float
    forum: List[str] = field(default_factory=list)
    news: List[str] = field(default_factory=list)  # events and quarterly reports released today
    background: str = ""                           # company profiles / history (day 1)
    leaderboard: Optional[str] = None              # competitive standing, if enabled
    fee_note: str = ""
    allow_loans: bool = True


# ---------------------------------------------------------------------------
# Validation ("secretary", paper 3.1)
# ---------------------------------------------------------------------------
MAX_PRICE_DEVIATION = 0.20  # reject limit prices >20% from last: guards against unit/typo errors


def validate_order(raw: dict, obs: Observation) -> Tuple[Optional[dict], str]:
    action = str(raw.get("action_type", "")).lower().strip()
    if action in ("no", "hold", "none", ""):
        return dict(NO_ORDER), ""
    if action not in ("buy", "sell"):
        return None, "action_type must be 'buy', 'sell' or 'no'."
    stock = str(raw.get("stock", "")).upper().strip()
    if stock not in obs.stocks:
        return None, f"stock must be one of {obs.stocks}."
    try:
        qty = int(raw.get("amount"))
        price = round(float(raw.get("price")), 2)
    except (TypeError, ValueError):
        return None, "amount must be an integer and price a number."
    if qty <= 0 or price <= 0:
        return None, "amount and price must be positive."
    last = obs.prices[stock]
    if abs(price / last - 1) > MAX_PRICE_DEVIATION:
        return None, (f"price {price} is more than {int(MAX_PRICE_DEVIATION * 100)}% away from the last "
                      f"price {last}; quote a realistic limit price.")
    if action == "buy":
        cost = qty * price + config.FEE_MAX
        if cost > obs.free_cash:
            max_qty = int(max(0.0, obs.free_cash - config.FEE_MAX) // price)
            return None, f"insufficient free cash {obs.free_cash:.2f}; at {price} you can buy at most {max_qty}."
    else:
        if qty > obs.free_holdings.get(stock, 0):
            return None, f"you can sell at most {obs.free_holdings.get(stock, 0)} shares of {stock}."
    return {"action_type": action, "stock": stock, "amount": qty, "price": price,
            "reason": str(raw.get("reason", ""))[:300]}, ""


def validate_loan(raw: dict, obs: Observation) -> Tuple[Optional[dict], str]:
    choice = str(raw.get("loan", "")).lower().strip()
    if choice == "no":
        return dict(NO_LOAN), ""
    if choice != "yes":
        return None, "loan must be 'yes' or 'no'."
    try:
        loan_type = int(raw.get("loan_type"))
        amount = float(raw.get("amount"))
    except (TypeError, ValueError):
        return None, "loan_type must be 0, 1 or 2 and amount a number."
    if loan_type not in range(len(config.LOAN_TERMS_DAYS)):
        return None, "loan_type must be 0, 1 or 2."
    if not 0 < amount <= obs.max_new_loan:
        return None, f"amount must be positive and at most {obs.max_new_loan:.2f}."
    return {"loan": "yes", "loan_type": loan_type, "amount": amount}, ""


def validate_estimate(raw: dict, obs: Observation) -> Tuple[Optional[dict], str]:
    buy = [s for s in raw.get("buy", []) if s in obs.stocks] if isinstance(raw.get("buy"), list) else None
    sell = [s for s in raw.get("sell", []) if s in obs.stocks] if isinstance(raw.get("sell"), list) else None
    loan = str(raw.get("loan", "")).lower()
    if buy is None or sell is None or loan not in ("yes", "no"):
        return None, 'return {"buy": [stocks], "sell": [stocks], "loan": "yes"|"no"}.'
    return {"buy": buy, "sell": sell, "loan": loan}, ""


# ---------------------------------------------------------------------------
class Policy:
    team = "base"

    def new_day(self, day: int) -> None:
        pass

    def decide_loan(self, obs: Observation) -> dict:
        return dict(NO_LOAN)

    def decide_order(self, obs: Observation) -> dict:
        return dict(NO_ORDER)

    def estimate(self, obs: Observation) -> dict:
        return {"buy": [], "sell": [], "loan": "no"}

    def post(self, obs: Observation) -> str:
        return ""


# ---------------------------------------------------------------------------
# LLM trader
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a stock trader with a {personality} investment style.
{objective}
Rules of the market:
- Orders are day limit orders; a buy fills against asks at or below your price, a sell against bids at or above it.
- Unfilled orders expire at the close. {fee_note}
- You cannot short or spend more than your free cash; prices must be within 20% of the last price.
Always answer with exactly one JSON object when asked for JSON, optionally preceded by brief reasoning."""

COMPETITIVE = ("You are competing against other traders, including other AI models and rule-based bots. "
               "You are ranked by the growth of your net worth (assets minus debt) over the whole "
               "competition, with large drawdowns and bankruptcy counted against you. Trade only when "
               "you believe it improves your expected risk-adjusted result.")
COOPERATIVE = "Your goal is to maximise the total value of your assets and cash."


def _fmt_money(x: float) -> str:
    return f"{x:,.2f}"


def render_state(obs: Observation) -> str:
    lines = [f"Day {obs.day}, session {obs.session}/{obs.sessions_per_day}."]
    lines.append("Market:")
    for s in obs.stocks:
        hist = obs.price_history.get(s, [])[-10:]
        book = obs.books.get(s) or {}
        book_txt = ""
        if book.get("bids") or book.get("asks"):
            book_txt = f" bids={book.get('bids', [])} asks={book.get('asks', [])}"
        lines.append(f"- {s}: last={obs.prices[s]} recent={hist}{book_txt}")
    lines.append(
        f"Portfolio: cash={_fmt_money(obs.cash)} free_cash={_fmt_money(obs.free_cash)} "
        + " ".join(f"holdings[{s}]={obs.holdings.get(s, 0)}" for s in obs.stocks) + " "
        + " ".join(f"holdings_free[{s}]={obs.free_holdings.get(s, 0)}" for s in obs.stocks)
    )
    lines.append(f"Debt={_fmt_money(obs.debt)} net_worth={_fmt_money(obs.net_worth)} "
                 f"start_net_worth={_fmt_money(obs.initial_wealth)}")
    if obs.leaderboard:
        lines.append(f"Standing: {obs.leaderboard}")
    return "\n".join(lines)


class LLMPolicy(Policy):
    def __init__(self, client: LLMClient, personality: str, competitive: bool = True,
                 team: Optional[str] = None, max_retries: int = 2, history_turns: int = 12):
        self.client = client
        self.team = team or client.spec
        self.personality = personality
        self.competitive = competitive
        self.max_retries = max_retries
        self.history_turns = history_turns
        self.messages: List[Dict[str, str]] = []
        self.memory: List[str] = []   # one line per past day, carried across days
        self.invalid = 0
        self.errors = 0
        self._context_sent = False

    def _system(self, obs: Observation) -> str:
        return SYSTEM_PROMPT.format(personality=self.personality,
                                    objective=COMPETITIVE if self.competitive else COOPERATIVE,
                                    fee_note=obs.fee_note)

    def new_day(self, day: int) -> None:
        self.messages = []
        self._context_sent = False

    def _ask(self, obs: Observation, prompt: str) -> str:
        self.messages.append({"role": "user", "content": prompt})
        window = self.messages[-(2 * self.history_turns + 1):]
        try:
            reply = self.client.chat(self._system(obs), window)
        except LLMError:
            self.errors += 1
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def _ask_json(self, obs, prompt, validator, fallback):
        try:
            reply = self._ask(obs, prompt)
            for attempt in range(self.max_retries + 1):
                try:
                    result, error = validator(extract_json(reply), obs)
                except ValueError as exc:
                    result, error = None, f"could not parse JSON ({exc})."
                if result is not None:
                    return result
                self.invalid += 1
                if attempt == self.max_retries:
                    break
                reply = self._ask(obs, f"Your answer was rejected: {error} Answer again with one JSON object.")
        except LLMError:
            pass
        return dict(fallback)

    def _context(self, obs: Observation) -> str:
        """Background, memory, news and forum: sent once per day, then kept in the conversation."""
        if self._context_sent:
            return ""
        self._context_sent = True
        parts = []
        if obs.background:
            parts.append(obs.background)
        if self.memory:
            parts.append("Your notes from previous days:\n" + "\n".join(self.memory[-10:]))
        if obs.news:
            parts.append("News today:\n" + "\n".join(f"- {n}" for n in obs.news))
        if obs.forum:
            parts.append("Forum posts from yesterday (anonymous, may be wrong or self-serving):\n"
                         + "\n".join(f"- {p}" for p in obs.forum))
        return "\n\n".join(parts)

    def decide_loan(self, obs: Observation) -> dict:
        if obs.max_new_loan <= 0 or not obs.allow_loans:
            return dict(NO_LOAN)
        rates = ", ".join(f"[{i}] {config.LOAN_TERMS_DAYS[i]} trading days at {r:.2%} annual"
                          for i, r in enumerate(obs.loan_rates))
        loans = "; ".join(f"{l['amount']:.0f} due day {l['due_day']}" for l in obs.loans) or "none"
        prompt = (f"{self._context(obs)}\n\n{render_state(obs)}\nExisting loans: {loans}.\n\nTASK: LOAN\n"
                  f"Decide whether to borrow. Options: {rates}. Interest is paid monthly; principal at maturity. "
                  f"Maximum new loan: {obs.max_new_loan:.2f}.\n"
                  'Return {"loan": "yes", "loan_type": 0, "amount": 1000} or {"loan": "no"}.')
        return self._ask_json(obs, prompt, validate_loan, NO_LOAN)

    def decide_order(self, obs: Observation) -> dict:
        prompt = (f"{self._context(obs)}\n\n{render_state(obs)}\n\nTASK: ORDER\n"
                  "Place at most one limit order, or none.\n"
                  'Return {"action_type": "buy"|"sell", "stock": "<ticker>", "amount": <int>, "price": <number>, '
                  '"reason": "<short>"} or {"action_type": "no"}.')
        return self._ask_json(obs, prompt, validate_order, NO_ORDER)

    def estimate(self, obs: Observation) -> dict:
        prompt = (f"{render_state(obs)}\n\nTASK: ESTIMATE\nWhich stocks do you expect to buy or sell tomorrow, "
                  'and will you borrow? Return {"buy": ["A"], "sell": [], "loan": "no"}.')
        return self._ask_json(obs, prompt, validate_estimate, {"buy": [], "sell": [], "loan": "no"})

    def post(self, obs: Observation) -> str:
        try:
            text = self._ask(obs, "TASK: POST\nThe trading day is over. Write a short public forum post "
                                  "(max 2 sentences) with your trading tips. Output only the post.")
        except LLMError:
            text = ""
        self.memory.append(f"Day {obs.day}: net_worth={obs.net_worth:,.0f}; posted: {text[:160]}")
        return text.strip()[:1000]


# ---------------------------------------------------------------------------
# Rule-based baselines (controls for the competition)
# ---------------------------------------------------------------------------
def _budget_qty(obs: Observation, stock: str, price: float, fraction: float) -> int:
    spend = max(0.0, obs.free_cash - config.FEE_MAX) * fraction
    return int(spend // price)


class FundamentalTrader(Policy):
    """Buys below the paper's ideal (DCF) price band, sells above it."""
    team = "baseline:fundamental"

    def __init__(self, rng: random.Random, margin: float = 0.02, fraction: float = 0.2):
        self.rng, self.margin, self.fraction = rng, margin, fraction

    def decide_order(self, obs):
        candidates = [s for s in obs.stocks if s in config.IDEAL_PRICE_UPPER]
        self.rng.shuffle(candidates)
        for s in candidates:
            fair = config.ideal_price_midpoint(s, obs.day)
            p = obs.prices[s]
            if p < fair * (1 - self.margin):
                price = round(min(fair, p * 1.02), 2)
                qty = _budget_qty(obs, s, price, self.fraction)
                if qty > 0:
                    return {"action_type": "buy", "stock": s, "amount": qty, "price": price}
            elif p > fair * (1 + self.margin) and obs.free_holdings.get(s, 0) > 0:
                price = round(max(fair, p * 0.98), 2)
                qty = max(1, int(obs.free_holdings[s] * self.fraction))
                return {"action_type": "sell", "stock": s, "amount": qty, "price": price}
        return dict(NO_ORDER)


class TrendFollower(Policy):
    team = "baseline:trend"

    def __init__(self, rng: random.Random, window: int = 5, fraction: float = 0.2):
        self.rng, self.window, self.fraction = rng, window, fraction

    def decide_order(self, obs):
        stocks = list(obs.stocks)
        self.rng.shuffle(stocks)
        for s in stocks:
            hist = obs.price_history.get(s, [])
            if len(hist) < self.window:
                continue
            ma = sum(hist[-self.window:]) / self.window
            p = obs.prices[s]
            if p > ma * 1.002:
                price = round(p * 1.01, 2)
                qty = _budget_qty(obs, s, price, self.fraction)
                if qty > 0:
                    return {"action_type": "buy", "stock": s, "amount": qty, "price": price}
            elif p < ma * 0.998 and obs.free_holdings.get(s, 0) > 0:
                return {"action_type": "sell", "stock": s, "amount": max(1, int(obs.free_holdings[s] * self.fraction)),
                        "price": round(p * 0.99, 2)}
        return dict(NO_ORDER)


class NoiseTrader(Policy):
    team = "baseline:noise"

    def __init__(self, rng: random.Random, fraction: float = 0.05):
        self.rng, self.fraction = rng, fraction

    def decide_order(self, obs):
        if self.rng.random() < 0.4:
            return dict(NO_ORDER)
        s = self.rng.choice(obs.stocks)
        p = obs.prices[s]
        price = round(p * (1 + self.rng.uniform(-0.02, 0.02)), 2)
        if self.rng.random() < 0.5 and obs.free_holdings.get(s, 0) > 0:
            return {"action_type": "sell", "stock": s,
                    "amount": max(1, int(obs.free_holdings[s] * self.fraction)), "price": price}
        qty = _budget_qty(obs, s, price, self.fraction)
        return {"action_type": "buy", "stock": s, "amount": qty, "price": price} if qty > 0 else dict(NO_ORDER)


class BuyAndHold(Policy):
    """Spends cash equally across all stocks once, then never trades."""
    team = "baseline:buy_and_hold"

    def __init__(self, rng: random.Random):
        self.done = set()

    def decide_order(self, obs):
        for s in obs.stocks:
            if s not in self.done:
                self.done.add(s)
                price = round(obs.prices[s] * 1.01, 2)
                remaining = len(obs.stocks) - len(self.done) + 1
                qty = _budget_qty(obs, s, price, 1.0 / remaining)
                if qty > 0:
                    return {"action_type": "buy", "stock": s, "amount": qty, "price": price}
        return dict(NO_ORDER)


BASELINES = {
    "fundamental": FundamentalTrader,
    "trend": TrendFollower,
    "noise": NoiseTrader,
    "buy_and_hold": BuyAndHold,
}


def make_policy(team: str, personality: str, rng: random.Random, competitive: bool = True,
                temperature: Optional[float] = None, clients: Optional[Dict[str, LLMClient]] = None) -> Policy:
    """``team`` is either ``baseline:<name>`` or an LLM model spec. LLM clients are shared per team."""
    if team.startswith("baseline:"):
        return BASELINES[team.split(":", 1)[1]](rng)
    clients = clients if clients is not None else {}
    if team not in clients:
        clients[team] = LLMClient(team, temperature=temperature)
    return LLMPolicy(clients[team], personality, competitive=competitive, team=team)


def describe(policy: Policy) -> str:
    return json.dumps({"team": policy.team, "personality": getattr(policy, "personality", None)})
