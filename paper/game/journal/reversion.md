# reversion (systematic)

Strategy: Short-term mean reversion in FTSE 100 blue chips: buy 2-day RSI < 10 dips in stocks above their 200-day average (max 5 at 20%), sell when the close recovers above the 5-day average.

Started 2026-10-13T14:29:58+00:00 with £10,000.

## 2026-10-13T14:30:06+00:00
Signal: no signals
Trades: none

## 2026-10-13T14:35:10+00:00
Signal: no signals
Trades: none

## 2026-10-13T16:05:23+00:00
Rulebook amendment:
- add REV-1: Park unused cash in GBP money market (CSH2.L) {'idle_asset': 'CSH2.L'} (why: The agent has been 100% idle cash earning nothing; money market earns ~Bank Rate while it waits for signals)
- add REV-2: Enter on 2-day RSI below 15 instead of 10 {'rsi_entry': 15} (why: Zero signals so far; a slightly looser trigger still captures short-term oversold dips but trades often enough to matter)
- add REV-3: Stop out any position 8% below cost {'stop_loss': 0.08} (why: Mean reversion fails when a dip becomes a trend change; cap the loss on those)

## 2026-10-13T16:05:45+00:00
Signal: idle 100% in CSH2.L
Trades: buy 7.0 CSH2.L @ 1255.7
