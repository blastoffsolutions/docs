"""Build reports/dashboard.html: every configured company plus the synthetic sample preview.

The page is self-contained (data inlined as JSON, charts drawn in the browser) and carries
aggregates only: no review text, no reviewer data. The sample is computed by running the real
pipeline code on scripts/synthetic.py's fictional company inside a temporary directory.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import aggregate_quarters
import backtest
import factors
import join_revenue
import score_tone
import synthetic
from aggregate_quarters import composite, load_quarterly
from common import (
    DEFAULT_ROOT,
    Paths,
    base_parser,
    list_companies,
    load_company,
    load_model_config,
    read_csv,
    to_jsonable,
    today,
    utc_now_iso,
)
from factors import FACTOR_COLUMNS, latest
from join_revenue import JOIN_COLUMNS, REV_COLUMNS
from sources.base import PLANNED, PRODUCT_COLUMNS, REGISTRY
from status import LIMITATIONS, history_status

TEMPLATE = DEFAULT_ROOT / "dashboard" / "template.html"
FIT_NOTES = {
    "manufacturer": "Good fit: the company makes the products being reviewed, so review tone speaks to its own quality and demand. Watch how much of revenue runs through Amazon US.",
    "dtc": "Good fit with caveats: direct-to-consumer brands sell mostly on their own sites, so Amazon may be a small channel. Trustpilot and app-store reviews matter more here.",
    "retailer": "Weak fit for product reviews: a retailer's reviews mostly describe other companies' goods. Track store, delivery and app experience, and private-label lines, instead.",
}


def company_payload(root: Path, ticker: str, synthetic_flag: bool = False) -> dict:
    paths = Paths(root, ticker)
    company = load_company(root, ticker)
    qt = load_quarterly(paths)
    comp = composite(qt)
    rev = read_csv(paths.revenue_quarterly_csv, REV_COLUMNS)
    joined = read_csv(paths.revenue_join_csv, JOIN_COLUMNS)
    fac = read_csv(paths.factors_csv, FACTOR_COLUMNS)
    products = read_csv(paths.products_csv, PRODUCT_COLUMNS)
    bt = json.loads(paths.backtest_json.read_text()) if paths.backtest_json.exists() else None
    meta = json.loads(paths.fetch_meta_json.read_text()) if paths.fetch_meta_json.exists() else {}
    profile = json.loads(paths.company_profile.read_text()) if paths.company_profile.exists() else {}
    last_fetch = max((b.get("fetched_at", "") for b in meta.values()), default="") or None

    sources = [{"name": s, "status": "active", "description": REGISTRY[s].description if s in REGISTRY else ""} for s in company.get("sources", [])]
    sources += [{"name": s, "status": "planned", "description": PLANNED.get(s, "")} for s in company.get("planned_sources", [])]
    coverage = {}
    for s, g in qt.groupby("source") if len(qt) else []:
        coverage[s] = g[["fiscal_quarter", "history_status", "n_reviews"]].to_dict("records")

    series = {}
    if len(fac):
        for key, g in fac[fac["fiscal_quarter"].fillna("") != ""].groupby("factor", sort=False):
            series[key] = g[["fiscal_quarter", "value", "zscore", "status"]].to_dict("records")

    return to_jsonable(
        {
            "ticker": company["ticker"],
            "name": company["name"],
            "synthetic": synthetic_flag,
            "company_type": company.get("company_type"),
            "fit_note": FIT_NOTES.get(company.get("company_type"), ""),
            "fiscal_year_end": company["fiscal_year_end"],
            "fiscal_calendar_note": company.get("fiscal_calendar_note", ""),
            "segment_note": company.get("segment_note", ""),
            "storefront_url": company.get("amazon", {}).get("storefront_url"),
            "verified": profile.get("verified", False),
            "references": company.get("references", []),
            "status": {
                **history_status(paths),
                "reviews_scored": int(read_csv(paths.tone_scores_csv, ["review_id"]).shape[0]),
                "products": int(products["asin"].nunique()) if len(products) else 0,
                "revenue_quarters": int(len(rev)),
                "last_fetch": last_fetch,
            },
            "sources": sources,
            "coverage": coverage,
            "quarters": comp.to_dict("records"),
            "revenue": rev.tail(16)[["fiscal_quarter", "revenue_usd", "revenue_yoy", "filed", "derived_q4", "filing_url"]].to_dict("records") if len(rev) else [],
            "joined": joined[["fiscal_quarter", "composite_tone_delta", "yoy_accel_next", "revenue_next_yoy", "revenue_status", "eligible"]].to_dict("records") if len(joined) else [],
            "factors_latest": latest(fac).to_dict("records") if len(fac) else [],
            "factor_series": series,
            "backtest": bt,
        }
    )


def sample_payload(as_of=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="sentiment-sample-") as tmp:
        root = synthetic.write_sample(Path(tmp))
        t = synthetic.TICKER
        score_tone.run(root, t)
        aggregate_quarters.run(root, t, synthetic.AS_OF)
        join_revenue.run(root, t)
        factors.run(root, t)
        backtest.run(root, t, as_of=synthetic.AS_OF)
        return company_payload(root, t, synthetic_flag=True)


def build(root: Path = DEFAULT_ROOT, as_of=None, out: Path | None = None) -> Path:
    cfg = load_model_config(root)
    payload = {
        "generated_at": utc_now_iso(),
        "as_of": str(as_of or today()),
        "model": {
            "version": cfg["model_version"],
            "frozen_at": cfg["frozen_at"],
            "frozen_sha256": cfg["frozen_sha256"],
            "description": cfg["tone_model"]["description"],
            "sparse_min_reviews": cfg["aggregation"]["sparse_min_reviews"],
            "sparse_min_products": cfg["aggregation"]["sparse_min_products"],
        },
        "rule": cfg["preregistered_rule"],
        "limitations": [line[2:] for line in LIMITATIONS.splitlines() if line.startswith("- ")],
        "companies": [company_payload(root, t) for t in list_companies(root)],
        "sample": sample_payload(as_of),
    }
    blob = json.dumps(to_jsonable(payload), separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("__DASHBOARD_DATA__", blob)
    out = out or root / "reports" / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--as-of")
    args = p.parse_args(argv)
    print(build(args.root, args.as_of))


if __name__ == "__main__":
    main()
