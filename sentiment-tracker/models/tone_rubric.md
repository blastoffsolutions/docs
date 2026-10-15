# Tone rubric (model `tone-v1`)

Every review gets one integer tone score from **−2 to +2** and a set of reason tags. The
parameters live in `tone_model_config.json`. They are part of the frozen, hashed content, so
changing them changes the pre-registered signal (see `backtest_report.md`).

Examples below are paraphrased patterns, not quotes from real reviews.

## The scale

| Score | Meaning | Typical pattern |
|---|---|---|
| **+2** | Delighted | "Best purchase this year; flawless; highly recommend." 5 stars. |
| **+1** | Satisfied | "Works well, solid build, good value." 4 stars. |
| **0** | Neutral or mixed | "Does the job, nothing special." 3 stars, or praise and complaints in equal measure. |
| **−1** | Disappointed | "Button started sticking after a month; not as good as my old one." 2 stars. |
| **−2** | Angry or failed | "Stopped working after two weeks; defective; do not buy." 1 star. |

## How the score is computed

1. **Text tone.** VADER's compound score on title + body maps to the scale with fixed cut points:
   `≤ −0.6 → −2`, `≤ −0.2 → −1`, `< 0.2 → 0`, `< 0.6 → +1`, otherwise `+2`.
2. **Star tone.** `rating − 3`, so 1 star is −2 and 5 stars is +2.
3. **Blend.** `tone = round(0.5 × text + 0.5 × star)`, rounding halves away from zero, clipped to
   [−2, 2]. When there is no star rating (future sources such as Reddit), text tone is used alone.

Both components are kept in `tone_scores.csv` (`text_tone`, `star_tone`, `tone_raw`), so a
reviewer can see when stars and words disagree.

## Reason tags

A review can carry several tags. Matching is case-insensitive on whole words and phrases; the
full term lists are in `tone_model_config.json` under `tags`.

| Tag | What it captures | Example terms |
|---|---|---|
| `defect` | The product failed or misbehaves | stopped working, broke, defective, dead on arrival, double-click, disconnect, drift |
| `quality_decline` | Worse than an earlier version or unit | used to be, not as good as, previous version, my old one, feels cheap, went downhill |
| `price` | Talks about price or value, good or bad | price, overpriced, expensive, not worth, for the money, $ amounts |
| `seller_shipping` | Delivery, packaging or seller problems | shipping, arrived, package, box was, seller, wrong item, refund, late |
| `other` | Negative (−1 or −2) with none of the above | |

## Seller and shipping-only reviews

A review is **seller/shipping-only** when it matches `seller_shipping` and nothing about the
product: no defect, quality-decline or price term and no product word (works, battery, button,
fit, sole, and so on). These reviews describe the channel, not the product, so they are:

- left out of product tone, review counts and tag rates;
- counted per quarter as `n_seller_shipping_excluded`;
- reported as the **Seller/shipping complaints** factor, a channel-health signal.

## Known weaknesses

- VADER is a general-purpose lexicon. It misses sarcasm ("great, it died on day two") and
  domain words. The star blend dampens this but does not remove it.
- Keyword tags over-match ("cost" in "cost me a weekend") and under-match unusual phrasing.
- A future model (for example an LLM rater) must be frozen and re-hashed before its results are
  compared with this one. Scores from different model versions are not comparable.
