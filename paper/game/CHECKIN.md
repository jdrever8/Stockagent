# Check-in runbook

The owner checks in a couple of times a day. Each check-in runs these steps, in order:

1. **Rules (first check-in of each UK day, from 14 Oct 2026).** Each agent reviews its results, its journal and the
   leaderboard, then may make one amendment to its own rulebook. Command:
   `python main.py game amend <agent> '<json>'`.
   Skipping is allowed. Rules must aim at an advantage and record why.
2. **Systematic agents:** `python main.py game run`. Momentum, reversion and passive trade on their code using
   their active rule parameters.
3. **Discretionary agents** (macro, quality, chartist, contrarian, speculative):
   - Refresh their research: prices and charts from Yahoo, macro and company news from web search.
   - Check their thesis, their exit rules and every active rule in their rulebook.
   - Trade with `python main.py game trade <agent> buy|sell SYMBOL QTY --instrument ... --note "<reason, citing rule ids>"`.
4. **Leaderboard:** `python main.py game status`.
5. **Commit and push** `paper/game/` to `claude/youthful-meitner-r4qqcu`.

House rules: `RULES.md` (ISA-only, no margin, no shorting, no loans, zero funds loses). The ledger enforces them.
