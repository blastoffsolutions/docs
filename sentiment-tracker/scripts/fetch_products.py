"""Step 2: map the catalog. Discover ASINs and variants, dedupe into products.csv, disclose gaps.

Sources: the configured catalog actor (official storefront / brand query) plus an optional
hand-curated companies/<ticker>/data/raw/asin_seed.csv (columns: asin, parent_asin, title, note).
"""
from __future__ import annotations

import pandas as pd

from common import Paths, base_parser, load_company, load_sources_config, log_access, read_csv, tickers_from_args, utc_now_iso, write_csv, write_section
from sources import get_source
from sources.base import PRODUCT_COLUMNS


def merge_products(existing: pd.DataFrame, new_rows: list[dict]) -> pd.DataFrame:
    """Union by ASIN; keep the earliest first_seen and latest last_seen; prefer non-empty fields."""
    new = pd.DataFrame(new_rows, columns=PRODUCT_COLUMNS)
    both = pd.concat([existing, new], ignore_index=True)
    if both.empty:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    both = both.dropna(subset=["asin"])
    both["parent_asin"] = both["parent_asin"].fillna(both["asin"])

    def pick(g: pd.DataFrame) -> pd.Series:
        row = g.ffill().bfill().iloc[-1].copy()
        row["first_seen"] = g["first_seen"].dropna().min() if g["first_seen"].notna().any() else pd.NA
        row["last_seen"] = g["last_seen"].dropna().max() if g["last_seen"].notna().any() else pd.NA
        vias = sorted({v for v in g["discovered_via"].dropna().astype(str) for v in v.split(";")})
        row["discovered_via"] = ";".join(vias)
        return row

    out = both.groupby("asin", sort=True, group_keys=False).apply(pick, include_groups=False).reset_index()
    return out.reindex(columns=PRODUCT_COLUMNS).sort_values(["parent_asin", "asin"]).reset_index(drop=True)


def seed_rows(paths: Paths, ticker: str) -> list[dict]:
    if not paths.asin_seed_csv.exists():
        return []
    seed = pd.read_csv(paths.asin_seed_csv, dtype=str)
    today = utc_now_iso()[:10]
    return [
        {
            "source": "manual_seed",
            "company": ticker,
            "parent_asin": r.get("parent_asin") or r["asin"],
            "asin": r["asin"],
            "variant_attrs": "",
            "title": r.get("title", ""),
            "brand": "",
            "category": "",
            "url": f"https://www.amazon.com/dp/{r['asin']}",
            "discovered_via": "asin_seed.csv",
            "first_seen": today,
            "last_seen": today,
        }
        for _, r in seed.fillna("").iterrows()
    ]


CATALOG_GAPS = """\
- **Survivorship.** The storefront and brand search list what is on sale now. Discontinued
  products, whose reviews may explain past quarters, are missing unless added to asin_seed.csv.
- **Result caps.** Catalog actors stop after `max_products` items per start URL; a brand with a
  deep catalog can be cut off. Compare the count below with the storefront's own count.
- **Third-party listings excluded.** Listings whose brand is not in the company's `brand_names`
  are dropped, including legitimate resellers of genuine product.
- **Variants share reviews.** Amazon pools reviews across a parent's variants, so tone is
  measured per parent ASIN, not per colour or size.
- **Amazon US only.** Other marketplaces and other retailers are not covered.
"""


def run(root, ticker: str) -> pd.DataFrame:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    sources_cfg = load_sources_config(root)
    existing = read_csv(paths.products_csv, PRODUCT_COLUMNS)
    rows = seed_rows(paths, company["ticker"])
    for name in company.get("sources", []):
        src = get_source(name, sources_cfg.get(name, {}))
        rows += src.fetch_products(company, lambda host, status, note: log_access(paths, name, host, status, note))
    merged = merge_products(existing, rows)
    write_csv(merged, paths.products_csv, PRODUCT_COLUMNS)
    body = CATALOG_GAPS + (
        f"\nCatalog as of {utc_now_iso()[:10]}: {merged['asin'].nunique()} ASINs under "
        f"{merged['parent_asin'].nunique()} parent ASINs.\n"
    )
    write_section(paths, "Catalog gaps", body)
    return merged


def main(argv=None):
    args = base_parser(__doc__).parse_args(argv)
    for t in tickers_from_args(args):
        df = run(args.root, t)
        print(f"{t}: {len(df)} ASINs, {df['parent_asin'].nunique()} parents")


if __name__ == "__main__":
    main()
