"""Add a company: resolve its SEC CIK and fiscal calendar, write config/companies/<ticker>.json.

    python scripts/new_company.py --ticker NKE --type manufacturer

Fields that need a human (Amazon storefront URL, brand names) are written as TODO-confirm.
"""
from __future__ import annotations

import json
import sys

from common import base_parser, company_config_path, sec_session, utc_now_iso

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_TYPES = ("manufacturer", "dtc", "retailer")


def find_cik(ticker: str, tickers_json: dict) -> str:
    for row in tickers_json.values():
        if str(row.get("ticker", "")).upper() == ticker.upper():
            return f"{int(row['cik_str']):010d}"
    raise SystemExit(f"{ticker} not found in SEC company_tickers.json")


def build_config(ticker: str, cik: str, submissions: dict, company_type: str) -> dict:
    fye = submissions.get("fiscalYearEnd") or "TODO-confirm"
    name = submissions.get("name", ticker)
    return {
        "ticker": ticker.upper(),
        "name": name,
        "cik": cik,
        "fiscal_year_end": fye,
        "fiscal_calendar_note": f"Fiscal year end {fye} (MMDD) per SEC submissions data.",
        "company_type": company_type,
        "segment_note": f"SIC: {submissions.get('sicDescription', 'unknown')}. TODO: describe how much of revenue runs through Amazon US.",
        "amazon": {
            "marketplace": "amazon.com",
            "storefront_url": "TODO-confirm",
            "brand_query": name.split(",")[0].split(" Inc")[0],
            "brand_names": ["TODO-confirm"],
        },
        "sources": ["amazon_apify"],
        "planned_sources": ["reddit", "app_store", "google_trends"],
        "sec": {"revenue_concepts": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]},
        "references": [
            {"fact": "CIK, name, fiscal year end", "url": SUBMISSIONS_URL.format(cik=cik), "checked": utc_now_iso()},
        ],
    }


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--type", choices=COMPANY_TYPES, default="manufacturer")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    args = p.parse_args(argv)
    if not args.ticker or len(args.ticker) != 1:
        p.error("pass exactly one --ticker")
    ticker = args.ticker[0].upper()
    out = company_config_path(args.root, ticker)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists; pass --force to overwrite")
    s = sec_session()
    cik = find_cik(ticker, s.get(TICKERS_URL).json())
    submissions = s.get(SUBMISSIONS_URL.format(cik=cik)).json()
    cfg = build_config(ticker, cik, submissions, args.type)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"wrote {out}; fill in the TODO-confirm fields, then run scripts/weekly_drop.py --ticker {ticker} --refresh")


if __name__ == "__main__":
    main()
