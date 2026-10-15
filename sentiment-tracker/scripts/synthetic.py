"""SYNTHETIC DATA, for tests and the dashboard's sample preview only.

Builds a fictional company, "Sample Co (synthetic)", in a throwaway root directory: fake
reviews with templated text, a fake SEC companyfacts file, and fetch metadata with one capped
product. The numbers are constructed so the preview shows every state the dashboard can be in
(a truncated quarter, a sparse quarter, eight evaluated quarters with hits and misses, a
quarter in progress). They say nothing about whether the method works.

It refuses to write into the real tracker root, so synthetic rows can never mix with real data.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from common import DEFAULT_ROOT, quarter_bounds, quarter_ordinal, quarter_range, shift_quarter
from sources.base import PRODUCT_COLUMNS, REVIEW_COLUMNS

TICKER = "SAMPLE"
FYE = "0630"
FIRST_Q, LAST_Q = "FY2022Q4", "FY2026Q3"  # 16 quarters of reviews; the last is still in progress
AS_OF = "2026-02-20"
REVENUE_FROM = "FY2020Q1"
PRODUCTS = [f"SAMPLE-P{i}" for i in range(1, 7)]

# Latent product quality per review quarter. Dips and recoveries drive both tone and revenue.
LATENT = [0.0, 0.0, 0.1, 0.0, -0.1, 0.05, 0.1, -0.7, -0.35, 0.15, 0.55, 0.3, -0.05, -0.6, 0.0, 0.2]
# Revenue-growth noise added after each quarter's latent effect; two quarters are pushed against
# the signal so the preview shows misses.
NOISE = [0.004, -0.003, 0.002, 0.0, 0.003, -0.002, 0.001, 0.002, 0.02, 0.002, -0.004, -0.035, 0.003, 0.004, -0.002, 0.0]
COUNTS = [70, 14, 96, 104, 99, 110, 118, 121, 109, 114, 131, 140, 128, 122, 135, 44]

TEMPLATES = {
    2: [
        ("Love it", "Excellent build quality and it works perfectly. Best purchase this year."),
        ("Fantastic", "Great design, comfortable, and the battery lasts forever. Highly recommend."),
        ("Superb", "Works flawlessly out of the box. Really happy with the performance."),
    ],
    1: [
        ("Good", "Works well for daily use. Solid build."),
        ("Pretty good", "Nice design and decent battery life, good value for the money."),
        ("Happy with it", "Setup was easy and it works fine."),
    ],
    0: [
        ("Okay", "It is okay. Does the job, nothing special."),
        ("Average", "Works as described. Software is a bit clunky but usable."),
        ("Fine", "Neutral on this one; the size is fine."),
    ],
    -1: [
        ("Disappointed", "The button started sticking after a month. Not as good as my old one."),
        ("Meh", "Overpriced for what you get and the build feels cheap."),
        ("Not great", "Connection keeps dropping. It will disconnect randomly, which is annoying."),
    ],
    -2: [
        ("Terrible", "Stopped working after two weeks. Defective and a total waste of money."),
        ("Awful", "Broke within a month, quality has gone down a lot since the previous version. Do not buy."),
        ("Junk", "Dead on arrival and the replacement also failed. Horrible."),
    ],
    "shipping": [
        ("Late package", "Package arrived late and the box was damaged."),
        ("Delivery issue", "The seller shipped the wrong item and the refund took weeks."),
    ],
}


def _guard(root: Path) -> None:
    if Path(root).resolve() == DEFAULT_ROOT.resolve():
        raise PermissionError("synthetic data must never be written into the real tracker root")


def make_reviews(rng: np.random.Generator) -> tuple[pd.DataFrame, dict]:
    rows = []
    quarters = quarter_range(FIRST_Q, LAST_Q)
    for qi, q in enumerate(quarters):
        start, end = quarter_bounds(q, FYE)
        if q == LAST_Q:
            end = pd.Timestamp(AS_OF) - pd.Timedelta(days=1)
        span = (end - start).days
        for k in range(COUNTS[qi]):
            pid = PRODUCTS[k % len(PRODUCTS)] if COUNTS[qi] >= 30 else PRODUCTS[k % 2]
            date = start + pd.Timedelta(days=int(rng.integers(0, span + 1)))
            if rng.random() < 0.04:
                title, body = TEMPLATES["shipping"][int(rng.integers(0, 2))]
                rating = int(rng.integers(1, 3))
            else:
                level = int(np.clip(round(0.9 + 1.6 * LATENT[qi] + rng.normal(0, 1.05)), -2, 2))
                title, body = TEMPLATES[level][int(rng.integers(0, 3))]
                rating = int(np.clip(level + 3 + (rng.random() < 0.15) * rng.choice([-1, 1]), 1, 5))
            rows.append(
                {
                    "source": "amazon_apify",
                    "company": TICKER,
                    "review_id": f"SYN-{q}-{k:04d}",
                    "product_id": pid,
                    "parent_id": pid,
                    "review_date": date.date().isoformat(),
                    "rating": rating,
                    "title": f"[SYNTHETIC] {title}",
                    "body": body,
                    "verified": True,
                    "variant": "",
                    "locale": "en_US",
                    "source_url": "https://example.invalid/synthetic",
                    "fetched_at": AS_OF + "T00:00:00+00:00",
                }
            )
    df = pd.DataFrame(rows, columns=REVIEW_COLUMNS)
    # Product 1 hit the source's result cap: nothing on file before mid-way through the first quarter.
    q0_start, q0_end = quarter_bounds(FIRST_Q, FYE)
    cut = q0_start + (q0_end - q0_start) / 2
    df = df[~((df["parent_id"] == PRODUCTS[0]) & (pd.to_datetime(df["review_date"]) < cut))]
    meta = {
        "amazon_apify": {
            "fetched_at": AS_OF,
            "products": {
                p: {
                    "returned": int((df["parent_id"] == p).sum()),
                    "kept_us": int((df["parent_id"] == p).sum()),
                    "earliest": df.loc[df["parent_id"] == p, "review_date"].min(),
                    "latest": df.loc[df["parent_id"] == p, "review_date"].max(),
                    "cap": 1000,
                    "cap_hit": p == PRODUCTS[0],
                }
                for p in PRODUCTS
            },
        }
    }
    return df.reset_index(drop=True), meta


def make_companyfacts() -> dict:
    """Quarterly 10-Q values for Q1-Q3 and annual 10-K values, so fiscal Q4 must be derived."""
    rev_quarters = quarter_range(REVENUE_FROM, shift_quarter(LAST_Q, -1))
    review_quarters = quarter_range(FIRST_Q, LAST_Q)
    season = {1: 0.95, 2: 1.25, 3: 0.9, 4: 0.9}
    growth = {}
    g = 0.05
    for q in rev_quarters:
        growth[q] = g
        if q in review_quarters:
            i = review_quarters.index(q)
            base = np.mean(LATENT[max(0, i - 4) : i]) if i >= 4 else 0.0
            g = g + 0.06 * (LATENT[i] - base) + NOISE[i]
        else:
            g = g + 0.002 * ((quarter_ordinal(q) % 3) - 1)
    revenue = {}
    for q in rev_quarters:
        prior = shift_quarter(q, -4)
        revenue[q] = revenue[prior] * (1 + growth[q]) if prior in revenue else 1_000_000_000 * season[int(q[-1])]
    entries = []
    fys = sorted({int(q[2:6]) for q in rev_quarters})
    for q in rev_quarters:
        fy, qn = int(q[2:6]), int(q[-1])
        start, end = quarter_bounds(q, FYE)
        filed = (end + pd.Timedelta(days=40)).date().isoformat()
        if qn < 4:
            entries.append({"start": start.date().isoformat(), "end": end.date().isoformat(), "val": round(revenue[q]), "accn": f"0000000000-{fy % 100:02d}-00000{qn}", "fy": fy, "fp": f"Q{qn}", "form": "10-Q", "filed": filed})
    for fy in fys:
        qs = [f"FY{fy}Q{i}" for i in range(1, 5)]
        if all(q in revenue for q in qs):
            start, end = quarter_bounds(qs[0], FYE)[0], quarter_bounds(qs[-1], FYE)[1]
            entries.append({"start": start.date().isoformat(), "end": end.date().isoformat(), "val": round(sum(revenue[q] for q in qs)), "accn": f"0000000000-{fy % 100:02d}-000004", "fy": fy, "fp": "FY", "form": "10-K", "filed": (end + pd.Timedelta(days=60)).date().isoformat()})
    return {"cik": 0, "entityName": "Sample Co (synthetic)", "facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": entries}}}}}


def write_sample(root: Path, real_root: Path = DEFAULT_ROOT, seed: int = 7) -> Path:
    """Create a complete synthetic workspace under `root` and return it."""
    root = Path(root)
    _guard(root)
    (root / "config" / "companies").mkdir(parents=True, exist_ok=True)
    (root / "models").mkdir(parents=True, exist_ok=True)
    shutil.copy(real_root / "models" / "tone_model_config.json", root / "models" / "tone_model_config.json")
    shutil.copy(real_root / "config" / "sources.json", root / "config" / "sources.json")
    company = {
        "ticker": TICKER,
        "name": "Sample Co (synthetic)",
        "cik": "0000000000",
        "fiscal_year_end": FYE,
        "fiscal_calendar_note": "Fictional company; fiscal year ends June 30.",
        "company_type": "manufacturer",
        "segment_note": "Synthetic data generated by scripts/synthetic.py to preview the dashboard. Not a real company.",
        "amazon": {"marketplace": "amazon.com", "storefront_url": "https://example.invalid", "brand_query": "Sample Co", "brand_names": ["Sample Co"]},
        "sources": ["amazon_apify"],
        "planned_sources": ["reddit", "app_store", "google_trends"],
        "sec": {"revenue_concepts": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]},
        "references": [],
        "synthetic": True,
    }
    (root / "config" / "companies" / "sample.json").write_text(json.dumps(company, indent=2))
    raw = root / "companies" / "sample" / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    reviews, meta = make_reviews(np.random.default_rng(seed))
    reviews.to_csv(raw / "reviews.csv", index=False)
    (raw / "fetch_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    (raw / "sec_companyfacts.json").write_text(json.dumps(make_companyfacts()))
    pd.DataFrame(
        [{"source": "synthetic", "company": TICKER, "parent_asin": p, "asin": p, "variant_attrs": "", "title": f"Synthetic product {i + 1}", "brand": "Sample Co", "category": "", "url": "https://example.invalid", "discovered_via": "synthetic", "first_seen": FIRST_Q, "last_seen": AS_OF} for i, p in enumerate(PRODUCTS)],
        columns=PRODUCT_COLUMNS,
    ).to_csv(raw / "products.csv", index=False)
    return root
