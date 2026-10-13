"""StockAgent command line.

  simulate   one simulated market (paper setup by default: 2 stocks, 3 sessions/day)
  league     stage 1: competitive league across seeds; writes league.md / league.json
  paper      stage 2: forward paper trading of candidates on real or replayed daily bars
  promote    pick a champion from the paper league (writes champion.json for human sign-off)
  live       stage 3: one daily step for the approved champion through IBKR (or Alpaca)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from stockagent.config import SimConfig, load_dotenv
from stockagent.league import QualifyRules, render_leaderboard, run_league
from stockagent.simulation import Simulation, parse_population

DEFAULT_POPULATION = ("mock:trader=8,baseline:fundamental=8,baseline:trend=8,"
                      "baseline:noise=8,baseline:buy_and_hold=8")


def sim_config(a) -> SimConfig:
    return SimConfig(stocks=a.stocks.split(","), days=a.days, sessions=a.sessions, seed=a.seed,
                     out_dir=a.out, enable_bbs=not a.no_bbs, enable_reports=not a.no_reports,
                     enable_loans=not a.no_loans, enable_events=not a.no_events,
                     competitive=not a.cooperative)


def add_sim_args(p):
    p.add_argument("--population", default=DEFAULT_POPULATION,
                   help="team=count list; team is provider:model or baseline:{fundamental,trend,noise,buy_and_hold}")
    p.add_argument("--stocks", default="A,B", help="subset of A,B,C,D (paper uses A,B)")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="res")
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--workers", type=int, default=8)
    for flag in ("no-bbs", "no-reports", "no-loans", "no-events"):
        p.add_argument(f"--{flag}", action="store_true", help="paper 5.5 ablation")
    p.add_argument("--cooperative", action="store_true", help="do not tell agents they are competing")


def make_feed(a, ib=None, live=False):
    from stockagent.live.feeds import AlpacaFeed, CSVFeed
    if a.csv:
        return CSVFeed(a.csv, anonymize=a.anonymize)
    symbols = a.symbols.split(",")
    if a.feed == "ibkr":
        from stockagent.live.ibkr import IBKRFeed
        return IBKRFeed(symbols, ib=ib, live=live)
    return AlpacaFeed(symbols)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("simulate")
    add_sim_args(p)

    p = sub.add_parser("league")
    add_sim_args(p)
    p.add_argument("--seeds", default="42,128,256,512,1024")

    for name in ("paper", "promote"):
        p = sub.add_parser(name)
        p.add_argument("--state", default="res/paper_league.json")
        p.add_argument("--csv", help="replay bars from CSV (date,symbol,open,high,low,close,volume)")
        p.add_argument("--anonymize", action="store_true", help="rename/rebase tickers for historical replay")
        p.add_argument("--symbols", default="SPY,QQQ,IWM,TLT,GLD", help="symbols for forward paper trading")
        p.add_argument("--feed", choices=["ibkr", "alpaca"], default="ibkr", help="daily bar source (ignored with --csv)")
        if name == "paper":
            p.add_argument("--candidates", help="comma list of model[@Personality]; required for a new league")
            p.add_argument("--capital", type=float, default=100_000)
            p.add_argument("--temperature", type=float, default=None)
            p.add_argument("--all", action="store_true", help="process every pending date (CSV replay)")
        else:
            p.add_argument("--min-days", type=int, default=40)
            p.add_argument("--out", default="res/champion.json")

    p = sub.add_parser("live")
    p.add_argument("--champion", default="res/champion.json")
    p.add_argument("--state", default="res/live_state.json")
    p.add_argument("--symbols", default="SPY,QQQ,IWM,TLT,GLD")
    p.add_argument("--budget", type=float, required=True)
    p.add_argument("--broker", choices=["ibkr", "alpaca"], default="ibkr")
    p.add_argument("--feed", choices=["ibkr", "alpaca"], default=None, help="defaults to the broker")
    p.add_argument("--real-money", action="store_true",
                   help="live account (IBKR port 4001 / Alpaca live endpoint); needs sign-off")
    p.add_argument("--no-confirm", action="store_true", help="skip per-order confirmation")
    p.add_argument("--csv", default=None)
    p.add_argument("--anonymize", action="store_true")

    load_dotenv()
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "simulate":
        result = Simulation(sim_config(a), parse_population(a.population), temperature=a.temperature,
                            workers=a.workers).run()
        print(render_leaderboard({"seeds": [a.seed], "best_baseline": None, "per_seed": [result],
                                  "leaderboard": [dict(t, qualified=False, excess_vs_best_baseline_ci95=None)
                                                  for t in result["teams"]]}))
        print(f"outputs in {a.out}/")

    elif a.cmd == "league":
        report = run_league(sim_config(a), parse_population(a.population),
                            [int(s) for s in a.seeds.split(",")], QualifyRules(),
                            temperature=a.temperature, workers=a.workers)
        print(render_leaderboard(report))
        print("qualified for paper trading:", report["qualified"] or "none")

    elif a.cmd == "paper":
        from stockagent.live.runner import PaperLeague
        feed = make_feed(a)
        league = PaperLeague(a.state, feed, a.candidates.split(",") if a.candidates else None,
                             capital=a.capital, temperature=a.temperature)
        dates = league.pending_dates()
        if not a.all:
            dates = dates[-1:]
        if not dates:
            print("no new trading day to process")
        board = None
        for d in dates:
            board = league.step(d)
        if board:
            print(json.dumps(board, indent=2))

    elif a.cmd == "promote":
        from stockagent.live.runner import PaperLeague, PromotionRules
        league = PaperLeague(a.state, make_feed(a))
        champion = league.promote(PromotionRules(min_days=a.min_days), a.out)
        print(json.dumps(league.leaderboard(), indent=2))
        if champion:
            print(f"\nchampion: {champion['candidate']} -> {a.out} (status awaiting_approval).\n"
                  "To approve, a human edits approved_by and approved_budget in that file.")
        else:
            print("\nno candidate meets the promotion rules yet")

    elif a.cmd == "live":
        from stockagent.live.runner import live_step

        def confirm(order):
            if a.no_confirm:
                return True
            reply = input(f"submit {order['action_type']} {order['amount']} {order['stock']} @ {order['price']}? [y/N] ")
            return reply.strip().lower() == "y"

        a.feed = a.feed or a.broker
        if a.broker == "ibkr":
            from stockagent.live.ibkr import IBKRBroker
            broker = IBKRBroker(live=a.real_money)
        else:
            from stockagent.live.brokers import AlpacaBroker
            broker = AlpacaBroker(live=a.real_money)
        try:
            feed = make_feed(a, ib=getattr(broker, "ib", None), live=a.real_money)
            placed = live_step(a.champion, a.state, feed, broker, a.budget, confirm)
        finally:
            if hasattr(broker, "close"):
                broker.close()
        print(json.dumps(placed, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
