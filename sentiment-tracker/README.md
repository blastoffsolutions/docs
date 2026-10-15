# Public sentiment tracker

A local research pipeline that asks one question about a public consumer company: **does the
tone of its Amazon reviews move before its SEC-reported revenue does?** It also lays out the other
signals around a company (defects, quality decline, review volume, price talk, channel
complaints, revenue growth) side by side, each against the company's own history.

Logitech (LOGI) is the worked example and Nike (NKE) ships as a second config. Any ticker can
be added. **Research only: not financial advice and not a trading system.** Nothing here
places, sizes or recommends trades.

> **Current state: INSUFFICIENT HISTORY.** No reviews or SEC revenue have been collected yet for
> LOGI or NKE, so their backtests evaluate 0 of 8 quarters. The pipeline was built in a cloud
> environment whose network policy blocked SEC, Apify and Amazon. Every output for those two
> companies is therefore an honest empty state, not a result. The dashboard's **Sample Co** view uses
> clearly labelled synthetic data to show what a full run looks like.

## What it produces

For each company, under `companies/<ticker>/` (the spec's layout, one copy per company):

| File | What it holds |
|---|---|
| `data/raw/products.csv` | Deduped ASIN catalog: parent ASIN, variant, title, URL, how it was found, first/last seen |
| `data/raw/reviews.csv` | Raw review text with IDs, dates, ratings, ASINs, source URLs. **Git-ignored; stays local** |
| `data/raw/access_log.md` | Every request, its status, every cap or block hit, catalog gaps, collection status |
| `data/raw/sources.md` | Where each number comes from |
| `data/processed/tone_scores.csv` | One row per review: tone −2..+2, components, reason tags. No text |
| `data/processed/quarterly_tone.csv` | Per fiscal quarter and source: mean tone, review count, prior-4-quarter baseline, difference, sparse flag, coverage flag, tag rates |
| `data/processed/revenue_quarterly.csv` | SEC-reported revenue per fiscal quarter, YoY growth, filing and accession number |
| `data/processed/revenue_join.csv` | Each tone quarter joined to revenue for that quarter and the next one |
| `data/processed/factors.csv` | The factor panel: every signal with value, z-score against own history, and status |
| `reports/backtest_report.md` | The frozen rule first, then 8 held-out quarters, hits and **misses** |
| `reports/charts/*.png` | Quarterly tone, tone vs. revenue, negative swings |
| `reports/drops/<YYYY-Www>/` | The weekly export: CSVs, charts, sources, limitations, manifest (git-ignored) |

Shared across companies:

| File | What it holds |
|---|---|
| `models/tone_rubric.md` | The −2..+2 scale, tags and how they are computed |
| `models/tone_model_config.json` | Model parameters, tag terms, aggregation thresholds and the **pre-registered rule**, with a SHA-256 over all of them |
| `reports/dashboard.html` | Self-contained dashboard: every configured company plus the synthetic sample |
| `config/companies/<ticker>.json` | Ticker, CIK, fiscal year end, company type, Amazon brand info, sources |
| `config/sources.json` | Apify actor IDs and input templates |

## Setup

```bash
cd sentiment-tracker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Collection needs two environment variables:

```bash
export APIFY_TOKEN=...                                  # from console.apify.com > Settings > Integrations
export SEC_USER_AGENT="Your Name research you@example.com"   # SEC fair-access policy requires contact details
```

It also needs outbound access to `api.apify.com`, `data.sec.gov` and `www.sec.gov`.

Before the first real run, open each actor's **Input** tab on Apify and confirm the keys in
`config/sources.json` (`junglee/amazon-crawler` for the catalog, `junglee/amazon-reviews-scraper`
for reviews). Apify usage is billed to your account. Fill in the `TODO-confirm` storefront URL and
brand names in each company config.

## Running it

```bash
python scripts/weekly_drop.py                       # rebuild every CSV, chart, report and the dashboard from data on disk
python scripts/weekly_drop.py --refresh             # fetch catalog, reviews and SEC revenue first, then rebuild
python scripts/weekly_drop.py --refresh --ticker NKE
python -m pytest -q                                 # tests (synthetic data in temp folders only)
```

The default run is offline on purpose. It reproduces every output from `data/raw` alone, so
anyone can re-derive the numbers from the same raw files. Each step is also a script you can run
on its own:

| Spec step | Script |
|---|---|
| 1. Verify company and fiscal calendar | `scripts/verify_company.py` |
| 2. Map catalog | `scripts/fetch_products.py` |
| 3. Collect reviews | `scripts/fetch_reviews.py` |
| 4. Score tone | `scripts/score_tone.py` |
| 5. Aggregate quarterly | `scripts/aggregate_quarters.py` |
| 6. Join and backtest | `scripts/join_revenue.py`, `scripts/backtest.py` |
| 7. Weekly refresh | `scripts/weekly_drop.py` (also runs `factors.py`, `charts.py`, `build_dashboard.py`) |

To refresh weekly, schedule it with cron, for example every Monday at 06:10:

```cron
10 6 * * 1  cd /path/to/sentiment-tracker && .venv/bin/python scripts/weekly_drop.py --refresh >> weekly.log 2>&1
```

### Adding a company

```bash
python scripts/new_company.py --ticker DECK --type manufacturer   # resolves CIK and fiscal year end from SEC
# edit config/companies/deck.json: storefront_url, brand_names
python scripts/weekly_drop.py --refresh --ticker DECK
```

`--type` is `manufacturer`, `dtc` or `retailer`. It changes the fit note on the dashboard, not the
math. The signal suits companies that make what is being reviewed and sell a meaningful share
through Amazon US: peripherals, footwear, small appliances, outdoor gear. It suits retailers
(Home Depot, Lowe's) poorly. Their reviews describe other companies' products, so they need
experience-type sources (store, delivery, app) and their private-label lines instead.

### Adding a source

Sources are adapters (`scripts/sources/`). Each one turns a channel into `ReviewRecord` rows with
a `source` column. Scoring, quarterly aggregation (one baseline per source), the factor panel and
the composite signal all work per source automatically. To add one:

1. Subclass `Source` in `scripts/sources/<name>.py`, implement `fetch_products` / `fetch_reviews`,
   and decorate it with `@register`.
2. Import it in `scripts/sources/__init__.py` and add its settings to `config/sources.json`.
3. Add its name to a company's `sources` list.

Reddit (official Data API), app-store reviews, Google Trends and Trustpilot are designed in and
listed as **planned**; only `amazon_apify` is implemented. Sources without star ratings are scored
on text alone. Their tone has a different base rate, which is why each source is compared with
its own history before the sources are averaged.

## Method

1. **Company and calendar.** CIK and fiscal year end are confirmed against SEC submissions data.
   Fiscal years are named for the calendar year they end in. Logitech's year ends March 31:
   its quarters are 13-week periods ending on a Friday, presented as calendar quarter ends.
   Nike's ends May 31. Quarters are mapped by month end.
2. **Catalog.** Brand storefront or brand search via Apify, plus an optional hand-kept
   `asin_seed.csv`. Deduped by ASIN, grouped by parent ASIN. Listings from other brands are
   excluded.
3. **Reviews.** Amazon US reviews per parent ASIN, sorted by most recent, deduped by review ID.
   Reviewer names are not requested. Per product, the access log records how many came back,
   the earliest date, and whether the actor's result cap was hit.
4. **Tone.** Deterministic VADER + star blend on a −2..+2 scale with reason tags; see
   `models/tone_rubric.md`. Seller/shipping-only reviews are separated out.
5. **Quarters.** Mean tone and counts per fiscal quarter and source. A quarter is **sparse**
   below 30 product reviews or 3 products. It is **truncated** when a capped product's history
   starts after the quarter began, and **in progress** until it ends. The baseline is the mean of
   the four prior quarters, and only when all four are usable. Otherwise it is blank, never filled.
6. **Revenue and backtest.** SEC XBRL companyfacts, first-filed value per quarter; Q4 = annual
   10-K minus Q1–Q3. The rule below is evaluated on the 8 most recent eligible quarters.

### The pre-registered rule (`tone-delta-lead-v1`, frozen 2026-10-15)

- **Signal:** this quarter's mean tone minus the mean of the prior four quarters, averaged over
  eligible sources.
- **Call:** DOWN at −0.15 or below; UP at +0.15 or above; otherwise no call.
- **Scored against:** whether next quarter's YoY revenue growth is lower (DOWN) or higher (UP)
  than this quarter's.
- **Compared with:** a coin flip, and a persistence baseline (the last growth trend known when
  the quarter ends).
- **No fitting:** thresholds were set before any data existed. `backtest.py` refuses to run if
  the rule, the tone model, the tags or the thresholds change after freezing, unless told to
  with `--acknowledge-rule-change`, and then the report says so first.

Twelve usable quarters are needed: four to form the first baseline, then eight to score.

## Data handling

- Raw review text (`reviews.csv`) is git-ignored and stays on the collecting machine. Every
  committed file and the dashboard carry IDs, scores and aggregates only.
- No login cookies or session tokens are ever passed to Amazon or to an actor. The adapter
  refuses actor input that contains them. Limits are logged, not bypassed.
- Amazon is reached only through Apify; nothing here scrapes amazon.com directly. Any direct
  web fetch goes through `PoliteSession`, which checks robots.txt and spaces requests. SEC
  requests identify the requester and stay under SEC's 10-requests-per-second limit.
- Synthetic data (`scripts/synthetic.py`) is generated only in temporary folders. The generator
  refuses to write into this folder, and a test checks that no synthetic rows sit under
  `companies/`.

## Limitations and data gaps

- **Review history is capped.** Amazon shows logged-out visitors a limited, sorted slice of
  reviews. Older quarters are often truncated or missing until weekly runs have accumulated
  history. They are flagged, never imputed. Reaching 12 complete quarters may take a long time,
  or may never happen for some products.
- **Most-recent sort bias.** Early runs over-weight the latest quarters.
- **Catalog survivorship.** Discontinued products drop off the storefront, so past quarters can
  miss the very products that failed. Add known ASINs to `asin_seed.csv`.
- **Product mix.** Launches and discontinuations change which products are reviewed; tone can
  move with no change in quality.
- **One channel.** Amazon US is a slice of global revenue whose size companies rarely disclose.
  Wholesale, other retailers and other countries are not observed.
- **Review integrity.** Fake and incentivised reviews are not filtered beyond Amazon's own
  policing.
- **Tone model.** A lexicon plus stars misses sarcasm and domain language. Its parameters are
  frozen for comparability, not because they are optimal.
- **Small samples.** Eight held-out quarters cannot show that the signal works. Treat hits and
  misses as anecdotes until many companies and years agree.
- **Revenue timing.** Q4 is derived. 52/53-week calendars are mapped to month-end quarters.
- **Other factors are descriptive.** Only the tone rule is backtested. Testing any other factor
  (review velocity, defect rate) needs its own rule frozen before looking at results.
