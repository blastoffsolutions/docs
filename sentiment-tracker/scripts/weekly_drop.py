"""Step 7: the weekly refresh. Rebuild every CSV, chart and report, then export a dated drop.

    python scripts/weekly_drop.py                  # offline: reproduce everything from raw data on disk
    python scripts/weekly_drop.py --refresh        # fetch new catalog, reviews and SEC revenue first
    python scripts/weekly_drop.py --ticker NKE     # one company

Offline is the default so the outputs are reproducible from data/raw alone. --refresh needs
APIFY_TOKEN and SEC_USER_AGENT and network access to api.apify.com and data.sec.gov.
Each company gets reports/drops/<ISO week>/ with the processed CSVs (no review text), charts,
backtest report, sources, limitations and a manifest of row counts and file hashes.
"""
from __future__ import annotations

import os
import shutil

import pandas as pd

import aggregate_quarters
import backtest
import build_dashboard
import charts
import factors
import fetch_products
import fetch_reviews
import join_revenue
import score_tone
import verify_company
from status import LIMITATIONS, history_status
from common import Paths, base_parser, file_sha256, frozen_hash, load_company, load_model_config, read_csv, tickers_from_args, today, utc_now_iso, write_csv, write_json, write_section
from sources.base import PRODUCT_COLUMNS, REVIEW_COLUMNS

def write_drop(root, ticker: str, as_of) -> tuple[str, dict]:
    paths = Paths(root, ticker)
    iso = pd.Timestamp(as_of).isocalendar()
    drop = paths.drops / f"{iso.year}-W{iso.week:02d}"
    if drop.exists():
        shutil.rmtree(drop)
    (drop / "charts").mkdir(parents=True)
    files = [
        paths.products_csv,
        paths.tone_scores_csv,
        paths.quarterly_tone_csv,
        paths.revenue_quarterly_csv,
        paths.revenue_join_csv,
        paths.factors_csv,
        paths.backtest_md,
        paths.backtest_json,
        paths.sources_md,
        paths.access_log,
    ]
    for f in files:
        if f.exists():
            shutil.copy(f, drop / f.name)
    for png in paths.charts.glob("*.png"):
        shutil.copy(png, drop / "charts" / png.name)
    status = history_status(paths)
    (drop / "limitations.md").write_text(LIMITATIONS + f"\n## Status this week\n\n- {status['label']}\n")
    manifest = {
        "ticker": ticker,
        "generated_at": utc_now_iso(),
        "as_of": str(as_of),
        "model_frozen_sha256": frozen_hash(load_model_config(root)),
        "history": status,
        "raw_review_text_included": False,
        "files": {
            p.relative_to(drop).as_posix(): {
                "sha256": file_sha256(p),
                **({"rows": int(sum(1 for _ in open(p)) - 1)} if p.suffix == ".csv" else {}),
            }
            for p in sorted(drop.rglob("*"))
            if p.is_file()
        },
    }
    write_json(manifest, drop / "manifest.json")
    return str(drop), status


def collection_status(paths: Paths, as_of) -> str:
    products = read_csv(paths.products_csv, PRODUCT_COLUMNS)
    reviews = read_csv(paths.reviews_csv, ["review_id", "review_date"])
    status = history_status(paths)
    if reviews.empty:
        head = "Nothing collected yet: no products and no reviews on file. Run weekly_drop.py --refresh with APIFY_TOKEN and SEC_USER_AGENT set."
    else:
        head = f"{len(products)} ASINs and {len(reviews)} reviews on file, {reviews['review_date'].min()} to {reviews['review_date'].max()}."
    return f"As of {as_of}. {head}\n\n{status['label']}. Quarters that are missing or unreliable are flagged, never imputed."


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"--refresh needs {', '.join(missing)} in the environment (see README > Setup).")


def run_company(root, ticker: str, refresh: bool, as_of, acknowledge: bool) -> dict:
    paths = Paths(root, ticker).ensure()
    load_company(root, ticker)
    if refresh:
        verify_company.verify(root, ticker)
        fetch_products.run(root, ticker)
        fetch_reviews.run(root, ticker)
    elif not paths.sources_md.exists():
        verify_company.verify(root, ticker, offline=True)
    if not paths.products_csv.exists():
        write_csv(pd.DataFrame(columns=PRODUCT_COLUMNS), paths.products_csv, PRODUCT_COLUMNS)
    if not paths.reviews_csv.exists():
        write_csv(pd.DataFrame(columns=REVIEW_COLUMNS), paths.reviews_csv, REVIEW_COLUMNS)
    score_tone.run(root, ticker)
    aggregate_quarters.run(root, ticker, as_of)
    join_revenue.run(root, ticker, refresh=refresh)
    factors.run(root, ticker)
    bt = backtest.run(root, ticker, acknowledge, as_of)
    charts.run(root, ticker)
    write_section(paths, "Collection status", collection_status(paths, as_of))
    drop, status = write_drop(root, ticker, as_of)
    return {"ticker": ticker, "drop": drop, "status": status, "backtest": bt}


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--refresh", action="store_true", help="fetch new data before rebuilding (needs APIFY_TOKEN, SEC_USER_AGENT)")
    p.add_argument("--as-of", help="treat this date as today (YYYY-MM-DD)")
    p.add_argument("--acknowledge-rule-change", action="store_true")
    p.add_argument("--no-dashboard", action="store_true")
    args = p.parse_args(argv)
    if args.refresh:
        require_env("APIFY_TOKEN", "SEC_USER_AGENT")
    as_of = args.as_of or str(today())
    for t in tickers_from_args(args):
        try:
            out = run_company(args.root, t, args.refresh, as_of, args.acknowledge_rule_change)
        except backtest.RuleChanged as e:
            raise SystemExit(f"{t}: {e}")
        bt = out["backtest"]
        print(f"{t}: {out['status']['label']}; backtest {bt['evaluated']}/{bt['required']} quarters; drop -> {out['drop']}")
    if not args.no_dashboard:
        path = build_dashboard.build(args.root, as_of=as_of)
        print(f"dashboard -> {path}")


if __name__ == "__main__":
    main()
