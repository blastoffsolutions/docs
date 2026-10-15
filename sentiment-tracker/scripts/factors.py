"""A company's factor panel: several signals side by side, each against its own history.

factors.csv is long format (one row per company, quarter and factor). Every factor carries
its value, a z-score against the company's own prior four usable quarters, and a status that
says whether to trust it: ok, sparse, truncated, partial, no_baseline, not_collected, planned.

These factors are descriptive. Only the pre-registered tone rule is backtested; testing any
other factor needs its own rule frozen before looking at results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aggregate_quarters import composite, load_quarterly
from common import Paths, base_parser, load_company, quarter_ordinal, read_csv, shift_quarter, tickers_from_args, write_csv
from join_revenue import REV_COLUMNS

FACTOR_COLUMNS = ["ticker", "fiscal_quarter", "factor", "label", "group", "value", "zscore", "status", "unit", "better", "description"]

# key, label, group, column in the composite/revenue table, unit, which direction is good, description
TONE_FACTORS = [
    ("product_tone", "Product tone", "Reviews", "composite_mean_tone", "score", "higher", "Mean review tone on a -2..+2 scale; seller/shipping-only reviews excluded."),
    ("tone_delta", "Tone vs. own baseline", "Reviews", "composite_tone_delta", "score", "higher", "This quarter's tone minus the mean of the prior four quarters. The pre-registered signal."),
    ("review_velocity", "Review velocity", "Demand", "review_velocity_yoy", "pct", "higher", "Review count vs. the same quarter a year earlier. A rough proxy for units sold."),
    ("defect_rate", "Defect mentions", "Quality", "defect_rate", "pct", "lower", "Share of reviews describing failures: stopped working, broke, defective, disconnects."),
    ("quality_decline_rate", "Quality-decline mentions", "Quality", "quality_decline_rate", "pct", "lower", "Share of reviews comparing unfavourably with an earlier version or model."),
    ("price_rate", "Price mentions", "Value", "price_rate", "pct", "neutral", "Share of reviews that talk about price or value, for better or worse."),
    ("seller_shipping_share", "Seller/shipping complaints", "Channel", "seller_shipping_share", "pct", "lower", "Share of reviews only about delivery, packaging or the seller."),
]
REVENUE_FACTORS = [
    ("revenue_yoy", "Revenue growth", "Financials", "revenue_yoy", "pct", "higher", "SEC-reported revenue vs. the same fiscal quarter a year earlier."),
    ("revenue_accel", "Revenue growth acceleration", "Financials", "revenue_accel", "pts", "higher", "Change in YoY growth from the previous quarter."),
]
PLANNED_FACTORS = [
    ("reddit_tone", "Reddit tone", "Community", "reddit", "Tone of brand and product posts and comments."),
    ("reddit_volume", "Reddit mention volume", "Community", "reddit", "Posts and comments mentioning the brand or flagship products."),
    ("search_interest", "Search interest", "Demand", "google_trends", "Google Trends interest for brand and flagship products."),
    ("app_rating", "App-store rating", "Experience", "app_store", "Average new rating of the company's apps."),
]
REVENUE_LOOKBACK = 16


def zscores(values: pd.Series, usable: pd.Series, k: int = 4) -> list[float]:
    out = []
    for i in range(len(values)):
        prior = values.iloc[:i][usable.iloc[:i]].tail(k)
        if len(prior) == k and prior.std(ddof=1) > 0 and usable.iloc[i]:
            out.append(float((values.iloc[i] - prior.mean()) / prior.std(ddof=1)))
        else:
            out.append(np.nan)
    return out


def tone_rows(ticker: str, comp: pd.DataFrame) -> list[dict]:
    rows = []
    for key, label, group, col, unit, better, desc in TONE_FACTORS:
        base = dict(ticker=ticker, factor=key, label=label, group=group, unit=unit, better=better, description=desc)
        if comp.empty:
            rows.append({**base, "fiscal_quarter": "", "value": np.nan, "zscore": np.nan, "status": "not_collected"})
            continue
        vals = comp[col].astype(float)
        hs = comp["history_status"]
        status = [
            ("no_baseline" if s == "ok" else s) if pd.isna(v) else ("ok" if s in ("ok", "no_baseline") else s)
            for v, s in zip(vals, hs)
        ]
        usable = pd.Series([s == "ok" for s in status], index=comp.index) & vals.notna()
        for q, v, z, s in zip(comp["fiscal_quarter"], vals, zscores(vals, usable), status):
            rows.append({**base, "fiscal_quarter": q, "value": v, "zscore": z, "status": s})
    return rows


def revenue_rows(ticker: str, rev: pd.DataFrame) -> list[dict]:
    rows = []
    rev = rev.tail(REVENUE_LOOKBACK).reset_index(drop=True) if not rev.empty else rev
    if not rev.empty:
        by_q = rev.set_index("fiscal_quarter")["revenue_yoy"]
        rev = rev.assign(revenue_accel=[v - by_q.get(shift_quarter(q, -1), np.nan) for q, v in zip(rev["fiscal_quarter"], rev["revenue_yoy"])])
    for key, label, group, col, unit, better, desc in REVENUE_FACTORS:
        base = dict(ticker=ticker, factor=key, label=label, group=group, unit=unit, better=better, description=desc)
        if rev.empty:
            rows.append({**base, "fiscal_quarter": "", "value": np.nan, "zscore": np.nan, "status": "not_collected"})
            continue
        vals = rev[col].astype(float)
        usable = vals.notna()
        for q, v, z in zip(rev["fiscal_quarter"], vals, zscores(vals, usable)):
            rows.append({**base, "fiscal_quarter": q, "value": v, "zscore": z, "status": "ok" if pd.notna(v) else "no_baseline"})
    return rows


def planned_rows(ticker: str) -> list[dict]:
    return [
        dict(ticker=ticker, fiscal_quarter="", factor=key, label=label, group=group, value=np.nan, zscore=np.nan,
             status="planned", unit="", better="", description=f"{desc} Planned source: {src}.")
        for key, label, group, src, desc in PLANNED_FACTORS
    ]


def build(ticker: str, comp: pd.DataFrame, rev: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(tone_rows(ticker, comp) + revenue_rows(ticker, rev) + planned_rows(ticker), columns=FACTOR_COLUMNS)
    return df


def latest(factors: pd.DataFrame) -> pd.DataFrame:
    """Most recent row per factor, preferring quarters whose status is usable."""
    out = []
    for key, g in factors.groupby("factor", sort=False):
        g = g.copy()
        g["_ord"] = g["fiscal_quarter"].map(lambda q: quarter_ordinal(q) if isinstance(q, str) and q else -1)
        good = g[g["status"] == "ok"]
        out.append((good if len(good) else g).sort_values("_ord").iloc[-1].drop("_ord"))
    return pd.DataFrame(out).reset_index(drop=True)


def run(root, ticker: str) -> pd.DataFrame:
    paths = Paths(root, ticker).ensure()
    load_company(root, ticker)
    comp = composite(load_quarterly(paths))
    rev = read_csv(paths.revenue_quarterly_csv, REV_COLUMNS)
    df = build(ticker, comp, rev)
    write_csv(df, paths.factors_csv, FACTOR_COLUMNS)
    return df


def main(argv=None):
    args = base_parser(__doc__).parse_args(argv)
    for t in tickers_from_args(args):
        df = run(args.root, t)
        print(f"{t}: {len(df)} factor rows; statuses {df['status'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
