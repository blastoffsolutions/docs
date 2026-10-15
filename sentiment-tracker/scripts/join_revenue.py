"""Step 6a: pull SEC-reported revenue and join it to the tone quarters.

Revenue comes from SEC EDGAR XBRL companyfacts. For each fiscal quarter the first-filed value
is used (later filings restate prior periods as comparatives; the first report is what the
market saw). Fiscal Q4 is rarely tagged on its own, so it is derived as the annual 10-K value
minus Q1-Q3 and flagged `derived_q4`.

Each tone quarter q is joined to revenue for q (nowcast) and q+1 (lead).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from aggregate_quarters import composite, load_quarterly
from common import (
    Paths,
    base_parser,
    fiscal_quarter,
    load_company,
    log_access,
    quarter_bounds,
    quarter_ordinal,
    sec_session,
    shift_quarter,
    tickers_from_args,
    write_csv,
)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

REV_COLUMNS = [
    "fiscal_quarter",
    "period_start",
    "period_end",
    "revenue_usd",
    "concept",
    "form",
    "accn",
    "filed",
    "derived_q4",
    "filing_url",
    "revenue_yoy",
]

JOIN_COLUMNS = [
    "fiscal_quarter",
    "eligible",
    "n_reviews",
    "composite_mean_tone",
    "composite_tone_delta",
    "revenue_q_usd",
    "revenue_q_yoy",
    "revenue_q_filed",
    "revenue_q_accn",
    "revenue_q_url",
    "lag_days_q",
    "next_quarter",
    "revenue_next_usd",
    "revenue_next_yoy",
    "revenue_next_filed",
    "revenue_next_accn",
    "revenue_next_url",
    "lag_days_next",
    "yoy_accel_now",
    "yoy_accel_next",
    "revenue_status",
]


def filing_url(cik: str, accn: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/"


def _label(end: pd.Timestamp, fye: str) -> str:
    # 52/53-week filers can end a period in the first days of the next month (e.g. Oct 2);
    # pull those back so the period lands in the quarter it actually covers.
    if end.day <= 7:
        end = end - pd.Timedelta(days=7)
    return fiscal_quarter(end, fye)


def extract_quarterly_revenue(facts: dict, concepts: list[str], fye: str, cik: str) -> pd.DataFrame:
    usgaap = facts.get("facts", {}).get("us-gaap", {})
    quarterly: dict[str, dict] = {}
    annual: dict[int, dict] = {}
    for rank, concept in enumerate(concepts):
        for e in usgaap.get(concept, {}).get("units", {}).get("USD", []):
            form = str(e.get("form", ""))
            if not form.startswith(("10-Q", "10-K")) or not e.get("start"):
                continue
            start, end = pd.Timestamp(e["start"]), pd.Timestamp(e["end"])
            days = (end - start).days + 1
            rec = {
                "period_start": start.date().isoformat(),
                "period_end": end.date().isoformat(),
                "revenue_usd": float(e["val"]),
                "concept": concept,
                "form": form,
                "accn": e.get("accn", ""),
                "filed": e.get("filed", ""),
                "rank": rank,
            }
            key = (rank, rec["filed"])
            if 80 <= days <= 100:
                label = _label(end, fye)
                cur = quarterly.get(label)
                if cur is None or key < (cur["rank"], cur["filed"]):
                    quarterly[label] = rec
            elif 350 <= days <= 380 and form.startswith("10-K"):
                fy = int(_label(end, fye)[2:6])
                cur = annual.get(fy)
                if cur is None or key < (cur["rank"], cur["filed"]):
                    annual[fy] = rec
    rows = {q: {**r, "derived_q4": False} for q, r in quarterly.items()}
    for fy, a in annual.items():
        q4 = f"FY{fy}Q4"
        parts = [rows.get(f"FY{fy}Q{i}") for i in (1, 2, 3)]
        if q4 in rows or any(p is None for p in parts):
            continue
        rows[q4] = {
            **a,
            "period_start": None,
            "revenue_usd": a["revenue_usd"] - sum(p["revenue_usd"] for p in parts),
            "derived_q4": True,
        }
    if not rows:
        return pd.DataFrame(columns=REV_COLUMNS)
    df = pd.DataFrame([{"fiscal_quarter": q, **r} for q, r in rows.items()])
    df = df.sort_values("fiscal_quarter", key=lambda s: s.map(quarter_ordinal)).reset_index(drop=True)
    df["filing_url"] = df["accn"].map(lambda a: filing_url(cik, a) if a else "")
    by_q = df.set_index("fiscal_quarter")["revenue_usd"]
    df["revenue_yoy"] = df["fiscal_quarter"].map(
        lambda q: by_q[q] / by_q[shift_quarter(q, -4)] - 1 if shift_quarter(q, -4) in by_q.index else np.nan
    )
    return df.reindex(columns=REV_COLUMNS)


def join(comp: pd.DataFrame, rev: pd.DataFrame, quarter_end_of) -> pd.DataFrame:
    if comp.empty:
        return pd.DataFrame(columns=JOIN_COLUMNS)
    r = rev.set_index("fiscal_quarter") if not rev.empty else pd.DataFrame(columns=REV_COLUMNS).set_index("fiscal_quarter")

    def get(q, col):
        return r.at[q, col] if q in r.index else np.nan

    rows = []
    for c in comp.itertuples(index=False):
        q, nq = c.fiscal_quarter, shift_quarter(c.fiscal_quarter, 1)
        yoy_q, yoy_prev, yoy_next = get(q, "revenue_yoy"), get(shift_quarter(q, -1), "revenue_yoy"), get(nq, "revenue_yoy")

        def lag(label):
            filed = get(label, "filed")
            return (pd.Timestamp(filed) - quarter_end_of(label)).days if isinstance(filed, str) and filed else np.nan

        if r.empty:
            status = "no_revenue_data"
        elif nq in r.index:
            status = "reported"
        else:
            status = "next_quarter_not_yet_reported"
        rows.append(
            {
                "fiscal_quarter": q,
                "eligible": c.eligible,
                "n_reviews": c.n_reviews,
                "composite_mean_tone": c.composite_mean_tone,
                "composite_tone_delta": c.composite_tone_delta,
                "revenue_q_usd": get(q, "revenue_usd"),
                "revenue_q_yoy": yoy_q,
                "revenue_q_filed": get(q, "filed"),
                "revenue_q_accn": get(q, "accn"),
                "revenue_q_url": get(q, "filing_url"),
                "lag_days_q": lag(q),
                "next_quarter": nq,
                "revenue_next_usd": get(nq, "revenue_usd"),
                "revenue_next_yoy": yoy_next,
                "revenue_next_filed": get(nq, "filed"),
                "revenue_next_accn": get(nq, "accn"),
                "revenue_next_url": get(nq, "filing_url"),
                "lag_days_next": lag(nq),
                "yoy_accel_now": yoy_q - yoy_prev,
                "yoy_accel_next": yoy_next - yoy_q,
                "revenue_status": status,
            }
        )
    return pd.DataFrame(rows, columns=JOIN_COLUMNS)


def fetch_companyfacts(paths: Paths, company: dict) -> None:
    url = COMPANYFACTS_URL.format(cik=company["cik"])
    try:
        resp = sec_session().get(url)
        log_access(paths, "sec_companyfacts", "data.sec.gov", str(resp.status_code), url)
        resp.raise_for_status()
        paths.companyfacts_json.write_text(resp.text)
    except SystemExit:
        raise
    except Exception as e:
        log_access(paths, "sec_companyfacts", "data.sec.gov", "error", f"{type(e).__name__}: {e}")


def run(root, ticker: str, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    if refresh:
        fetch_companyfacts(paths, company)
    facts = json.loads(paths.companyfacts_json.read_text()) if paths.companyfacts_json.exists() else {}
    fye = company["fiscal_year_end"]
    rev = extract_quarterly_revenue(facts, company["sec"]["revenue_concepts"], fye, company["cik"])
    write_csv(rev, paths.revenue_quarterly_csv, REV_COLUMNS)
    joined = join(composite(load_quarterly(paths)), rev, lambda q: quarter_bounds(q, fye)[1])
    write_csv(joined, paths.revenue_join_csv, JOIN_COLUMNS)
    return rev, joined


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--refresh", action="store_true", help="download companyfacts from SEC first (needs SEC_USER_AGENT)")
    args = p.parse_args(argv)
    for t in tickers_from_args(args):
        rev, joined = run(args.root, t, args.refresh)
        print(f"{t}: {len(rev)} revenue quarters; {len(joined)} tone quarters joined")


if __name__ == "__main__":
    main()
