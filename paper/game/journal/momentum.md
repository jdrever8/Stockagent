# momentum (systematic)

Strategy: Relative momentum across UCITS ETFs (US, UK, Europe, Japan, EM, gold, gilts, property): hold the top 3 by 6-month return skipping the last week, equal weight, only if positive; otherwise short gilts.

Started 2026-10-13T14:29:58+00:00 with £10,000.

## 2026-10-13T14:35:02+00:00
Signal: 6m-1w returns: VUSA.L +18.6%, VJPN.L +16.2%, VFEM.L +12.1%, VMID.L +11.7%, IUKP.L +7.5%, VERX.L +5.0%, VUKE.L +1.1%, IGLT.L -2.9%, SGLN.L -10.8% -> hold ['VUSA.L', 'VJPN.L', 'VFEM.L']
Trades: buy 29.0 VUSA.L @ 111.652, buy 83.0 VJPN.L @ 39.747, buy 53.0 VFEM.L @ 61.86

## 2026-10-13T16:05:23+00:00
Rulebook amendment:
- add MOM-1: Hold money market (CSH2.L) instead of short gilts when nothing qualifies {'safe': 'CSH2.L'} (why: Gilts are losing value as yields rise (IGLS -1.9% 12m) while GBP money market earns ~Bank Rate (+4.2% 12m) with no duration risk)
- add MOM-2: Only hold an ETF whose momentum score exceeds +3% {'min_score': 0.03} (why: Filters out noise-level trends that reverse and cost round-trip trades)
- add MOM-3: Rebalance only when a position drifts more than 15% from target {'tolerance': 0.15} (why: Cuts turnover and costs; momentum edges are slow, so small drifts are not worth trading)

## 2026-10-13T16:05:36+00:00
Signal: 126d return skipping 5d: VUSA.L +18.6%, VJPN.L +16.2%, VFEM.L +12.1%, VMID.L +11.7%, IUKP.L +7.5%, VERX.L +5.0%, VUKE.L +1.1%, IGLT.L -2.9%, SGLN.L -10.8% -> hold ['VUSA.L', 'VJPN.L', 'VFEM.L']
Trades: none

## 2026-10-13T16:49:33+00:00
Rulebook amendment:
- add MOM-4: Widen the hunting ground: add Nasdaq-100, global semiconductors, US energy and India ETFs {'universe': ['VUSA.L', 'VUKE.L', 'VMID.L', 'VERX.L', 'VJPN.L', 'VFEM.L', 'SGLN.L', 'IGLT.L', 'IUKP.L', 'EQQQ.L', 'SEMI.L', 'IUES.L', 'NDIA.L']} (why: The strongest trends this year (semis +38% 6m, US energy +21%) were outside the original nine; more candidates, better top 3)

## 2026-10-13T16:49:33+00:00
Rulebook amendment:
- add MOM-5: Crash filter: if the global tracker VWRP.L closes below its 200-day average, hold only the safe asset {'trend_filter': 'VWRP.L'} (why: Inherits the retired trend agent's edge: momentum's worst losses come in bear markets, which the 200-day filter sidesteps)

## 2026-10-13T16:49:33+00:00
Rulebook amendment:
- add MOM-6: Weight holdings by inverse 63-day volatility instead of equally {'weighting': 'inverse_vol'} (why: Equalises risk across picks so a volatile winner (semis) cannot dominate drawdowns; better risk-adjusted momentum)

## 2026-10-13T16:50:05+00:00
Signal: 126d return skipping 5d: SEMI.L +74.0%, EQQQ.L +29.8%, VUSA.L +18.6%, VJPN.L +16.2%, VFEM.L +12.1%, VMID.L +11.7%, IUKP.L +7.5%, IUES.L +6.0%, VERX.L +5.0%, VUKE.L +1.1%, NDIA.L -0.9%, IGLT.L -2.9%, SGLN.L -10.8% -> hold SEMI.L 13%, EQQQ.L 33%, VUSA.L 55% (inverse vol)
Trades: sell 83.0 VJPN.L @ 39.705, sell 53.0 VFEM.L @ 61.81, buy 82.0 SEMI.L @ 15.158, buy 5.0 EQQQ.L @ 561.64, buy 19.0 VUSA.L @ 111.418

## 2026-10-13T16:58:58+00:00
Rulebook amendment:
- add MOM-7: Lever the leaders: move a third of any rising leader (above its 50-day average) into its 3x ETP (EQQQ->LQQ3, VUSA->3USL) {'lever_map': {'EQQQ.L': 'LQQ3.L', 'VUSA.L': '3USL.L'}, 'lever_share': 0.33} (why: Turns 1x trend exposure into ~1.67x on the strongest, still-rising legs while keeping two thirds unlevered; daily-reset decay only bites in chop, which the 50-day filter avoids)

## 2026-10-13T16:59:15+00:00
Signal: 126d return skipping 5d: SEMI.L +74.0%, EQQQ.L +29.8%, VUSA.L +18.6%, VJPN.L +16.2%, VFEM.L +12.1%, VMID.L +11.7%, IUKP.L +7.5%, IUES.L +6.0%, VERX.L +5.0%, VUKE.L +1.1%, NDIA.L -0.9%, IGLT.L -2.9%, SGLN.L -10.8% -> hold SEMI.L 13%, EQQQ.L 22%, VUSA.L 37%, LQQ3.L 11%, 3USL.L 18% (inverse vol, 33% of EQQQ.L via 3x LQQ3.L, 33% of VUSA.L via 3x 3USL.L)
Trades: sell 2.0 EQQQ.L @ 561.64, sell 16.0 VUSA.L @ 111.418, buy 3.0 LQQ3.L @ 341.34, buy 12.0 3USL.L @ 192.72
