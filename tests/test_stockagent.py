import csv
import dataclasses
import json
import random

import pytest

from stockagent import config
from stockagent.agents import Account
from stockagent.config import SimConfig
from stockagent.league import run_league
from stockagent.live.brokers import VirtualPortfolio
from stockagent.live.feeds import Bar, CSVFeed
from stockagent.live.risk import RiskGate, RiskLimits
from stockagent.live.runner import PaperLeague, PromotionRules, live_step
from stockagent.llm import extract_json, parse_model_spec
from stockagent.market import Market, Order, transaction_fee
from stockagent.policies import Observation, validate_loan, validate_order
from stockagent.simulation import Simulation, parse_population


def accounts(n=2, cash=10_000.0, shares=100):
    return {i: Account(i, "t", "Balanced", cash, {"A": shares}) for i in range(n)}


# --- order book -------------------------------------------------------------
def test_crossing_orders_fill_at_resting_price_and_conserve():
    accts = accounts()
    m = Market(["A"], {"A": 10.0})
    m.submit(Order(0, "sell", "A", 50, 10.0), accts, 1, 1)
    trades = m.submit(Order(1, "buy", "A", 30, 10.5), accts, 1, 1)
    assert [(t.qty, t.price) for t in trades] == [(30, 10.0)]
    assert accts[1].holdings["A"] == 130 and accts[0].holdings["A"] == 70
    fee = transaction_fee(30)
    assert accts[1].cash == pytest.approx(10_000 - 300 - fee)
    assert accts[0].cash == pytest.approx(10_000 + 300 - fee)
    m.close_session(1, 1)
    assert m.prices["A"] == 10.0
    m.clear_books(accts)
    assert all(a.reserved_cash == 0 and a.reserved_shares.get("A", 0) == 0 for a in accts.values())


def test_non_crossing_orders_rest_and_price_time_priority():
    accts = accounts(3)
    m = Market(["A"], {"A": 10.0})
    m.submit(Order(0, "sell", "A", 10, 10.2), accts, 1, 1)
    m.submit(Order(1, "sell", "A", 10, 10.1), accts, 1, 1)
    assert m.books["A"].best_ask() == 10.1
    assert m.submit(Order(2, "buy", "A", 5, 10.0), accts, 1, 1) == []
    trades = m.submit(Order(2, "buy", "A", 15, 10.2), accts, 1, 1)
    assert [(t.seller, t.price, t.qty) for t in trades] == [(1, 10.1, 10), (0, 10.2, 5)]


def test_reserved_shares_prevent_double_selling():
    accts = accounts()
    m = Market(["A"], {"A": 10.0})
    m.submit(Order(0, "sell", "A", 100, 11.0), accts, 1, 1)
    assert accts[0].free_shares("A") == 0


# --- validation -------------------------------------------------------------
def obs(**kw):
    base = dict(day=1, session=1, sessions_per_day=3, stocks=["A", "B"], prices={"A": 10.0, "B": 20.0},
                price_history={}, books={}, cash=1000.0, free_cash=1000.0, holdings={"A": 5}, free_holdings={"A": 5},
                debt=0.0, loans=[], max_new_loan=500.0, loan_rates=config.LOAN_RATES, personality="Balanced",
                net_worth=1050.0, initial_wealth=1050.0)
    base.update(kw)
    return Observation(**base)


@pytest.mark.parametrize("raw,ok", [
    ({"action_type": "buy", "stock": "A", "amount": 50, "price": 10.1}, True),
    ({"action_type": "buy", "stock": "A", "amount": 500, "price": 10.1}, False),   # cash
    ({"action_type": "sell", "stock": "A", "amount": 6, "price": 10.0}, False),    # holdings
    ({"action_type": "buy", "stock": "C", "amount": 1, "price": 10.0}, False),     # unknown stock
    ({"action_type": "buy", "stock": "A", "amount": 1, "price": 101.0}, False),    # 10x typo
    ({"action_type": "no"}, True),
])
def test_validate_order(raw, ok):
    result, err = validate_order(raw, obs())
    assert (result is not None) == ok, err


def test_validate_loan_limits():
    assert validate_loan({"loan": "yes", "loan_type": 1, "amount": 400}, obs())[0]["amount"] == 400
    assert validate_loan({"loan": "yes", "loan_type": 3, "amount": 400}, obs())[0] is None
    assert validate_loan({"loan": "yes", "loan_type": 0, "amount": 600}, obs())[0] is None


def test_extract_json_handles_prose_and_fences():
    assert extract_json('I think so.\n```json\n{"action_type": "no"}\n```')["action_type"] == "no"
    assert extract_json('reasoning {not json} then {"loan": "no"}')["loan"] == "no"
    with pytest.raises(ValueError):
        extract_json("nothing here")


def test_parse_model_spec(monkeypatch):
    monkeypatch.delenv("YUNWU_API_KEY", raising=False)
    assert parse_model_spec("anthropic:claude-sonnet-4-6") == ("anthropic", "claude-sonnet-4-6")
    assert parse_model_spec("claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")
    assert parse_model_spec("deepseek-v4-flash")[0] == "deepseek"
    monkeypatch.setenv("YUNWU_API_KEY", "x")
    assert parse_model_spec("gpt-5.5") == ("yunwu", "gpt-5.5")


# --- loans ------------------------------------------------------------------
def test_loan_interest_monthly_principal_at_maturity():
    a = Account(0, "t", "Balanced", 0.0, {})
    a.take_loan(12_000, 0, 1, [0.03, 0.03, 0.03])
    assert a.cash == 12_000 and a.loans[0].due_day == 23
    assert a.pay_interest() == pytest.approx(30.0)
    assert a.repay_matured(23) == 12_000 and a.loans == []


# --- full simulation ----------------------------------------------------------
def test_simulation_conserves_shares_and_cash(tmp_path):
    cfg = SimConfig(days=14, seed=7, out_dir=str(tmp_path))  # covers the day-12 report
    pop = parse_population("mock:t=5,baseline:fundamental=5,baseline:trend=5,baseline:noise=5,baseline:buy_and_hold=5")
    sim = Simulation(cfg, pop, workers=1)
    shares0 = {s: sum(a.holdings.get(s, 0) for a in sim.accounts.values()) for s in cfg.stocks}
    cash0 = sum(a.cash for a in sim.accounts.values())
    for d in range(1, cfg.days + 1):
        sim.run_day(d)
        for a in sim.accounts.values():
            assert a.reserved_cash == pytest.approx(0) and all(v == 0 for v in a.reserved_shares.values())
            assert all(q >= 0 for q in a.holdings.values())
    shares1 = {s: sum(a.holdings.get(s, 0) for a in sim.accounts.values()) for s in cfg.stocks}
    assert shares0 == shares1
    fees = sum(a.fees_paid for a in sim.accounts.values())
    interest = sum(a.interest_paid for a in sim.accounts.values())
    cash1 = sum(a.cash for a in sim.accounts.values())   # initial loans mature on day 22 or later
    assert cash1 == pytest.approx(cash0 - fees - interest, rel=1e-9)
    summary = sim.finish()
    assert summary["trades"] > 0
    news = (tmp_path / "news.csv").read_text()
    assert "quarterly report" in news
    assert len(summary["teams"]) == 5


def test_mirrored_endowments(tmp_path):
    sim = Simulation(SimConfig(days=1, out_dir=str(tmp_path)), [("baseline:noise", 3), ("baseline:trend", 3)])
    a = [sim.accounts[i] for i in range(3)]
    b = [sim.accounts[i] for i in range(3, 6)]
    assert [(x.cash, x.holdings, x.personality) for x in a] == [(x.cash, x.holdings, x.personality) for x in b]


def test_league_qualification(tmp_path):
    cfg = SimConfig(days=4, out_dir=str(tmp_path))
    report = run_league(cfg, [("mock:t", 4), ("baseline:noise", 4), ("baseline:trend", 4)], [1, 2], workers=1)
    assert {t["team"] for t in report["leaderboard"]} == {"mock:t", "baseline:noise", "baseline:trend"}
    assert (tmp_path / "league.md").exists()
    mock_row = next(t for t in report["leaderboard"] if t["team"] == "mock:t")
    assert mock_row["excess_vs_best_baseline_ci95"] is not None


# --- paper / live -------------------------------------------------------------
def write_csv(path, days=50, symbols=("AAA", "BBB")):
    rng = random.Random(3)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "open", "high", "low", "close", "volume"])
        for sym in symbols:
            px = 100.0
            for d in range(days):
                o = px
                c = px * (1 + rng.uniform(-0.02, 0.025))
                w.writerow([f"2026-{1 + d // 28:02d}-{d % 28 + 1:02d}", sym, o, max(o, c) * 1.01, min(o, c) * 0.99, c, 1000])
                px = c


def test_virtual_portfolio_no_lookahead_fills():
    pf = VirtualPortfolio(cash=10_000)
    pf.queue({"action_type": "buy", "stock": "X", "amount": 10, "price": 100.0})
    pf.queue({"action_type": "buy", "stock": "X", "amount": 10, "price": 90.0})
    fills = pf.settle("d2", {"X": Bar("d2", 101, 105, 95, 104, 0)})
    assert [(f["qty"], f["price"]) for f in fills] == [(10, 100.0)]   # limit hit intraday; 90 never reached
    assert pf.pending == []


def test_risk_gate_limits_and_kill_switch():
    gate = RiskGate(RiskLimits(budget=10_000, max_order_pct=0.1, max_position_pct=0.25))
    gate.start_day(10_000)
    o, why = gate.check({"action_type": "buy", "stock": "X", "amount": 100, "price": 50}, 10_000, 10_000, {}, {"X": 50})
    assert o["amount"] == 20 and "reduced" in why
    assert gate.check({"action_type": "sell", "stock": "X", "amount": 5, "price": 50}, 10_000, 10_000, {}, {"X": 50})[0] is None
    gate.start_day(8_000)  # 20% drawdown
    assert gate.state.halted
    assert gate.check({"action_type": "buy", "stock": "X", "amount": 1, "price": 50}, 8_000, 8_000, {}, {"X": 50})[0] is None


def test_paper_league_and_promotion(tmp_path):
    path = tmp_path / "bars.csv"
    write_csv(path)
    feed = CSVFeed(str(path), anonymize=True)
    assert feed.symbols == ["S1", "S2"]
    state = str(tmp_path / "league.json")
    league = PaperLeague(state, feed, ["mock:a@Aggressive", "mock:b"], capital=50_000)
    for d in league.pending_dates():
        board = league.step(d)
    assert board["days"] == 50
    assert {r["candidate"] for r in board["rows"]} == {"mock:a@Aggressive", "mock:b", "baseline:buy_and_hold"}
    # resumable from disk
    again = PaperLeague(state, feed)
    assert again.pending_dates() == []
    champion = again.promote(PromotionRules(min_days=10, min_sharpe=-99, max_drawdown=-1, min_excess_return=-99),
                             str(tmp_path / "champion.json"))
    assert champion["status"] == "awaiting_approval" and champion["approved_by"] is None
    assert not champion["candidate"].startswith("baseline:")


class FakeBroker:
    def __init__(self, live, equity=10_000):
        self.live, self.equity, self.orders = live, equity, []

    def account(self):
        return {"cash": self.equity, "equity": self.equity, "buying_power": self.equity}

    def positions(self):
        return {}

    def cancel_all(self):
        pass

    def submit_limit(self, symbol, side, qty, limit, client_id):
        self.orders.append((symbol, side, qty, limit))
        return {"id": client_id}


def test_live_requires_signoff(tmp_path, monkeypatch):
    path = tmp_path / "bars.csv"
    write_csv(path, days=25)
    feed = CSVFeed(str(path))
    champ = tmp_path / "champion.json"
    champ.write_text(json.dumps({"candidate": "mock:a", "approved_by": None, "approved_budget": None}))
    with pytest.raises(PermissionError):
        live_step(str(champ), str(tmp_path / "s.json"), feed, FakeBroker(live=True), 10_000, lambda o: True)
    champ.write_text(json.dumps({"candidate": "mock:a", "approved_by": "me", "approved_budget": 10_000}))
    monkeypatch.delenv("STOCKAGENT_LIVE", raising=False)
    with pytest.raises(PermissionError):
        live_step(str(champ), str(tmp_path / "s.json"), feed, FakeBroker(live=True), 10_000, lambda o: True)
    monkeypatch.setenv("STOCKAGENT_LIVE", "1")
    with pytest.raises(PermissionError):   # account much larger than budget
        live_step(str(champ), str(tmp_path / "s.json"), feed, FakeBroker(live=True, equity=1e6), 10_000, lambda o: True)
    broker = FakeBroker(live=True)
    live_step(str(champ), str(tmp_path / "s.json"), feed, broker, 10_000, lambda o: True)
    for sym, side, qty, limit in broker.orders:
        assert qty * limit <= 10_000 * 0.10 + 1e-6


# --- IBKR adapter (fake connection; no gateway needed) ----------------------------
from types import SimpleNamespace as NS  # noqa: E402

from stockagent.live.ibkr import IBKRBroker, IBKRFeed, is_paper_account  # noqa: E402


class FakeIB:
    def __init__(self, account="DU123", equity=10_000.0):
        self.account, self.equity = account, equity
        self.placed, self.cancelled = [], []
        self.open = [NS(order=NS(account=account, orderRef="sa-old", orderId=1)),
                     NS(order=NS(account=account, orderRef="manual", orderId=2))]

    def managedAccounts(self):
        return [self.account]

    def accountSummary(self, account):
        return [NS(tag="NetLiquidation", value=str(self.equity), currency="USD"),
                NS(tag="TotalCashValue", value=str(self.equity), currency="USD"),
                NS(tag="AvailableFunds", value=str(self.equity), currency="USD")]

    def positions(self, account):
        return [NS(contract=NS(symbol="SPY", secType="STK"), position=3.0)]

    def openTrades(self):
        return self.open

    def cancelOrder(self, order):
        self.cancelled.append(order.orderRef)

    def qualifyContracts(self, c):
        return [c]

    def placeOrder(self, contract, order):
        self.placed.append((contract.symbol, order.action, order.totalQuantity, order.lmtPrice, order.tif,
                            order.orderRef, order.account))
        return NS(order=NS(orderId=99), orderStatus=NS(status="Submitted"))

    def sleep(self, s):
        pass

    def reqHistoricalData(self, contract, **kw):
        import datetime as _dt
        return [NS(date=_dt.date(2026, 1, d), open=1, high=2, low=0.5, close=1.5, volume=10) for d in (2, 5, 6)]


def test_ibkr_paper_live_account_guard():
    assert is_paper_account("DU1234567") and not is_paper_account("U1234567")
    with pytest.raises(PermissionError):
        IBKRBroker(live=False, ib=FakeIB("U999"))   # paper mode must not touch a live account
    with pytest.raises(PermissionError):
        IBKRBroker(live=True, ib=FakeIB("DU999"))   # real-money mode on a paper login is a mistake
    with pytest.raises(RuntimeError):
        IBKRBroker(live=False, ib=FakeIB("DU1"), account="DU2")


def test_ibkr_broker_orders_and_cancel():
    pytest.importorskip("ib_async")
    ib = FakeIB()
    b = IBKRBroker(live=False, ib=ib)
    assert b.account()["equity"] == 10_000 and b.positions() == {"SPY": 3}
    b.cancel_all()
    assert ib.cancelled == ["sa-old"]           # manual orders are left alone
    res = b.submit_limit("SPY", "buy", 5, 101.234, "sa-x")
    assert res == {"id": 99, "status": "Submitted"}
    assert ib.placed == [("SPY", "BUY", 5, 101.23, "DAY", "sa-x", "DU123")]


def test_ibkr_feed_and_live_step(tmp_path, monkeypatch):
    pytest.importorskip("ib_async")
    ib = FakeIB()
    feed = IBKRFeed(["SPY"], ib=ib)
    assert feed.dates() == ["2026-01-02", "2026-01-05", "2026-01-06"]
    champ = tmp_path / "champion.json"
    champ.write_text(json.dumps({"candidate": "mock:a", "approved_by": None, "approved_budget": None}))
    live_step(str(champ), str(tmp_path / "s.json"), feed, IBKRBroker(live=False, ib=ib), 10_000, lambda o: True)
    assert all(p[6] == "DU123" and p[2] * p[3] <= 1_000 + 1e-6 for p in ib.placed)


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    from stockagent.config import env, load_dotenv
    f = tmp_path / ".env"
    f.write_text("# c\nIBKR_ACCOUNT=DUX000001\nIBKR_PORT='4002'\n")
    monkeypatch.delenv("IBKR_ACCOUNT", raising=False)
    monkeypatch.setenv("IBKR_PORT", "7497")
    load_dotenv(str(f))
    assert env("IBKR_ACCOUNT") == "DUX000001" and env("IBKR_PORT") == "7497"
    assert is_paper_account(env("IBKR_ACCOUNT"))
    monkeypatch.delenv("IBKR_ACCOUNT")
