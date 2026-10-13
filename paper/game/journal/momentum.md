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
