# Agents' own rules

From 2026-10-13, once a day, each agent may add or retire its own rules to gain an advantage.
House rules (RULES.md) are the default. An agent may break out of them for its own book via
breakout switches ['isa', 'margin_rate', 'margin_ratio']; every breakout is logged and flagged on the leaderboard.

## momentum (systematic)
- **MOM-1** (since 2026-10-13): Hold money market (CSH2.L) instead of short gilts when nothing qualifies `{"safe": "CSH2.L"}`. _Why: Gilts are losing value as yields rise (IGLS -1.9% 12m) while GBP money market earns ~Bank Rate (+4.2% 12m) with no duration risk_
- **MOM-2** (since 2026-10-13): Only hold an ETF whose momentum score exceeds +3% `{"min_score": 0.03}`. _Why: Filters out noise-level trends that reverse and cost round-trip trades_
- **MOM-3** (since 2026-10-13): Rebalance only when a position drifts more than 15% from target `{"tolerance": 0.15}`. _Why: Cuts turnover and costs; momentum edges are slow, so small drifts are not worth trading_
- **MOM-4** (since 2026-10-13): Widen the hunting ground: add Nasdaq-100, global semiconductors, US energy and India ETFs `{"universe": ["VUSA.L", "VUKE.L", "VMID.L", "VERX.L", "VJPN.L", "VFEM.L", "SGLN.L", "IGLT.L", "IUKP.L", "EQQQ.L", "SEMI.L", "IUES.L", "NDIA.L"]}`. _Why: The strongest trends this year (semis +38% 6m, US energy +21%) were outside the original nine; more candidates, better top 3_
- **MOM-5** (since 2026-10-13): Crash filter: if the global tracker VWRP.L closes below its 200-day average, hold only the safe asset `{"trend_filter": "VWRP.L"}`. _Why: Inherits the retired trend agent's edge: momentum's worst losses come in bear markets, which the 200-day filter sidesteps_
- **MOM-6** (since 2026-10-13): Weight holdings by inverse 63-day volatility instead of equally `{"weighting": "inverse_vol"}`. _Why: Equalises risk across picks so a volatile winner (semis) cannot dominate drawdowns; better risk-adjusted momentum_
- **MOM-7** (since 2026-10-13): Lever the leaders: move a third of any rising leader (above its 50-day average) into its 3x ETP (EQQQ->LQQ3, VUSA->3USL) `{"lever_map": {"EQQQ.L": "LQQ3.L", "VUSA.L": "3USL.L"}, "lever_share": 0.33}`. _Why: Turns 1x trend exposure into ~1.67x on the strongest, still-rising legs while keeping two thirds unlevered; daily-reset decay only bites in chop, which the 50-day filter avoids_

## reversion (systematic)
- **REV-1** (since 2026-10-13): Park unused cash in GBP money market (CSH2.L) `{"idle_asset": "CSH2.L"}`. _Why: The agent has been 100% idle cash earning nothing; money market earns ~Bank Rate while it waits for signals_
- **REV-2** (since 2026-10-13): Enter on 2-day RSI below 15 instead of 10 `{"rsi_entry": 15}`. _Why: Zero signals so far; a slightly looser trigger still captures short-term oversold dips but trades often enough to matter_
- **REV-3** (since 2026-10-13): Stop out any position 8% below cost `{"stop_loss": 0.08}`. _Why: Mean reversion fails when a dip becomes a trend change; cap the loss on those_
- **REV-4** (since 2026-10-13): Time stop: exit any position after 10 calendar days `{"time_stop_days": 10}`. _Why: A dip that has not reverted in two weeks has become a different trade; frees capital for fresh signals_
- **REV-5** (since 2026-10-13): Add ten US mega-caps to the dip universe `{"universe": ["AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "DGE.L", "REL.L", "LSEG.L", "BATS.L", "NG.L", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "NVDA", "V", "MA", "JPM", "COST"]}`. _Why: Deeper, more liquid names with frequent sharp dips; more signals, no stamp duty_
- **REV-6** (since 2026-10-13): Concentrate: at most 4 positions of 25% `{"max_positions": 4}`. _Why: Signals are rare; when one fires, a larger position makes it count_
- **REV-7** (since 2026-10-13): BREAK OUT into statistical arbitrage: trade six correlated pairs; open long-cheap/short-rich legs (20% each) at |z| >= 2 on the 60-day ratio, close at |z| < 0.5 `{"isa": false, "pairs": [["RIO.L", "AAL.L"], ["GSK.L", "AZN.L"], ["LLOY.L", "NWG.L"], ["V", "MA"], ["HSBA.L", "BARC.L"], ["ULVR.L", "DGE.L"]], "pair_z": 2.0}`. _Why: Market-neutral mean reversion earns whether markets rise or fall; Rio Tinto vs Anglo American is already 2.1 standard deviations stretched with 0.83 correlation_

## passive (systematic)
- **PAS-1** (since 2026-10-13): Sweep spare cash into the fund whenever at least one unit is affordable `{"top_up": true, "tolerance": 0.01}`. _Why: Its only drag is idle cash (£215); full investment is the purest form of its edge_
- **PAS-2** (since 2026-10-13): Switch to the cheapest all-world tracker, Invesco FWRG.L (0.15% fee vs 0.22%) `{"holding": "FWRG.L"}`. _Why: Same index, lower fee: the one edge a passive investor fully controls_
- **PAS-3** (since 2026-10-13): Commitment: passive adopts no further rules for the rest of the game, and this rule is never retired. _Why: A passive investor's real edge is never tinkering; it binds itself against its own future temptation_

## macro (discretionary)
- **MAC-1** (since 2026-10-13): Keep at least 20% in GBP money market until the Fed and BoE paths are clear. _Why: Dry powder earning ~Bank Rate during a hiking cycle; lets the agent buy dislocations after FOMC 27-28 Oct and BoE 5 Nov_
- **MAC-2** (since 2026-10-13): Halve energy exposure if Brent closes below its 50-day average. _Why: Energy is the core inflation trade; a break in oil's trend is the first sign the regime is turning_
- **MAC-3** (since 2026-10-13): Rotate money market into gilts if the US 10-year falls 30bp from 5.22% or payrolls turn negative. _Why: Pre-commits the pivot trade so the agent acts on the regime change instead of debating it_
- **MAC-4** (since 2026-10-13): Volatility regime: if VIX closes above 25, halve equity exposure into money market; if below 15, allow +10% equity. _Why: Equity risk premia are best harvested in calm regimes and protected in stressed ones; VIX is the market's own fear gauge_
- **MAC-5** (since 2026-10-13): War-risk insurance: hold 10% in gold while Brent is above $90. _Why: Oil above $90 signals live Middle East risk; gold hedges a geopolitical shock that would hurt the rest of the book_
- **MAC-6** (since 2026-10-13): FOMC playbook: if the Fed holds on 28 Oct and signals no further hikes, move 20% from money market into long gilts the same day. _Why: Pre-commits the pivot trade; the biggest macro gains come from acting first on a regime change_
- **MAC-7** (since 2026-10-13): BREAK OUT: short long gilts (IGLT) at 15% of equity as a term-premium trade until the MAC-6 pivot trigger fires `{"isa": false}`. _Why: Sticky 3%+ inflation, a hiking Fed and rising global yields (US 10y 5.2%, +29bp in a month) push long-gilt prices down; the money-market sleeve already pays Bank Rate_

## quality (discretionary)
- **QUA-1** (since 2026-10-13): No single holding above 25% of the book. _Why: Concentration is the edge, but one AI-disruption headline should not decide the game_
- **QUA-2** (since 2026-10-13): Add up to 5% of equity to a holding that is 10% or more below cost when no thesis-breaking news exists (once per name per week). _Why: Quality compounders de-rated on sentiment are the opportunity; averaging down on noise compounds the edge_
- **QUA-3** (since 2026-10-13): Sell only on a thesis break (organic growth or margins deteriorating in results), never on price alone. _Why: Avoids panic-selling exactly when the market offers quality cheaply_
- **QUA-4** (since 2026-10-13): Follow insider conviction: when a holding's directors buy shares (RNS director dealings), add up to 3% of equity. _Why: Directors buying in a de-rated business is one of the best-documented signals that the market is too pessimistic_
- **QUA-5** (since 2026-10-13): Earnings blackout: no adding in the three trading days before a holding reports; add after if results confirm the thesis. _Why: Results are the thesis test; buying after the evidence costs a little upside but avoids averaging into a broken story_
- **QUA-6** (since 2026-10-13): Swap the weakest: if a holding is the book's worst over 3 months and its consensus estimates were cut, replace it with the cheapest high-ROIC name on the watchlist (Halma, Experian, Games Workshop, RELX, LSEG, Visa). _Why: Keeps capital in the best compounding businesses rather than defending a stale pick_
- **QUA-7** (since 2026-10-13): BREAK OUT: borrow up to 0.3x equity to lift each core compounder to its 25% cap while they trade 15-40% below their highs `{"margin_ratio": 0.3, "margin_rate": 0.0525}`. _Why: Its names trade at 13-18x forward earnings against 5%+ interest; the expected rerating from AI-fear capitulation outweighs the borrowing cost_

## chartist (discretionary)
- **CHA-1** (since 2026-10-13): Exit on a close below the 50-day average or -8% from entry. _Why: Formalises the opening exit rule; small, fast losses are what make breakout trading pay_
- **CHA-2** (since 2026-10-13): Once a trade is +10%, move the stop to entry +2%, then trail 8% below the highest close. _Why: Locks in winners without capping the runs that pay for all the failed breakouts_
- **CHA-3** (since 2026-10-13): New entries only on 20-day breakouts with volume at least 1.2x the 50-day average and RSI(14) below 75. _Why: Volume confirms real demand; very high RSI breakouts tend to be exhausted_
- **CHA-4** (since 2026-10-13): Relative strength: only hold names beating the FTSE 100 over 20 days; swap the laggard for the strongest new breakout. _Why: Breakouts that lag the index are failing quietly; capital belongs with the leaders_
- **CHA-5** (since 2026-10-13): BREAK OUT: leave the ISA to short 20-day breakdowns on 1.2x volume (max 20% of equity short), cover on a close above the 20-day high or +8% against `{"isa": false}`. _Why: In a stagflationary market, breakdowns are as tradable as breakouts; a two-sided book doubles the opportunity set_
- **CHA-6** (since 2026-10-13): At most 2 new entries per day and 6 positions in total. _Why: Overtrading kills breakout systems; forced selectivity keeps only the best setups_
- **CHA-7** (since 2026-10-13): Lever index breakouts: when the S&P 500 or Nasdaq-100 closes at a 20-day high on rising volume, take up to 15% in 3USL/LQQ3 with a 6% stop. _Why: Index breakouts have the cleanest follow-through; 3x ETPs make a 5% index move worth 15% on the sleeve, and the tight stop caps the decay risk_

## contrarian (discretionary)
- **CON-1** (since 2026-10-13): Scale in once: add 5% of equity if a holding falls a further 15% with its thesis intact. _Why: Contrarian entries are rarely at the bottom; pre-planned averaging turns early entries into an advantage_
- **CON-2** (since 2026-10-13): Sell half at +25%, the rest when the narrative turns consensus-positive. _Why: The edge is buying pessimism; it is gone once the crowd agrees, so harvest it_
- **CON-3** (since 2026-10-13): Never buy a company with negative free cash flow and rising net debt. _Why: Separates hated-but-sound from broken (the Oracle rule); value traps are the contrarian's main risk_
- **CON-4** (since 2026-10-13): Fade the game's own crowd: never hold an instrument that two or more other agents hold; prefer names no agent owns. _Why: The other seven agents are the nearest crowd it can see; if they all agree, the edge is gone_
- **CON-5** (since 2026-10-13): Sentiment gauge: only open a new position when a news search for the name in the past week is predominantly negative. _Why: Measures pessimism directly instead of inferring it from price alone_
- **CON-6** (since 2026-10-13): Every Monday, buy up to 5% of equity in the largest holding of the bottom-ranked agent, if the asset is not broken. _Why: The game's worst performer is holding what has just been punished most: the purest contrarian signal available_
- **CON-7** (since 2026-10-13): Capitulation buys: buy names with 14-day RSI below 25, within 5% of a 52-week low and positive free cash flow (subject to CON-5 sentiment). _Why: Forced selling into the low is where contrarian returns are largest; the free-cash-flow test keeps it out of value traps_

## speculative (discretionary)
- **SPE-1** (since 2026-10-13): BREAK OUT: leave the ISA wrapper and borrow up to 0.5x equity at 5.25% (Bank Rate 3.75% + 1.5%) `{"isa": false, "margin_ratio": 0.5, "margin_rate": 0.0525}`. _Why: Maximum convexity: margin adds firepower, and outside the ISA the agent can hold bitcoin ETPs and short; it accepts the interest cost and higher risk of elimination_
- **SPE-2** (since 2026-10-13): Survival rule: close all margin borrowing if total equity falls 25% from its peak; hard stop -20% per position. _Why: Zero funds means losing the game; leverage is only an edge if the agent survives long enough to use it_
- **SPE-3** (since 2026-10-13): Use borrowed money only for the two strongest momentum exposures. _Why: Leverage on the best trends, not on hope_
- **SPE-4** (since 2026-10-13): Run a short book of up to 30% of equity in broken high-volatility names (below their 200-day average, 6-month return worse than -25%). _Why: Having left the ISA, shorts on collapsing momentum fund the longs and profit if the speculative froth keeps deflating_
- **SPE-5** (since 2026-10-13): Pyramid winners: add with margin to any position up 20% from entry; never average down. _Why: Convexity comes from pressing winners; adding to losers is how leveraged accounts reach zero_
- **SPE-6** (since 2026-10-13): Crypto sleeve: up to 20% of equity in the iShares Bitcoin ETP (IB1T.L). _Why: Outside the ISA it can hold bitcoin directly rather than via MSTR; bitcoin is +34% over 3 months and is the purest high-beta asset available_
- **SPE-7** (since 2026-10-13): AI-chip supercycle sleeve: up to 10% in the 3x NVIDIA ETP (3NVD.L) while NVDA is above its 50-day average. _Why: NVIDIA is the purest AI-capex beneficiary; 3x exposure with a trend gate maximises upside per pound of margin_
