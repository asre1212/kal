# Kal — paper trading study

[Open dashboard](https://asre1212.github.io/kal/) · [Collector runs](https://github.com/asre1212/kal/actions)

A forward experiment with **$100,000 hypothetical capital** and **$2,000 maximum total entry outlay per complete trade**, including all legs and modeled entry fees. No Kalshi account, trading credentials, or real orders. The first trial lasts 30 days from the first observation. No fabricated past performance.

## Comparison

| Contract horizon at entry | Definition | Maximum planned holding time |
|---|---|---|
| Same day | Deadline falls today in New York | 2 hours |
| About one month | 21–45 days to deadline | 72 hours |
| 6–12 months | 180–366 days to deadline | 7 days |

Each horizon has a $15,000 resting-limit sleeve and $15,000 immediate-execution sleeve, plus a shared $10,000 reserve: $100,000 total. The selected opportunities are the same for both execution modes; realized holdings differ because limits may not fill. Unused capital remains cash. Contract maturity is distinct from our planned holding period. Asset categories and strategy types vary with availability, so this is an exploratory comparison, not a causal test of maturity alone.

## Strategy and execution

- Numeric thresholds: buy NO below the lower boundary and YES above the higher boundary, with equal contract counts. Both can lose inside the range. Range markets: buy NO to the central interval. Deadline ladders: buy early YES + late NO, a bet against the intervening timing window.
- YES and NO of one contract are complementary and do not create an options strangle. A 50/50 probability or longer expiry alone does not supply an edge.
- Baseline candidates pass fixed liquidity, spread, horizon and price filters. These are test rules, **not validated predictors of profitability**.
- Immediate entries pay observed asks and modeled taker fees, capped at 5% of displayed top-level liquidity. Resting entries post bids. The v2 maker model uses subsequent trades at or through the recorded limit with the appropriate aggressor, capped at 5% of eligible volume. A touch remains a queue-unknown estimate, not a confirmed execution. Unknown queues and market impact mean these remain estimates.
- Every 8-hour check retrieves complete paginated trade history since each order checkpoint and replays events chronologically. A buy can activate the pre-defined contingent sell in the same interval; sells at the same timestamp as activation are rejected. Orders stop at expiry or the holding cutoff. Incomplete history retains the checkpoint and does not mutate the affected trade. No fills are invented from OHLC extremes; no new trade is selected retrospectively. Backfilled fills carry historical execution time, current recording time and source trade ID. Unfilled/partial entries expire; unmatched positions are unwound at available bids. Stops and time exits are checked at observations, not continuously, and cannot guarantee fill price.
- Profit target: up to 25% net on entry outlay, reduced to half the remaining theoretical maximum profit for expensive baskets. Loss trigger: 35% of outlay. Early time exits apply; do not intentionally hold to settlement. Official outcomes settle residual positions if necessary.
- Maximum sleeve exposure $6,000; aggregate event exposure $4,000; new entries pause at 7.5% portfolio drawdown. These are additional conservative experimental controls.
- Marks use available bid depth less modeled taker exit fees. Missing prices/unavailable size receive zero value and are flagged. Cash includes reserved pending orders; it is not all available for new entries.
- Fees use series metadata and published quadratic schedules, including applicable maker fees. Per-fill .0001 rounding is an approximation to actual exchange accumulator behavior; inspect the ledger. Public API snapshots are not execution confirmations.

## Cadence assessment

November 8, 2026: **REDUCE TO 8H**, based on execution timing rather than profitability. Seven persisted checks during the initial cadence period made 28 trade-history attempts: two initial HTTP 429 failures were recovered in the next run, with 23/23 subsequent attempts complete. All 67 backfilled fills have matching archived print IDs and timestamps. No observed truncation or unresolved history failure remains. Three scheduled runs started approximately 4h53m–6h08m after their nearest preceding configured slots; the November 8 13:07 UTC slot had no run recorded at the 16:05 UTC review cutoff. Push-triggered checks supplemented the schedule, so the observed sample is not a clean 12-hour experiment.

Eight unique trades had overdue exit handling, 2.238–17.101 hours in the persisted lag fields. These include partial-entry expiry and illiquid residual handling, not just pure polling latency. T00013/T00014 first attempted their time exit about 8.96 hours late and settled remaining legs on the following check; shortening polling does not create missing liquidity. Sparse trading by itself is not a collector failure. The 8-hour schedule reduces intended observation gaps but cannot guarantee scheduler punctuality or a two-hour same-day exit. Historical prints can estimate resting limits, not executable market stops. Preserve the separate biweekly strategy review and assess subsequent cadence performance there.

`limit-replay-v3-8h` is the prospective entry version following the cadence review; execution logic remains `touch-volume-v2`. Existing trades retain their entry version; later fills explicitly label the execution-model change. Market/stop/time exits are not backfilled without historical executable order-book evidence. Intraperiod drawdown remains unobserved, so reported maximum drawdown is sampled and may understate the true path.

## Profit-taking experiment (`profit-targets-v1`)

Prospectively compare **+5%, +10%, +15%, +25%**, and **half at +10% / half at +25%** using the exact same fully filled entry. Targets include allocated entry fees and modeled exit fees. This is a matched counterfactual study, not five additional portfolios: its P&L never enters the funded $100,000 ledger or its equity curve. Prior trades are not retrofitted. At least two contracts per leg are required; the first half rounds down and the second receives the remainder. Each policy retains the source cost cap of at most $2,000.

Each tranche places a resting sale limit on each leg sufficient for **that leg alone to recover the entire tranche basket cost plus target profit**. This is deliberately conservative for two-leg trades; a 10% price rise in one leg does not mean 10% basket profit. Residual legs remain exposed until an observed bid exit. Fixed targets are never silently reduced; unattainable limits are flagged. All policies use resting profit exits, with the original maker/taker entry method retained as a filter. Entry opportunity selection still depends on the funded baseline; this is not a simulation of five independently reinvesting portfolios.

Maximum intended holding times remain 2h / 72h / 7 days, or 30 minutes before close / trial end, with a 35% loss trigger. Each source/tranche within a policy shares the 5% volume and displayed-depth budget. Alternative policies use separate budgets because they are mutually exclusive hypotheses. Historical fills require later timestamped qualifying prints and complete history. Stops, time exits and residual sales need an observed book, so eight-hour checks can miss the intended deadline. Settlement is a reported failure to exit early, not the objective. Alternative positions keep being monitored even after the funded source trade closes.

The comparison table and chart use only entries completed under **every** policy. Separately show all-position marked P&L on identically priced source sets, unresolved exposure, gaps, late exits, target attainment and settlement fallbacks. This exposes completion bias; it does not eliminate it. Individual tranche limits, print IDs, fill times and source references remain in the full JSON export; comparison outcomes have a separate CSV export.

| Quant diagnostic | Definition / limit |
|---|---|
| Net return / expectancy | Sum net P&L / sum entry outlay; average net P&L per matched completed trade |
| Uncertainty | 2,000 paired event-cluster bootstrap draws; 95% percentile intervals for returns and differences versus +25%; minimum 20 event blocks |
| Multiple comparisons | Event-cluster sign-flip tests, assuming symmetric exchangeable effects; Holm correction for four comparisons per horizon/entry-method stratum |
| Win/loss | Win rate, descriptive Wilson interval, average win/loss, median return, profit factor (undefined when no losses) |
| Downside / concentration | Worst return, empirical worst-5% mean after 20 events, effective event count `1 / sum(event outlay weights²)`, largest event weight |
| Time / execution | Average/median/P90 holding hours, early exits, incomplete history, unachievable targets, settlement fallback and late exits |
| Stress cases | Double all modeled fees; subtract an extra 1¢ per contract sold before settlement; accounting haircuts, not liquidity replays |

Hypotheses are fixed during the initial experiment. Twenty events is a display threshold, not proof of adequacy. Event clustering does not remove correlation across markets or days; intervals are not simultaneous, and repeated reviews/12 strata remain exploratory. Do not select a winner from p-values alone. Any tuned target requires a new prospective validation phase; no look-ahead optimization, annualized Sharpe on this short trial, or Kelly sizing without calibrated probabilities. Scripts `profit_study.py` and `profit_stats.py` produce the persisted results in `state.profit_study`; the regular collector runs them automatically.

The replay allocator now reserves only each order's remaining quantity from a print. This allows split tranches to use leftover qualifying volume while retaining the shared 5% cap. Existing fills and losses are preserved.

## Two-week reviews

Reviews are stored in `data/reviews.json` and appear in Research & reviews. They assess realized and marked P&L, expectancy, returns on entry outlay, drawdown, fee drag, duration, fill rates, partial/unpaired exposure, data gaps and concentration. Win-rate Wilson intervals are descriptive; the review module provides 2,000 event-cluster bootstrap resamples only after 20 event blocks. Serial/cross-event correlation and repeated comparisons still need review; do not optimize rules on tiny samples or apply Kelly without a validated probability model.

Decisions must say **keep, pause, or revise**, with evidence, uncertainty and a prospective evaluation plan. Preserve all old fills, original versions and losses. If rules change, assign a new `config.version`; do not rewrite the original strategy's results. The initial 30-day entry window stays fixed unless a subsequent review explicitly documents an extension/new phase.

## Data and operation

- `data/config.json`: versioned entry/risk rules.
- `data/state.json`: authoritative shared ledger and equity history.
- `data/observations/YYYY-MM-DD.jsonl`: archived source markets, books and trade prints used in simulation. Evidence begins at launch; this is not a tick-complete historical dataset.
- `data/reviews.json`: dated review objects `{at,type,title,summary,decisions}` (additional structured evidence fields allowed).
- `docs/data/dashboard.json`: published read-only ledger, generated from state.
- `scripts/collect.py`: public GET requests only; advances simulated trades, preserves evidence and generates dashboard data.
- `scripts/review_stats.py`: reproducible uncertainty diagnostics.
- `.github/workflows/study.yml`: tests, collection, ledger commit and Pages deployment requested every 8 hours (01:07, 09:07 and 17:07 UTC), plus manual runs and source pushes. GitHub schedules may be delayed or skipped. The dashboard shows data age/errors; **this is not a continuous real-time service**. Scheduled workflows in public repos may be disabled after 60 days without repository activity.

GitHub Pages is configured with GitHub Actions as the source. Ledger/data are intentionally public in this public repository. Never add personal accounts, API secrets, or real trading credentials. The app refreshes its published snapshot every minute; the collector, not an open phone tab, maintains the ledger.

Run locally: `python -m unittest discover -s tests -v`, `python scripts/collect.py`, then serve `docs/` with any static server. Python standard library only; no frontend build dependencies. CSV and complete JSON downloads are available in the app.

## Primary sources

1. https://docs.kalshi.com/getting_started/quick_start_market_data
2. https://docs.kalshi.com/getting_started/fee_rounding
3. https://kalshi.com/docs/kalshi-fee-schedule.pdf
4. https://docs.kalshi.com/api-reference/market/get-trades
5. https://docs.kalshi.com/getting_started/historical_data
6. https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

Reviewed November 6, 2026. Verify live rules/fees when making material strategy changes.

Statistical references checked for the profit-target experiment (November 2026):
- Efron (1979), *Bootstrap Methods: Another Look at the Jackknife*: https://www.math.ntu.edu.tw/~hchen/teaching/LargeSample/references/Efron79.pdf
- Holm (1979), *A Simple Sequentially Rejective Multiple Test Procedure*: https://www.ime.usp.br/~abe/lista/pdf4R8xPVzCnX.pdf
- NIST, Wilson proportion confidence intervals: https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm

## November 13 allocation revision

Owner-directed hypothetical allocation: 50% same-day, 25% about-one-month, 25% 6–12-month, applied to $90,000 investable capital ($45,000 / $22,500 / $22,500); the $10,000 reserve is unchanged. Maker/taker each receive half their horizon allocation. The collector applies one idempotent, audited transfer of unreserved cash; no past fills, historical snapshots or original starting balances are rewritten. Net transfers are excluded from P&L, and dashboard percentages remain against original capital, not a time-weighted return. All trade/exposure caps and cadence remain unchanged; this does not force additional entries. See the prospective review and sparse-sample limitations.
