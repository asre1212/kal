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
- Every 12-hour check retrieves complete paginated trade history since each order checkpoint and replays events chronologically. A buy can activate the pre-defined contingent sell in the same interval; sells at the same timestamp as activation are rejected. Orders stop at expiry or the holding cutoff. Incomplete history retains the checkpoint and does not mutate the affected trade. No fills are invented from OHLC extremes; no new trade is selected retrospectively. Backfilled fills carry historical execution time, current recording time and source trade ID. Unfilled/partial entries expire; unmatched positions are unwound at available bids. Stops and time exits are checked at observations, not continuously, and cannot guarantee fill price.
- Profit target: up to 25% net on entry outlay, reduced to half the remaining theoretical maximum profit for expensive baskets. Loss trigger: 35% of outlay. Early time exits apply; do not intentionally hold to settlement. Official outcomes settle residual positions if necessary.
- Maximum sleeve exposure $6,000; aggregate event exposure $4,000; new entries pause at 7.5% portfolio drawdown. These are additional conservative experimental controls.
- Marks use available bid depth less modeled taker exit fees. Missing prices/unavailable size receive zero value and are flagged. Cash includes reserved pending orders; it is not all available for new entries.
- Fees use series metadata and published quadratic schedules, including applicable maker fees. Per-fill .0001 rounding is an approximation to actual exchange accumulator behavior; inspect the ledger. Public API snapshots are not execution confirmations.

## Cadence assessment

After two days, review `collection_history` and workflow timing. The user authorizes reducing to eight hours if 12-hour checks produce incomplete backfills or material order-handling delays. Assess data coverage and missed exits, not two days of P&L. A shorter eight-hour poll still cannot guarantee a two-hour same-day exit. A one-time review will decide and document the change. Preserve the separate biweekly strategy review.

`limit-replay-v2` is the new entry version. Existing trades retain their entry version; later fills explicitly label the execution-model change. Market/stop/time exits are not backfilled without historical executable order-book evidence. Intraperiod drawdown remains unobserved, so reported maximum drawdown is sampled and may understate the true path.

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
- `.github/workflows/study.yml`: tests, collection, ledger commit and Pages deployment requested every 12 hours (01:07 and 13:07 UTC), plus manual runs and source pushes. GitHub schedules may be delayed or skipped. The dashboard shows data age/errors; **this is not a continuous real-time service**. Scheduled workflows in public repos may be disabled after 60 days without repository activity.

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
