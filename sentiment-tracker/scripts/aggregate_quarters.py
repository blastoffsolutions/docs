"""Step 5: aggregate scored reviews by fiscal quarter and source.

Each row carries mean tone, counts, tag rates, the prior-four-quarter baseline and the
difference from it, plus flags that say how far to trust the row. Missing or unreliable
quarters stay blank; nothing is imputed.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import (
    Paths,
    base_parser,
    fiscal_quarter,
    load_company,
    load_model_config,
    quarter_bounds,
    quarter_ordinal,
    quarter_range,
    read_csv,
    tickers_from_args,
    today,
    write_csv,
)
from score_tone import SCORE_COLUMNS

QT_COLUMNS = [
    "fiscal_quarter",
    "source",
    "quarter_start",
    "quarter_end",
    "n_reviews",
    "n_products",
    "mean_tone",
    "share_negative",
    "defect_rate",
    "quality_decline_rate",
    "price_rate",
    "other_rate",
    "n_seller_shipping_excluded",
    "seller_shipping_share",
    "baseline_prior4_mean",
    "tone_delta",
    "sparse_flag",
    "coverage_flag",
    "review_velocity_yoy",
    "history_status",
]

COMPOSITE_COLUMNS = [
    "fiscal_quarter",
    "n_sources",
    "n_reviews",
    "composite_mean_tone",
    "composite_tone_delta",
    "eligible",
    "defect_rate",
    "quality_decline_rate",
    "price_rate",
    "seller_shipping_share",
    "review_velocity_yoy",
    "history_status",
]


STATUS_PRIORITY = ["partial_quarter", "truncated", "sparse", "no_baseline", "ok"]


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1"])


def coverage_start(scores: pd.DataFrame, meta: dict, source: str):
    """Latest 'earliest review on file' among products whose fetch hit the source's cap.

    Before that date at least one capped product is missing history, so quarters starting
    earlier are truncated. Weekly runs accumulate reviews, so this date moves back over time.
    """
    capped = [pid for pid, m in meta.get(source, {}).get("products", {}).items() if m.get("cap_hit")]
    if not capped:
        return None
    on_file = scores[(scores["source"] == source) & (scores["parent_id"].astype(str).isin(capped))]
    if on_file.empty:
        return None
    return pd.to_datetime(on_file.groupby("parent_id")["review_date"].min()).max()


def aggregate(scores: pd.DataFrame, fye: str, model_cfg: dict, meta: dict, as_of) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame(columns=QT_COLUMNS)
    agg = model_cfg["aggregation"]
    neg = agg["negative_tone_at_or_below"]
    as_of = pd.Timestamp(as_of)
    scores = scores.copy()
    for c in ["seller_shipping_only", "tag_defect", "tag_quality_decline", "tag_price", "tag_other"]:
        scores[c] = _bool(scores[c])
    rows = []
    for source, g in scores.groupby("source"):
        cov = coverage_start(scores, meta, source)
        first_q = min(g["fiscal_quarter"], key=quarter_ordinal)
        last_q = fiscal_quarter(as_of, fye)
        for q in quarter_range(first_q, last_q):
            qs, qe = quarter_bounds(q, fye)
            gq = g[g["fiscal_quarter"] == q]
            prod = gq[~gq["seller_shipping_only"]]
            n = len(prod)
            excluded = int(gq["seller_shipping_only"].sum())
            n_products = prod["parent_id"].nunique()
            sparse = bool(n < agg["sparse_min_reviews"] or n_products < agg["sparse_min_products"])
            if qe > as_of:
                coverage = "partial_quarter"
            elif cov is not None and qs < cov:
                coverage = "truncated"
            else:
                coverage = "complete"
            rate = (lambda col: float(prod[col].mean())) if n else (lambda col: np.nan)
            rows.append(
                {
                    "fiscal_quarter": q,
                    "source": source,
                    "quarter_start": qs.date().isoformat(),
                    "quarter_end": qe.date().isoformat(),
                    "n_reviews": n,
                    "n_products": n_products,
                    "mean_tone": float(prod["tone"].mean()) if n else np.nan,
                    "share_negative": float((prod["tone"] <= neg).mean()) if n else np.nan,
                    "defect_rate": rate("tag_defect"),
                    "quality_decline_rate": rate("tag_quality_decline"),
                    "price_rate": rate("tag_price"),
                    "other_rate": rate("tag_other"),
                    "n_seller_shipping_excluded": excluded,
                    "seller_shipping_share": excluded / (n + excluded) if (n + excluded) else np.nan,
                    "sparse_flag": sparse,
                    "coverage_flag": coverage,
                }
            )
    df = pd.DataFrame(rows)
    k = agg["baseline_quarters"]
    out = []
    for _, g in df.groupby("source", sort=True):
        g = g.sort_values("fiscal_quarter", key=lambda s: s.map(quarter_ordinal)).reset_index(drop=True)
        usable = (~g["sparse_flag"]) & (g["coverage_flag"] == "complete")
        base, delta, vel, status = [], [], [], []
        for i in range(len(g)):
            prior = g.iloc[max(0, i - k) : i]
            ok_prior = len(prior) == k and usable.iloc[max(0, i - k) : i].all()
            b = float(prior["mean_tone"].mean()) if ok_prior else np.nan
            base.append(b)
            delta.append(g.at[i, "mean_tone"] - b if ok_prior and usable.iloc[i] else np.nan)
            if i >= 4 and usable.iloc[i] and usable.iloc[i - 4] and g.at[i - 4, "n_reviews"] > 0:
                vel.append(g.at[i, "n_reviews"] / g.at[i - 4, "n_reviews"] - 1)
            else:
                vel.append(np.nan)
            if g.at[i, "coverage_flag"] != "complete":
                status.append(g.at[i, "coverage_flag"])
            elif g.at[i, "sparse_flag"]:
                status.append("sparse")
            elif not ok_prior:
                status.append("no_baseline")
            else:
                status.append("ok")
        g["baseline_prior4_mean"] = base
        g["tone_delta"] = delta
        g["review_velocity_yoy"] = vel
        g["history_status"] = status
        out.append(g)
    return pd.concat(out, ignore_index=True).reindex(columns=QT_COLUMNS)


def composite(qt: pd.DataFrame) -> pd.DataFrame:
    """One row per fiscal quarter across sources; tone delta averaged over eligible sources."""
    if qt.empty:
        return pd.DataFrame(columns=COMPOSITE_COLUMNS)
    qt = qt.copy()
    rows = []
    for q, g in qt.groupby("fiscal_quarter"):
        ok = g[g["history_status"] == "ok"]
        n = g["n_reviews"].sum()
        w = g["n_reviews"].where(g["n_reviews"] > 0)

        def wmean(col):
            vals = g[col]
            mask = vals.notna() & w.notna()
            return float(np.average(vals[mask], weights=w[mask])) if mask.any() else np.nan

        statuses = set(g["history_status"])
        rows.append(
            {
                "fiscal_quarter": q,
                "n_sources": len(g),
                "n_reviews": int(n),
                "composite_mean_tone": wmean("mean_tone"),
                "composite_tone_delta": float(ok["tone_delta"].mean()) if len(ok) else np.nan,
                "eligible": bool(len(ok)),
                "defect_rate": wmean("defect_rate"),
                "quality_decline_rate": wmean("quality_decline_rate"),
                "price_rate": wmean("price_rate"),
                "seller_shipping_share": wmean("seller_shipping_share"),
                "review_velocity_yoy": float(g["review_velocity_yoy"].mean()) if g["review_velocity_yoy"].notna().any() else np.nan,
                "history_status": "ok" if len(ok) else next(s for s in STATUS_PRIORITY if s in statuses),
            }
        )
    out = pd.DataFrame(rows, columns=COMPOSITE_COLUMNS)
    return out.sort_values("fiscal_quarter", key=lambda s: s.map(quarter_ordinal)).reset_index(drop=True)


def load_quarterly(paths: Paths) -> pd.DataFrame:
    qt = read_csv(paths.quarterly_tone_csv, QT_COLUMNS)
    if not qt.empty:
        qt["sparse_flag"] = _bool(qt["sparse_flag"])
    return qt


def run(root, ticker: str, as_of=None) -> pd.DataFrame:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    scores = read_csv(paths.tone_scores_csv, SCORE_COLUMNS)
    meta = json.loads(paths.fetch_meta_json.read_text()) if paths.fetch_meta_json.exists() else {}
    qt = aggregate(scores, company["fiscal_year_end"], load_model_config(root), meta, as_of or today())
    write_csv(qt, paths.quarterly_tone_csv, QT_COLUMNS)
    return qt


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--as-of", help="treat this date as today (YYYY-MM-DD)")
    args = p.parse_args(argv)
    for t in tickers_from_args(args):
        qt = run(args.root, t, args.as_of)
        ok = int((qt["history_status"] == "ok").sum()) if len(qt) else 0
        print(f"{t}: {len(qt)} source-quarters, {ok} usable")


if __name__ == "__main__":
    main()
