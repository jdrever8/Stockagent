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

import json
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
    AgentSpec("contrarian", "discretionary",
              "Buy what the crowd hates: assets and sound businesses after heavy de-ratings, peak-pessimism "
              "downgrades or capitulation, avoiding genuinely broken balance sheets; sell into recovery once "
              "the consensus has come round."),
    AgentSpec("speculative", "discretionary",
              "Asymmetric, high-volatility bets for outsized returns: leveraged ETPs, high-beta growth and "
              "momentum names, bitcoin-proxy equities and small caps. Concentrated, cuts losers fast, lets "
              "winners run, rotates into whatever has the most upside."),
]

GLOBAL_TRACKER = "VWRP.L"   # Vanguard FTSE All-World UCITS (accumulating, GBP line); passive's holding
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


# Tunable parameters. Agents may change these through their own daily rulebook amendments.
DEFAULT_PARAMS: Dict[str, Dict[str, object]] = {
    "momentum": {"universe": MOMENTUM_UNIVERSE, "safe": SAFE, "top_n": 3, "lookback": 126, "skip": 5,
                 "min_score": 0.0, "tolerance": 0.10},
    "reversion": {"universe": REVERSION_UNIVERSE, "rsi_len": 2, "rsi_entry": 10.0, "trend_len": 200,
                  "exit_len": 5, "max_positions": 5, "stop_loss": None, "tolerance": 0.10},
    "passive": {"holding": GLOBAL_TRACKER, "tolerance": 0.10},
}


def strat_momentum(hist: History, book: Book, p: dict) -> Tuple[Dict[str, float], str, str]:
    lookback, skip = int(p["lookback"]), int(p["skip"])
    scores = {}
    for sym in p["universe"]:
        closes = hist(sym)
        if len(closes) >= lookback + skip + 1:
            scores[sym] = closes[-1 - skip] / closes[-1 - skip - lookback] - 1
    ranked = sorted(scores, key=scores.get, reverse=True)
    picks = [s for s in ranked[: int(p["top_n"])] if scores[s] > float(p["min_score"])]
    why = f"{lookback}d return skipping {skip}d: " + ", ".join(f"{s} {scores[s]:+.1%}" for s in ranked)
    if not picks:
        return {p["safe"]: 1.0}, why + " -> nothing qualifies, hold the safe asset", "etf"
    return {s: 1 / len(picks) for s in picks}, why + f" -> hold {picks}", "etf"


def strat_reversion(hist: History, book: Book, p: dict) -> Tuple[Dict[str, float], str, str]:
    weight = 1.0 / int(p["max_positions"])
    keep, notes = {}, []
    for sym, pos in book.data["positions"].items():
        closes = hist(sym)
        m_exit = sma(closes, int(p["exit_len"]))
        avg_cost = pos["cost_base"] / pos["qty"] if pos["qty"] else 0
        if p.get("stop_loss") and closes and avg_cost and closes[-1] < avg_cost * (1 - float(p["stop_loss"])):
            notes.append(f"stop {sym}: {closes[-1]:.2f} < cost {avg_cost:.2f} -{float(p['stop_loss']):.0%}")
        elif m_exit is not None and closes[-1] > m_exit:
            notes.append(f"exit {sym}: {closes[-1]:.2f} > {p['exit_len']}d {m_exit:.2f}")
        else:
            keep[sym] = weight
    candidates = []
    for sym in p["universe"]:
        if sym in keep:
            continue
        closes = hist(sym)
        m_trend, r = sma(closes, int(p["trend_len"])), rsi(closes, int(p["rsi_len"]))
        if m_trend is not None and r is not None and closes[-1] > m_trend and r < float(p["rsi_entry"]):
            candidates.append((r, sym))
    for r, sym in sorted(candidates)[: max(0, int(p["max_positions"]) - len(keep))]:
        keep[sym] = weight
        notes.append(f"enter {sym}: RSI{p['rsi_len']} {r:.1f}, above {p['trend_len']}d")
    return keep, "; ".join(notes) or "no signals", "share"


def strat_passive(hist: History, book: Book, p: dict) -> Tuple[Dict[str, float], str, str]:
    if book.data["trades"]:
        return {s: None for s in book.data["positions"]}, "hold forever", "etf"   # None = leave as is
    return {p["holding"]: 1.0}, "day one: buy and hold", "etf"


STRATEGIES = {"momentum": strat_momentum, "reversion": strat_reversion,
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
            params = active_params(spec.name, root)
            targets, why, instrument = STRATEGIES[spec.name](hist, book, params)
            if failed:   # never trade (or report "no signal") on missing data
                raise RuntimeError(f"no price history for {sorted(failed)}")
            symbols = sorted(set(targets) | set(book.data["positions"]))
            quotes = quotes_fn(symbols + ["GBPUSD"]) if symbols else {}
            missing = [s for s in symbols if s not in quotes]
            if missing:
                raise RuntimeError(f"no live quote for {missing}")
            fx = {s: book.fx_to_base(quotes[s].currency, quotes)[0] for s in symbols}
            trades = rebalance(book, targets, {s: quotes[s].price for s in symbols}, fx, instrument, why,
                               tolerance=float(params.get("tolerance", 0.10)))
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


# ---------------------------------------------------------------------------
# Agents' own rulebooks (owner's grant: from 14 Oct 2026, once a day, each agent may
# create rules that help it gain an advantage). The owner's house rules are not part
# of any rulebook: the ledger enforces them, so no agent rule can override them.
# ---------------------------------------------------------------------------
RULES_START = "2026-10-14"


def london_today() -> str:
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def rules_path(name: str, root: str = GAME_DIR) -> str:
    return os.path.join(root, "rules", f"{name}.json")


def load_rules(name: str, root: str = GAME_DIR) -> dict:
    path = rules_path(name, root)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"agent": name, "amendments": [], "active": []}


def active_params(name: str, root: str = GAME_DIR) -> dict:
    params = dict(DEFAULT_PARAMS.get(name, {}))
    for rule in load_rules(name, root)["active"]:
        params.update(rule.get("params") or {})
    return params


def amend_rules(name: str, changes: List[dict], root: str = GAME_DIR, today: Optional[str] = None) -> dict:
    """Apply one day's amendment: a list of {"op": "add"|"retire", "text", "params", "rationale", "id"}."""
    spec = next((a for a in ROSTER if a.name == name), None)
    if spec is None:
        raise ValueError(f"unknown agent {name}")
    today = today or london_today()
    if today < RULES_START:
        raise PermissionError(f"agents may write their own rules from {RULES_START}")
    book = Book(book_path(name, root))
    if book.data.get("eliminated"):
        raise PermissionError(f"{name} is eliminated")
    rules = load_rules(name, root)
    if any(a["date"] == today for a in rules["amendments"]):
        raise PermissionError(f"{name} has already amended its rules today ({today}); once a day")
    if not changes:
        raise ValueError("an amendment needs at least one change")
    active = {r["id"]: r for r in rules["active"]}
    next_id = 1 + max([int(r["id"].split("-")[1]) for a in rules["amendments"] for r in a["changes"]
                       if r.get("op") == "add"] or [0])
    applied = []
    for ch in changes:
        op = ch.get("op")
        if op == "add":
            if not ch.get("text") or not ch.get("rationale"):
                raise ValueError("a new rule needs text and a rationale (the advantage it is meant to give)")
            params = ch.get("params") or {}
            if params:
                if spec.kind != "systematic":
                    raise ValueError("only systematic agents have code parameters; write the rule as text")
                unknown = set(params) - set(DEFAULT_PARAMS[name])
                if unknown:
                    raise ValueError(f"unknown parameters {sorted(unknown)}; allowed {sorted(DEFAULT_PARAMS[name])}")
            rid = f"{name[:3].upper()}-{next_id}"
            next_id += 1
            rule = {"id": rid, "adopted": today, "text": ch["text"], "params": params, "rationale": ch["rationale"]}
            active[rid] = rule
            applied.append(dict(rule, op="add"))
        elif op == "retire":
            rid = ch.get("id")
            if rid not in active:
                raise ValueError(f"no active rule {rid}")
            del active[rid]
            applied.append({"op": "retire", "id": rid, "rationale": ch.get("rationale", "")})
        else:
            raise ValueError("op must be add or retire (retire then add to amend)")
    rules["amendments"].append({"date": today, "time": now(), "changes": applied})
    rules["active"] = list(active.values())
    os.makedirs(os.path.dirname(rules_path(name, root)), exist_ok=True)
    with open(rules_path(name, root), "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=1)
    journal(name, "Rulebook amendment:\n" + "\n".join(
        f"- {c['op']} {c.get('id', '')}: {c.get('text', '')} {c.get('params') or ''} (why: {c.get('rationale', '')})"
        for c in applied), root)
    if spec.kind == "systematic":   # make sure the new parameters actually run
        _ = active_params(name, root)
    return rules


def render_rulebooks(root: str = GAME_DIR) -> str:
    lines = ["# Agents' own rules", "",
             f"From {RULES_START}, once a day, each agent may add or retire its own rules to gain an advantage.",
             "The owner's house rules (RULES.md) are enforced by the ledger and cannot be overridden.", ""]
    for spec in ROSTER:
        rules = load_rules(spec.name, root)
        lines.append(f"## {spec.name} ({spec.kind})")
        if not rules["active"]:
            lines.append("_No rules of its own yet._")
        for r in rules["active"]:
            p = f" `{json.dumps(r['params'])}`" if r.get("params") else ""
            lines.append(f"- **{r['id']}** (since {r['adopted']}): {r['text']}{p}. _Why: {r['rationale']}_")
        lines.append("")
    text = "\n".join(lines)
    os.makedirs(os.path.join(root, "rules"), exist_ok=True)
    with open(os.path.join(root, "rules", "RULEBOOKS.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
