"""Step 1: confirm the company and its fiscal calendar against SEC EDGAR; record sources.

Writes companies/<ticker>/data/raw/company_profile.json and sources.md. When SEC cannot be
reached, the profile is written from config with verified=false and the failure is logged.
"""
from __future__ import annotations

from common import (
    Paths,
    base_parser,
    load_company,
    load_sources_config,
    log_access,
    sec_session,
    tickers_from_args,
    utc_now_iso,
    write_json,
)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def write_sources_md(paths: Paths, company: dict, profile: dict, sources_cfg: dict) -> None:
    lines = [
        f"# Sources: {company['name']} ({company['ticker']})",
        "",
        f"Generated {utc_now_iso()}. Raw review text is kept local and is not republished.",
        "",
        "## Company and fiscal calendar",
        "",
        f"- SEC submissions: {SUBMISSIONS_URL.format(cik=company['cik'])} "
        f"({'verified ' + profile['verified_at'] if profile['verified'] else 'not reached in this run'})",
        f"- Fiscal year end (MMDD): {company['fiscal_year_end']}. {company.get('fiscal_calendar_note', '')}",
    ]
    for ref in company.get("references", []):
        lines.append(f"- {ref['fact']}: {ref['url']} (checked {ref['checked']})")
    lines += [
        "",
        "## Revenue",
        "",
        f"- SEC XBRL companyfacts: https://data.sec.gov/api/xbrl/companyfacts/CIK{company['cik']}.json",
        f"- Concepts, in fallback order: {', '.join(company['sec']['revenue_concepts'])}",
        "",
        "## Reviews and catalog",
        "",
        f"- Amazon US storefront: {company['amazon'].get('storefront_url')}",
    ]
    for name in company.get("sources", []):
        s = sources_cfg.get(name, {})
        if name == "amazon_apify":
            lines.append(f"- Apify actor (catalog): https://apify.com/{s.get('products_actor')}")
            lines.append(f"- Apify actor (reviews): https://apify.com/{s.get('reviews_actor')}")
        else:
            lines.append(f"- {name}")
    if company.get("planned_sources"):
        lines.append(f"- Planned, not yet implemented: {', '.join(company['planned_sources'])}")
    paths.sources_md.write_text("\n".join(lines) + "\n")


def verify(root, ticker: str, offline: bool = False) -> dict:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    profile = {
        "ticker": company["ticker"],
        "name": company["name"],
        "cik": company["cik"],
        "fiscal_year_end": company["fiscal_year_end"],
        "company_type": company.get("company_type"),
        "verified": False,
        "verified_at": None,
        "sec_name": None,
        "sec_fiscal_year_end": None,
        "mismatches": [],
    }
    if not offline:
        url = SUBMISSIONS_URL.format(cik=company["cik"])
        try:
            resp = sec_session().get(url)
            log_access(paths, "sec_submissions", "data.sec.gov", str(resp.status_code), url)
            resp.raise_for_status()
            sub = resp.json()
            profile.update(
                verified=True,
                verified_at=utc_now_iso(),
                sec_name=sub.get("name"),
                sec_fiscal_year_end=sub.get("fiscalYearEnd"),
                sec_tickers=sub.get("tickers"),
                sic=sub.get("sicDescription"),
            )
            if sub.get("fiscalYearEnd") and sub["fiscalYearEnd"] != company["fiscal_year_end"]:
                profile["mismatches"].append(
                    f"config fiscal_year_end {company['fiscal_year_end']} != SEC {sub['fiscalYearEnd']}"
                )
            if company["ticker"] not in (sub.get("tickers") or []):
                profile["mismatches"].append(f"ticker {company['ticker']} not in SEC tickers {sub.get('tickers')}")
        except SystemExit:
            raise
        except Exception as e:  # network policy, DNS, HTTP errors: log and carry on unverified
            log_access(paths, "sec_submissions", "data.sec.gov", "error", f"{type(e).__name__}: {e}")
    write_json(profile, paths.company_profile)
    write_sources_md(paths, company, profile, load_sources_config(root))
    return profile


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--offline", action="store_true")
    args = p.parse_args(argv)
    for t in tickers_from_args(args):
        prof = verify(args.root, t, args.offline)
        state = "verified" if prof["verified"] else "NOT verified (see access_log.md)"
        print(f"{t}: {state}; mismatches: {prof['mismatches'] or 'none'}")


if __name__ == "__main__":
    main()
