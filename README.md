# StockAgent: competing LLM traders, from simulation to a funded champion

![workflow](fig/workflow.png)

This is a rebuild of the code for *When AI Meets Finance (StockAgent): Large Language Model-based Stock
Trading in Simulated Real-world Environments* ([arXiv:2407.18957](https://arxiv.org/pdf/2407.18957),
v5, ACM TIST). It is turned into a three-stage pipeline in which LLM agents **compete**, and only a
proven winner is allowed near real money.

```
 Stage 1  LEAGUE (simulated)    many models + rule baselines trade the paper's fictional market
          python main.py league  -> league.md: who beats every baseline, with 95% CIs
                │ qualified teams only
 Stage 2  PAPER (real prices)   candidates get identical virtual portfolios on real daily bars
          python main.py paper   (daily cron) vs buy-and-hold, behind hard risk limits
                │ promote: >= 40 days, beats benchmark, Sharpe, drawdown, error budget
 Stage 3  LIVE (budget)         champion.json must be signed by a human (approved_by, approved_budget)
          python main.py live    dedicated broker account = budget, RiskGate, per-order confirm
```

## Quick start (no API keys needed)

```bash
pip install -r requirements.txt
python main.py simulate                       # mock LLM + 4 baselines, paper setup, 10 days
python -m pytest -q tests                     # order book, accounting, risk, promotion, live gates
```

## Stage 1: the league

```bash
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=... GOOGLE_API_KEY=... DEEPSEEK_API_KEY=...
# or route everything through one OpenAI-compatible gateway:
export YUNWU_API_KEY=... YUNWU_API_BASE_URL=https://yunwu.ai/v1

python main.py league --days 30 --seeds 42,128,256 \
  --population "anthropic:claude-sonnet-4-6=10,openai:gpt-5.5=10,gemini:gemini-3.1-flash-lite=10,\
deepseek:deepseek-v4-flash=10,baseline:fundamental=10,baseline:trend=10,baseline:noise=10,baseline:buy_and_hold=10"
```

How the competition works:

* **One market, everyone in it.** All teams trade the same order book, so every profit comes out of another
  agent's pocket or from mispricing. Agents are told they are ranked against each other. Each prompt shows
  their current rank and return, and the forum (BBS) is anonymous and may be self-serving.
* **Fair starts.** Endowments are *mirrored*: agent *j* of every team gets the same cash, shares, initial loan
  and personality. Teams therefore differ only in the model driving them.
* **Controls.** The rule-based baselines are a fundamental trader that uses the paper's DCF band, a trend
  follower, a noise trader and buy-and-hold. A model that cannot beat them has not shown any skill.
* **Statistics.** Results are pooled over seeds. A team *qualifies* only if the lower 95% bootstrap bound of
  its paired excess return over the best baseline is above 0, with ≤5% bankruptcies and few invalid answers.
* **Cost.** With N agents there are about N × (1 loan + 3 orders + 1 estimate + 1 post) calls per day, plus
  retries. Use `--days`, population sizes and cheap models to fit the budget. Token usage per model is
  written to `summary.json`.

Paper ablations (section 5.5): `--no-bbs --no-reports --no-loans --no-events`. Two more switches:
`--stocks A,B,C,D` adds the repo's two extra companies, and `--cooperative` removes the competitive framing.

## Stage 2: paper trading on real prices

```bash
export ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=...       # free account; market data only here
python main.py paper --candidates "anthropic:claude-sonnet-4-6@Balanced,openai:gpt-5.5@Aggressive" \
                     --symbols SPY,QQQ,IWM,TLT,GLD --capital 100000
# then once per trading day after the close (cron), with no --candidates:
python main.py paper --symbols SPY,QQQ,IWM,TLT,GLD
python main.py promote --symbols SPY,QQQ,IWM,TLT,GLD        # after >= 40 days
```

* Decisions are made after day *t*'s close. Fills are evaluated on day *t+1*'s bar: at the open if the order
  is marketable, otherwise at the limit if the day's range reaches it. Fees and slippage apply, and nothing
  can see the future.
* Every candidate runs behind the same `RiskGate`:
  * max 25% of equity per symbol and 10% per order, 3 orders a day;
  * no shorting or leverage;
  * no new buys after a 3% daily loss;
  * a kill switch at 15% drawdown.
* **Leakage:** LLMs have memorised historical prices of real tickers. Forward paper trading is clean.
  Historical replay (`--csv bars.csv --all`) is only meaningful with `--anonymize`, which renames tickers to
  S1..Sn and rebases each one to 100.
* `promote` writes `champion.json` with status `awaiting_approval`. Rules are in `PromotionRules`:
  * at least 40 days of paper trading;
  * beats equal-weight buy-and-hold;
  * Sharpe ≥ 0.5;
  * drawdown no worse than 10%;
  * ≤25% risk rejections and ≤5% LLM errors;
  * never hit the kill switch.

## Stage 3: the champion gets a budget

1. Open a **dedicated** brokerage account (or sub-account) and fund it with the budget only. The runner
   refuses to trade an account worth more than 1.5× the budget.
2. Run the champion in the Alpaca *paper* account first. This checks real order plumbing:
   `python main.py live --budget 10000`.
3. A human reviews the evidence and edits `champion.json`, setting `approved_by` and `approved_budget`.
4. Run `STOCKAGENT_LIVE=1 python main.py live --budget 10000 --real-money`. Every order asks for `y/N`
   confirmation unless `--no-confirm` is given.

The LLM never holds the keys. Every order passes `validate_order` and then `RiskGate`, and the budget is
enforced by the account itself. None of this is investment advice. Check the rules for automated trading in
your jurisdiction before stage 3.

## What changed from the original code

The original repo could not produce valid results. The fixes:

* **Wrong share counts.** `buy_stock`/`sell_stock` received price and amount swapped, so holdings changed by
  the *price*.
* **Order matching:**
  * orders filled only at exactly equal prices, so a buy above the ask never filled;
  * there was no price-time priority;
  * cash wasn't reserved, so a seller could be paid when the buyer's leg failed.
* **Loan retries always failed.** The retry called `check_loan(date, resp)`, with the arguments in the
  wrong order.
* **Price history wiped.** `Stock.update_price` saved and then cleared the same list.
* **Half-finished upgrade.** The prompts mentioned stocks C/D but no code supplied them, and Claude/DeepSeek
  models silently returned `None`.
* **Default run skipped all events.** It lasted 10 days, while reports and events start on day 12/78/144.
* **Loan terms disagreed.** Prompts said 1/2/3 *years*; the code used 22/44/66 days. Maturity charged a
  full year's rate.
* **Slow logging and missing dependencies.** Every trade rewrote an .xlsx file, there were no seeds, and the
  repo depended on `procoder`, `openpyxl` and `google-generativeai`.

The new code is laid out as follows:

* **Paper fidelity:** transaction fees from 4.1.2, monthly simple interest, and the D78 easing / D144 rate
  hike with the A/B revenue guidance.
* **Data:** ideal-price tables 6/7 drive the fundamental baseline and the price-fidelity metric.
* **LLM layer:** plain HTTP clients with no SDK pins. JSON is extracted robustly and invalid answers are
  retried with the validator's message. Agents carry one line of memory per day across days.

Known simplifications:

* Prices update to the last trade of each session, as in the paper.
* Forced liquidation after a missed repayment sells at the last price to an outside buyer. Those shares
  leave the simulated float.
* Shorting is not modelled.

## Layout

```
stockagent/config.py       paper constants, events, SimConfig
stockagent/market.py       order book, fees, session prices
stockagent/agents.py       accounts, loans, liquidation, endowments
stockagent/policies.py     Observation, validation, LLMPolicy, baselines
stockagent/llm.py          openai/anthropic/gemini/deepseek/gateway/mock clients
stockagent/simulation.py   day loop (paper Fig. 2)
stockagent/league.py       stage 1 league + qualification
stockagent/live/           feeds, VirtualPortfolio + Alpaca broker, RiskGate, paper league, promotion, live step
```

## Citation

```
@article{zhang2024ai,
  title={When ai meets finance (stockagent): Large language model-based stock trading in simulated real-world environments},
  author={Zhang, Chong and Liu, Xinyi and Zhang, Zhongmou and Jin, Mingyu and Li, Lingyao and Wang, Zhenting and Hua, Wenyue and Shu, Dong and Zhu, Suiyuan and Jin, Xiaobo and others},
  journal={arXiv preprint arXiv:2407.18957},
  year={2024}
}
```
