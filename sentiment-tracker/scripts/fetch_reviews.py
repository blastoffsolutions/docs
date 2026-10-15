"""Step 3: collect reviews from each configured source into reviews.csv (raw text, kept local).

reviews.csv is git-ignored: it holds third-party review text that this project does not
republish. Per-product fetch metadata (counts, earliest date, cap hits) goes to fetch_meta.json
and the access log so later steps can flag truncated quarters.
"""
from __future__ import annotations

import json

import pandas as pd

from common import Paths, base_parser, load_company, load_sources_config, log_access, read_csv, tickers_from_args, utc_now_iso, write_csv, write_section
from sources import get_source
from sources.base import PRODUCT_COLUMNS, REVIEW_COLUMNS


def merge_reviews(existing: pd.DataFrame, records: list[dict]) -> pd.DataFrame:
    new = pd.DataFrame(records, columns=REVIEW_COLUMNS)
    both = pd.concat([existing, new], ignore_index=True)
    if both.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    both = both.sort_values("fetched_at").drop_duplicates(["source", "review_id"], keep="first")
    return both.sort_values(["source", "review_date", "review_id"]).reset_index(drop=True)


def run(root, ticker: str) -> pd.DataFrame:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    sources_cfg = load_sources_config(root)
    products = read_csv(paths.products_csv, PRODUCT_COLUMNS)
    if products.empty:
        raise SystemExit(f"{ticker}: products.csv is empty; run fetch_products.py first")
    existing = read_csv(paths.reviews_csv, REVIEW_COLUMNS)
    meta = json.loads(paths.fetch_meta_json.read_text()) if paths.fetch_meta_json.exists() else {}
    new_records = []
    for name in company.get("sources", []):
        src = get_source(name, sources_cfg.get(name, {}))
        recs, src_meta = src.fetch_reviews(
            company, products.to_dict("records"), lambda host, status, note: log_access(paths, name, host, status, note)
        )
        new_records += [r.as_row() for r in recs]
        meta[name] = {"fetched_at": utc_now_iso(), "products": src_meta}
    merged = merge_reviews(existing, new_records)
    write_csv(merged, paths.reviews_csv, REVIEW_COLUMNS)
    paths.fetch_meta_json.write_text(json.dumps(meta, indent=2) + "\n")
    write_section(paths, "Review collection limits", describe_limits(meta, merged))
    return merged


def describe_limits(meta: dict, reviews: pd.DataFrame) -> str:
    lines = [
        "Amazon shows logged-out visitors a limited slice of each product's reviews and sorts them;",
        "this pipeline does not log in or pass cookies, so older history may simply be unavailable.",
        "",
        "| Source | Parent ASIN | Returned | Kept (US) | Earliest | Latest | Cap hit |",
        "|---|---|---|---|---|---|---|",
    ]
    for source, block in meta.items():
        for pid, m in sorted(block.get("products", {}).items()):
            lines.append(
                f"| {source} | {pid} | {m.get('returned')} | {m.get('kept_us')} | {m.get('earliest')} | "
                f"{m.get('latest')} | {'yes' if m.get('cap_hit') else 'no'} |"
            )
    lines.append("")
    lines.append(f"Total reviews on file: {len(reviews)}.")
    return "\n".join(lines)


def main(argv=None):
    args = base_parser(__doc__).parse_args(argv)
    for t in tickers_from_args(args):
        df = run(args.root, t)
        print(f"{t}: {len(df)} reviews on file")


if __name__ == "__main__":
    main()
