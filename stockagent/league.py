"""Stage 1 of the pipeline: the simulated league.

Every team (an LLM model spec or a baseline) trades in the same fictional
market, over several seeds, with mirrored starting portfolios. A team qualifies
for paper trading only if it beats every baseline with statistical confidence.
"""
from __future__ import annotations

import dataclasses
import os
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from . import metrics
from .config import SimConfig
from .records import Recorder
from .simulation import Simulation


@dataclasses.dataclass
class QualifyRules:
    min_excess_return_ci_low: float = 0.0   # lower 95% CI bound of (team - best baseline) per-agent return
    max_bankrupt_rate: float = 0.05
    max_invalid_per_agent: float = 5.0


def run_league(cfg: SimConfig, population: Sequence[Tuple[str, int]], seeds: Sequence[int],
               rules: QualifyRules = QualifyRules(), temperature: Optional[float] = None,
               workers: int = 8) -> dict:
    root = cfg.out_dir
    all_rows: List[dict] = []
    per_seed = []
    for seed in seeds:
        run_cfg = dataclasses.replace(cfg, seed=seed, out_dir=os.path.join(root, f"seed_{seed}"))
        result = Simulation(run_cfg, population, temperature=temperature, workers=workers).run()
        all_rows.extend(result["agents"])
        per_seed.append({"seed": seed, "teams": result["teams"], "price_fidelity": result["price_fidelity"],
                         "llm_usage": result["llm_usage"]})

    table = metrics.team_summary(all_rows)
    baselines = [t for t in table if t["team"].startswith("baseline:")]
    best_baseline = max(baselines, key=lambda t: t["mean_return"]) if baselines else None

    # Paired comparison: same seed + same endowment slot => same starting portfolio.
    by_key: Dict[Tuple[str, int, int], float] = {}
    slot: Dict[Tuple[str, int], int] = {}
    for r in all_rows:
        k = (r["team"], r["seed"])
        slot[k] = slot.get(k, -1) + 1
        by_key[(r["team"], r["seed"], slot[k])] = r["return"]

    for t in table:
        t["qualified"] = False
        t["excess_vs_best_baseline_ci95"] = None
        if t["team"].startswith("baseline:") or best_baseline is None:
            continue
        diffs = [ret - by_key[(best_baseline["team"], seed, j)]
                 for (team, seed, j), ret in by_key.items()
                 if team == t["team"] and (best_baseline["team"], seed, j) in by_key]
        if diffs:
            lo, hi = metrics.bootstrap_ci(diffs)
            t["excess_vs_best_baseline_ci95"] = [lo, hi]
            t["qualified"] = (lo > rules.min_excess_return_ci_low
                              and t["bankrupt_rate"] <= rules.max_bankrupt_rate
                              and t["invalid_per_agent"] <= rules.max_invalid_per_agent)

    report = {
        "seeds": list(seeds),
        "population": list(population),
        "best_baseline": best_baseline["team"] if best_baseline else None,
        "leaderboard": table,
        "qualified": [t["team"] for t in table if t["qualified"]],
        "per_seed": per_seed,
        "rules": dataclasses.asdict(rules),
    }
    rec = Recorder(root)
    rec.json("league", report)
    rec.text("league.md", render_leaderboard(report))
    return report


def render_leaderboard(report: dict) -> str:
    lines = ["# StockAgent league", "",
             f"Seeds: {report['seeds']}  |  best baseline: {report['best_baseline']}", "",
             "| # | team | agents | mean return | 95% CI | median | Sharpe | worst DD | bankrupt | excess vs baseline (95% CI) | qualified |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(report["leaderboard"], 1):
        ci = t["return_ci95"]
        ex = t["excess_vs_best_baseline_ci95"]
        ex_txt = f"[{ex[0]:+.2%}, {ex[1]:+.2%}]" if ex else "-"
        lines.append(f"| {i} | {t['team']} | {t['agents']} | {t['mean_return']:+.2%} | "
                     f"[{ci[0]:+.2%}, {ci[1]:+.2%}] | {t['median_return']:+.2%} | {t['mean_sharpe']:.2f} | "
                     f"{t['worst_drawdown']:.1%} | {t['bankrupt_rate']:.0%} | {ex_txt} | "
                     f"{'yes' if t['qualified'] else 'no'} |")
    fid = [s["price_fidelity"] for s in report["per_seed"]]
    if fid and fid[0]:
        avg = {k: statistics.mean(f[k] for f in fid if k in f) for k in fid[0]}
        lines += ["", "Mean |price / ideal DCF mid - 1|: " + ", ".join(f"{k}: {v:.1%}" for k, v in avg.items())]
    return "\n".join(lines) + "\n"
