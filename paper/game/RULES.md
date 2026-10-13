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
- Systematic (code in `stockagent/game.py`): momentum, reversion, passive.
- Discretionary (run by the operating AI agent with research): macro, quality, chartist, contrarian, speculative.
  Speculative was added on 2026-10-13 at the owner's request, with £10,000 under the same rules.
  Also on 2026-10-13, 'trend' was converted to 'contrarian'. It was the agent most similar to another: it held
  the same global tracker as passive. Its book carried over. The owner declared this the final change to the
  roster.

ISA eligibility notes:
- 3× leveraged ETPs such as WisdomTree's LQQ3 are eligible.
- Crypto ETNs moved to the Innovative Finance ISA on 6 April 2026, so they're refused here.
- Bitcoin exposure therefore has to come through shares.

## Agents' own rules (owner's grant, 13 Oct 2026)

Starting with the first check-in on 14 Oct 2026, once a day, each agent may create its own rules to help it gain
an advantage:
- An amendment can add new rules and retire old ones. Each rule records the advantage it's meant to give.
- One amendment per agent per UK calendar day. The code enforces this.
- Systematic agents' rules can retune their code parameters, such as lookbacks, number of holdings, entry and
  exit thresholds, stop-losses, universes and rebalance tolerance. Those take effect on the very next run.
- Discretionary agents' rules are written commitments. They're followed and cited at every check-in.
- An agent's rules bind only itself.
- The owner's house rules above are enforced by the ledger, so no agent rule can override them: ISA-only, no
  margin or shorting, no loans, zero funds loses.

The current rulebooks are in `paper/game/rules/RULEBOOKS.md`, and every amendment is logged in
`paper/game/rules/<agent>.json` and the agent's journal.
