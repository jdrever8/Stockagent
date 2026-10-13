"""The ISA agent game: several agents, £10,000 each, live prices, tracked on paper.

House rules (set by the owner): ISA-eligible instruments only, no margin, no
shorting, no loans; an agent whose funds reach zero loses. Everything else,
including strategy, trade count and sizing, is up to each agent.

Systematic agents are code (below) and trade on every run. Discretionary agents
are run by the operating AI agent, which researches and then trades their books with
``python main.py game trade <agent> ...``, writing its reasoning into the note
and the agent's journal.
"""
from __future__ import annotations

import math
import os
import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .book import Book, fetch_history, fetch_quotes, now

GAME_DIR = os.path.join("paper", "game")
START_CAPITAL = 10_000.0


@dataclass
class AgentSpec:
    name: str
    kind: str          # "systematic" | "discretionary"
    strategy: str


ROSTER: List[AgentSpec] = [
    AgentSpec("momentum", "systematic",
              "Relative momentum across UCITS ETFs (US, UK, Europe, Japan, EM, gold, gilts, property): hold the "
              "top 3 by 6-month return skipping the last week, equal weight, only if positive; otherwise short gilts."),
    AgentSpec("trend", "systematic",
              "Trend filter: all-in a global all-cap tracker while it is above its 200-day average, "
              "otherwise all-in short-dated gilts."),
    AgentSpec("reversion", "systematic",
              "Short-term mean reversion in FTSE 100 blue chips: buy 2-day RSI < 10 dips in stocks above their "
              "200-day average (max 5 at 20%), sell when the close recovers above the 5-day average."),
    AgentSpec("passive", "systematic",
              "Buy a global all-cap tracker with everything on day one and never trade again (the control)."),
    AgentSpec("macro", "discretionary",
              "Top-down macro: rates, inflation, growth, central banks and FX drive the allocation across equity "
              "regions, sectors, gilts and gold."),
    AgentSpec("quality", "discretionary",
              "Bottom-up fundamentals: a concentrated portfolio of high return-on-capital UK and US businesses "
              "bought at reasonable valuations, held while the thesis holds."),
    AgentSpec("chartist", "discretionary",
              "Technical swing trading: breakouts, support and resistance, moving averages, volume and momentum "
              "divergences, with disciplined exits."),
]

GLOBAL_TRACKER = "VWRP.L"   # Vanguard FTSE All-World UCITS (accumulating, GBP line)
SAFE = "IGLS.L"             # iShares UK Gilts 0-5yr UCITS
MOMENTUM_UNIVERSE = ["VUSA.L", "VUKE.L", "VMID.L", "VERX.L", "VJPN.L", "VFEM.L", "SGLN.L", "IGLT.L", "IUKP.L"]
REVERSION_UNIVERSE = ["AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "DGE.L", "REL.L",
                      "LSEG.L", "BATS.L", "NG.L"]


def book_path(name: str, root: str = GAME_DIR) -> str:
    return os.path.join(root, f"{name}.json")


def journal_path(name: str, root: str = GAME_DIR) -> str:
    return os.path.join(root, "journal", f"{name}.md")


def init_game(root: str = GAME_DIR, capital: float = START_CAPITAL) -> List[str]:
    created = []
    os.makedirs(os.path.join(root, "journal"), exist_ok=True)
    for spec in ROSTER:
        path = book_path(spec.name, root)
        if os.path.exists(path):
            continue
        Book.init(path, capital, "GBP", owner=spec.name, isa=True, strategy=spec.strategy)
        with open(journal_path(spec.name, root), "w", encoding="utf-8") as fh:
            fh.write(f"# {spec.name} ({spec.kind})\n\nStrategy: {spec.strategy}\n\nStarted {now()} with "
                     f"£{capital:,.0f}.\n")
        created.append(spec.name)
    return created


def journal(name: str, text: str, root: str = GAME_DIR) -> None:
    with open(journal_path(name, root), "a", encoding="utf-8") as fh:
        fh.write(f"\n## {now()}\n{text.strip()}\n")


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def sma(xs: List[float], n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(xs: List[float], n: int = 2) -> Optional[float]:
    if len(xs) < n + 1:
        return None
    changes = [b - a for a, b in zip(xs[-n - 1:-1], xs[-n:])]
    gain = sum(c for c in changes if c > 0) / n
    loss = -sum(c for c in changes if c < 0) / n
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


# ---------------------------------------------------------------------------
# Systematic strategies: each returns ({symbol: target weight}, reasoning, instrument)
# ---------------------------------------------------------------------------
History = Callable[[str], List[float]]


def strat_momentum(hist: History, book: Book) -> Tuple[Dict[str, float], str, str]:
    scores = {}
    for sym in MOMENTUM_UNIVERSE:
        closes = hist(sym)
        if len(closes) >= 132:
            scores[sym] = closes[-6] / closes[-132] - 1
    ranked = sorted(scores, key=scores.get, reverse=True)
    picks = [s for s in ranked[:3] if scores[s] > 0]
    why = "6m-1w returns: " + ", ".join(f"{s} {scores[s]:+.1%}" for s in ranked)
    if not picks:
        return {SAFE: 1.0}, why + " -> nothing positive, hold short gilts", "etf"
    return {s: 1 / len(picks) for s in picks}, why + f" -> hold {picks}", "etf"


def strat_trend(hist: History, book: Book) -> Tuple[Dict[str, float], str, str]:
    closes = hist(GLOBAL_TRACKER)
    avg = sma(closes, 200)
    if avg is None:
        return {}, "not enough history for a 200-day average", "etf"
    last = closes[-1]
    if last > avg:
        return {GLOBAL_TRACKER: 1.0}, f"{GLOBAL_TRACKER} {last:.2f} above 200d {avg:.2f}: risk on", "etf"
    return {SAFE: 1.0}, f"{GLOBAL_TRACKER} {last:.2f} below 200d {avg:.2f}: risk off", "etf"


def strat_reversion(hist: History, book: Book) -> Tuple[Dict[str, float], str, str]:
    held = set(book.data["positions"])
    keep, notes = {}, []
    for sym in held:
        closes = hist(sym)
        m5 = sma(closes, 5)
        if m5 is not None and closes[-1] > m5:
            notes.append(f"exit {sym}: {closes[-1]:.2f} > 5d {m5:.2f}")
        else:
            keep[sym] = 0.2
    candidates = []
    for sym in REVERSION_UNIVERSE:
        if sym in keep:
            continue
        closes = hist(sym)
        m200, r = sma(closes, 200), rsi(closes, 2)
        if m200 is not None and r is not None and closes[-1] > m200 and r < 10:
            candidates.append((r, sym))
    for r, sym in sorted(candidates)[: max(0, 5 - len(keep))]:
        keep[sym] = 0.2
        notes.append(f"enter {sym}: RSI2 {r:.1f}, above 200d")
    return keep, "; ".join(notes) or "no signals", "share"


def strat_passive(hist: History, book: Book) -> Tuple[Dict[str, float], str, str]:
    if book.data["trades"]:
        return {s: None for s in book.data["positions"]}, "hold forever", "etf"   # None = leave as is
    return {GLOBAL_TRACKER: 1.0}, "day one: buy the world and hold", "etf"


STRATEGIES = {"momentum": strat_momentum, "trend": strat_trend, "reversion": strat_reversion,
              "passive": strat_passive}


# ---------------------------------------------------------------------------
# Rebalancing (whole shares, sells before buys, 10% tolerance band to avoid churn)
# ---------------------------------------------------------------------------
def rebalance(book: Book, targets: Dict[str, Optional[float]], prices: Dict[str, float], fx: Dict[str, float],
              instrument: str, reason: str, trade: Optional[Callable] = None, tolerance: float = 0.10) -> List[dict]:
    trade = trade or book.trade
    d = book.data
    pos = d["positions"]
    equity = d["cash"] + sum(p["qty"] * prices[s] * fx[s] for s, p in pos.items())
    budget = equity * 0.995  # leave room for stamp duty / FX costs
    done = []
    # sells: exits and trims
    for sym, p in list(pos.items()):
        w = targets.get(sym, 0.0)
        if w is None:
            continue
        unit = prices[sym] * fx[sym]
        target_qty = math.floor(budget * w / unit)
        if p["qty"] - target_qty > 0 and (w == 0 or (p["qty"] - target_qty) * unit > tolerance * budget * w):
            done.append(trade("sell", sym, p["qty"] - target_qty, note=reason, instrument=p.get("instrument", instrument)))
    # buys
    for sym, w in targets.items():
        if not w:
            continue
        unit = prices[sym] * fx[sym]
        have = pos.get(sym, {}).get("qty", 0.0)
        target_qty = math.floor(budget * w / unit)
        gap = target_qty - have
        if gap > 0 and (have == 0 or gap * unit > tolerance * budget * w):
            affordable = math.floor((d["cash"] * 0.99) / unit)
            qty = min(gap, affordable)
            if qty > 0:
                done.append(trade("buy", sym, qty, note=reason, instrument=instrument))
    return done


def run_systematic(root: str = GAME_DIR, history_fn=None, quotes_fn=None) -> Dict[str, dict]:
    history_fn = history_fn or (lambda s: [c for _, c in fetch_history(s)[0]])
    quotes_fn = quotes_fn or fetch_quotes
    cache: Dict[str, List[float]] = {}
    failed: set = set()

    def hist(sym: str) -> List[float]:
        if sym not in cache:
            try:
                cache[sym] = history_fn(sym)
            except Exception:
                cache[sym] = []
            if not cache[sym]:
                failed.add(sym)
        return cache[sym]

    results = {}
    for spec in ROSTER:
        if spec.kind != "systematic":
            continue
        book = Book(book_path(spec.name, root))
        if book.data.get("eliminated"):
            results[spec.name] = {"status": "eliminated"}
            continue
        try:
            failed.clear()
            targets, why, instrument = STRATEGIES[spec.name](hist, book)
            if failed:   # never trade (or report "no signal") on missing data
                raise RuntimeError(f"no price history for {sorted(failed)}")
            symbols = sorted(set(targets) | set(book.data["positions"]))
            quotes = quotes_fn(symbols + ["GBPUSD"]) if symbols else {}
            missing = [s for s in symbols if s not in quotes]
            if missing:
                raise RuntimeError(f"no live quote for {missing}")
            fx = {s: book.fx_to_base(quotes[s].currency, quotes)[0] for s in symbols}
            trades = rebalance(book, targets, {s: quotes[s].price for s in symbols}, fx, instrument, why)
            journal(spec.name, f"Signal: {why}\nTrades: " + (", ".join(
                f"{t['side']} {t['qty']} {t['symbol']} @ {t['price']}" for t in trades) or "none"), root)
            results[spec.name] = {"status": "ok", "signal": why, "trades": len(trades)}
        except Exception as exc:   # one agent's data problem must not stop the others
            results[spec.name] = {"status": "error", "error": str(exc)}
    return results


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def leaderboard(root: str = GAME_DIR, record: bool = True) -> Tuple[List[dict], str]:
    rows = []
    for spec in ROSTER:
        path = book_path(spec.name, root)
        if not os.path.exists(path):
            continue
        try:
            m = Book(path).mark(record=record)
        except Exception as exc:
            m = {"equity": float("nan"), "return": float("nan"), "cash": float("nan"), "positions": [],
                 "trades": 0, "eliminated": None, "unpriced": [str(exc)]}
        rows.append({"agent": spec.name, "kind": spec.kind, **m})
    rows.sort(key=lambda r: (r["equity"] if not math.isnan(r["equity"]) else -1), reverse=True)
    lines = [f"# ISA agent game: leaderboard", "", f"Updated {now()} (GBP, live prices)", "",
             "| # | agent | kind | equity | return | cash | holdings | trades | status |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        hold = ", ".join(p["symbol"] for p in r["positions"]) or "-"
        status = "ELIMINATED" if r.get("eliminated") else ("unpriced: " + ", ".join(r["unpriced"]) if r["unpriced"] else "active")
        lines.append(f"| {i} | {r['agent']} | {r['kind']} | £{r['equity']:,.2f} | {r['return']:+.2%} | "
                     f"£{r['cash']:,.2f} | {hold} | {r['trades']} | {status} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(root, "LEADERBOARD.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return rows, text
