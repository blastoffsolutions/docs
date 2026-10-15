# Backtest: NIKE, Inc. (NKE)

As of 2026-10-15. Research only; not financial advice and not a trading system.

## 1. Pre-registered rule (fixed before any results)

Rule `tone-delta-lead-v1`, frozen 2026-10-15.

- **Question:** Does a quarter's Amazon review tone, relative to its own recent history, lead the next quarter's change in SEC-reported revenue growth?
- **Signal:** composite_tone_delta[q] = mean over eligible sources of (mean_tone[q] - mean(mean_tone[q-4..q-1]))
- **Eligible quarter:** non-sparse, coverage complete, prior-4-quarter baseline present (thresholds in 'aggregation')
- **Call:** DOWN if composite_tone_delta <= -0.15; UP if composite_tone_delta >= +0.15; otherwise NO CALL.
- **Primary target:** sign(revenue_yoy[q+1] - revenue_yoy[q]); DOWN is a hit if negative, UP is a hit if positive, exactly zero is a miss
- **Secondary (reported only):** nowcast sign(revenue_yoy[q] - revenue_yoy[q-1]); reported only, never counted
- **Holdout:** the 8 most recent eligible quarters whose q+1 revenue has been filed with the SEC
- **Comparisons:** coin flip (50%); persistence: predict sign(revenue_yoy[q-1] - revenue_yoy[q-2]), the last acceleration known when quarter q ends
- **Statistics:** hit rate on calls; one-sided exact binomial p-value against 50%; with at most 8 quarters this has almost no statistical power and is reported as such
- **No fitting:** Thresholds were fixed before any data was collected. Nothing is estimated from the holdout quarters.

Frozen-content SHA-256: `5f3520c98c154a4d2dc5b0419f2561db0ad44cd21c33b7d489dc838a50a88b85`. Current content matches.

## 2. Data status

No reviews collected yet, so there are no tone quarters. No SEC revenue data on file (companyfacts not yet downloaded).

**INSUFFICIENT HISTORY: 0 of 8 held-out quarters available.** Missing quarters are not imputed. The rule needs 12 complete, non-sparse quarters of reviews (4 to form the first baseline, then 8 to evaluate) plus the following quarter's SEC revenue.

## 3. Results

No quarter qualified for evaluation.

## 4. Misses

None recorded. (Nothing was evaluated.)

## 5. Secondary: nowcast (reported only)

Nothing to report.

See README.md for limitations and data gaps.
