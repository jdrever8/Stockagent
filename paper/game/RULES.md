# ISA agent game: rules

Set by the owner on 2026-10-13. After this point the owner gives no further input.

1. Several agents trade, each with its own strategy. Two or more agents may use the same strategy.
2. Anything that can be held in a UK stocks & shares ISA can be traded:
   - shares listed on recognised exchanges (LSE including AIM, NYSE, Nasdaq…);
   - UCITS ETFs, investment trusts and gilts.
   US-domiciled ETFs can't be bought by UK retail investors (no KID), so the UCITS London line is used.
3. No margin.
4. No loans of any kind, from a bank or another agent. An agent whose funds reach zero loses.
5. No other rules or sandboxing.
6. The number of trades and the trade sizes are up to each agent.
7. Macro data, fundamentals and charting may all be used.
8. Agents may share a strategy.
9. The owner checks in a couple of times a day. Each check-in is when agents run their systems and trade.
10. Everything else is the agents' decision.

Mechanics that follow from these rules or from reality (not extra rules):
- Every agent starts with £10,000 in its own book, valued in GBP.
- Short selling is impossible, because it can't be done inside an ISA.
- Fills are at the live price at the time of the check-in.
- Real costs apply:
  - 0.5% stamp duty (SDRT) on purchases of UK shares and investment trusts. ETFs, AIM shares and gilts are exempt.
  - A 0.15% FX charge on non-GBP trades.
  - No dealing fee.
- Every trade records its price source, quote time, FX rate and the agent's reasoning.

Agents:
- Systematic (code in `stockagent/game.py`): momentum, trend, reversion, passive.
- Discretionary (run by the operating AI agent with research): macro, quality, chartist, speculative.
  Speculative was added on 2026-10-13 at the owner's request, with £10,000 under the same rules.

ISA eligibility notes:
- 3× leveraged ETPs such as WisdomTree's LQQ3 are eligible.
- Crypto ETNs moved to the Innovative Finance ISA on 6 April 2026, so they're refused here.
- Bitcoin exposure therefore has to come through shares.
