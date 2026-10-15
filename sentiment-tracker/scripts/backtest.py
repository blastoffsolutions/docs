"""Step 6b: evaluate the pre-registered rule on up to eight held-out quarters and list the misses.

The rule lives in models/tone_model_config.json together with a SHA-256 of everything that
defines the signal. If that content no longer matches the hash, this script stops: changing
the rule after seeing results would turn the backtest into curve fitting. Passing
--acknowledge-rule-change runs anyway and the report says so in its first section.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from aggregate_quarters import composite, load_quarterly
from common import (
    Paths,
    base_parser,
    to_jsonable,
    frozen_hash,
    load_company,
    load_model_config,
    quarter_ordinal,
    read_csv,
    shift_quarter,
    tickers_from_args,
    today,
    write_json,
)
from join_revenue import JOIN_COLUMNS, REV_COLUMNS


class RuleChanged(RuntimeError):
    pass


def check_rule(model_cfg: dict, acknowledge: bool) -> dict:
    actual = frozen_hash(model_cfg)
    recorded = model_cfg.get("frozen_sha256")
    ok = actual == recorded
    if not ok and not acknowledge:
        raise RuleChanged(
            f"the frozen model/rule content changed (recorded {recorded}, now {actual}). "
            "Re-run with --acknowledge-rule-change to evaluate anyway; the report will flag it."
        )
    return {"recorded": recorded, "actual": actual, "matches": ok, "acknowledged_change": (not ok) and acknowledge}


def sign(x) -> int:
    return 0 if pd.isna(x) or x == 0 else (1 if x > 0 else -1)


def binom_p_at_least(hits: int, n: int, p: float = 0.5) -> float | None:
    if n == 0:
        return None
    return float(sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(hits, n + 1)))


def evaluate(joined: pd.DataFrame, rev: pd.DataFrame, comp: pd.DataFrame, rule: dict) -> dict:
    down, up, n_hold = rule["down_threshold"], rule["up_threshold"], rule["holdout_quarters"]
    yoy = rev.set_index("fiscal_quarter")["revenue_yoy"] if not rev.empty else pd.Series(dtype=float)
    tags = comp.set_index("fiscal_quarter") if not comp.empty else pd.DataFrame()

    if joined.empty:
        candidates = joined
    else:
        j = joined.copy()
        j["eligible"] = j["eligible"].astype(str).str.lower().isin(["true", "1"])
        candidates = j[j["eligible"] & (j["revenue_status"] == "reported") & j["yoy_accel_next"].notna()]
        candidates = candidates.sort_values("fiscal_quarter", key=lambda s: s.map(quarter_ordinal))
    holdout = candidates.tail(n_hold)

    rows = []
    for r in holdout.itertuples(index=False):
        delta = float(r.composite_tone_delta)
        call = "DOWN" if delta <= down else "UP" if delta >= up else "NO CALL"
        actual = sign(r.yoy_accel_next)
        pers = sign(yoy.get(shift_quarter(r.fiscal_quarter, -1), np.nan) - yoy.get(shift_quarter(r.fiscal_quarter, -2), np.nan))
        if call == "NO CALL":
            outcome = "NO CALL"
        else:
            outcome = "HIT" if actual == (-1 if call == "DOWN" else 1) else "MISS"
        t = tags.loc[r.fiscal_quarter] if r.fiscal_quarter in tags.index else None
        rows.append(
            {
                "fiscal_quarter": r.fiscal_quarter,
                "n_reviews": int(r.n_reviews),
                "tone_delta": delta,
                "call": call,
                "revenue_yoy_q": r.revenue_q_yoy,
                "revenue_yoy_next": r.revenue_next_yoy,
                "yoy_accel_next": r.yoy_accel_next,
                "actual": {1: "accelerated", -1: "decelerated", 0: "flat"}[actual],
                "outcome": outcome,
                "persistence_call": {1: "UP", -1: "DOWN", 0: "NO CALL"}[pers],
                "persistence_hit": None if pers == 0 else pers == actual,
                "nowcast_accel": r.yoy_accel_now,
                "nowcast_outcome": None if call == "NO CALL" or pd.isna(r.yoy_accel_now) else ("HIT" if sign(r.yoy_accel_now) == (-1 if call == "DOWN" else 1) else "MISS"),
                "defect_rate": None if t is None else t["defect_rate"],
                "quality_decline_rate": None if t is None else t["quality_decline_rate"],
                "price_rate": None if t is None else t["price_rate"],
                "revenue_next_filed": r.revenue_next_filed,
                "revenue_next_url": r.revenue_next_url,
            }
        )
    calls = [r for r in rows if r["outcome"] != "NO CALL"]
    hits = sum(r["outcome"] == "HIT" for r in calls)
    pers_scored = [r for r in calls if r["persistence_hit"] is not None]
    return {
        "eligible_quarters": int(len(candidates)),
        "evaluated": len(rows),
        "required": n_hold,
        "insufficient": len(rows) < n_hold,
        "calls": len(calls),
        "hits": hits,
        "misses": len(calls) - hits,
        "no_calls": len(rows) - len(calls),
        "hit_rate": hits / len(calls) if calls else None,
        "p_value_vs_50": binom_p_at_least(hits, len(calls)),
        "persistence_hits_on_same_calls": sum(bool(r["persistence_hit"]) for r in pers_scored),
        "persistence_scored": len(pers_scored),
        "rows": rows,
    }


def fmt_pct(x, signed=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%"


def fmt_num(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2f}"


def render_report(company: dict, model_cfg: dict, rule_check: dict, res: dict, as_of: str, data_note: str) -> str:
    rule = model_cfg["preregistered_rule"]
    L = [
        f"# Backtest: {company['name']} ({company['ticker']})",
        "",
        f"As of {as_of}. Research only; not financial advice and not a trading system.",
        "",
        "## 1. Pre-registered rule (fixed before any results)",
        "",
        f"Rule `{rule['id']}`, frozen {model_cfg['frozen_at']}.",
        "",
        f"- **Question:** {rule['question']}",
        f"- **Signal:** {rule['signal']}",
        f"- **Eligible quarter:** {rule['eligible_quarter']}",
        f"- **Call:** DOWN if {rule['call']['DOWN']}; UP if {rule['call']['UP']}; otherwise NO CALL.",
        f"- **Primary target:** {rule['primary_target']}",
        f"- **Secondary (reported only):** {rule['secondary_target']}",
        f"- **Holdout:** {rule['holdout_selection']}",
        f"- **Comparisons:** {'; '.join(rule['baselines_for_comparison'])}",
        f"- **Statistics:** {rule['statistics']}",
        f"- **No fitting:** {rule['no_fitting']}",
        "",
        f"Frozen-content SHA-256: `{rule_check['recorded']}`. "
        + ("Current content matches." if rule_check["matches"] else f"**Current content does NOT match** (`{rule_check['actual']}`); run with --acknowledge-rule-change. Treat these results as exploratory, not a pre-registered test."),
        "",
        "## 2. Data status",
        "",
        data_note,
        "",
    ]
    if res["insufficient"]:
        L += [
            f"**INSUFFICIENT HISTORY: {res['evaluated']} of {res['required']} held-out quarters available.** "
            "Missing quarters are not imputed. The rule needs 12 complete, non-sparse quarters of reviews "
            "(4 to form the first baseline, then 8 to evaluate) plus the following quarter's SEC revenue.",
            "",
        ]
    L += ["## 3. Results", ""]
    if res["rows"]:
        L += [
            "| Quarter | Reviews | Tone delta | Call | Revenue YoY (q) | Revenue YoY (q+1) | Actual | Outcome | Persistence |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in res["rows"]:
            L.append(
                f"| {r['fiscal_quarter']} | {r['n_reviews']} | {fmt_num(r['tone_delta'])} | {r['call']} | "
                f"{fmt_pct(r['revenue_yoy_q'])} | {fmt_pct(r['revenue_yoy_next'])} | {r['actual']} | **{r['outcome']}** | "
                f"{r['persistence_call']} |"
            )
        L.append("")
        if res["calls"]:
            L.append(
                f"Calls: {res['calls']} · hits: {res['hits']} · misses: {res['misses']} · no-calls: {res['no_calls']}. "
                f"Hit rate {fmt_pct(res['hit_rate'], signed=False)}; one-sided binomial p = {res['p_value_vs_50']:.3f} against a coin flip. "
                f"Persistence baseline on the same quarters: {res['persistence_hits_on_same_calls']} of {res['persistence_scored']}."
            )
        else:
            L.append("The rule made no calls on the evaluated quarters.")
        L += ["", f"With {res['calls']} calls this test has almost no statistical power; a few lucky or unlucky quarters swing it.", ""]
    else:
        L += ["No quarter qualified for evaluation.", ""]
    L += ["## 4. Misses", ""]
    misses = [r for r in res["rows"] if r["outcome"] == "MISS"]
    if misses:
        L += ["| Quarter | Call | Tone delta | Next-quarter growth change | Defect | Quality decline | Price |", "|---|---|---|---|---|---|---|"]
        for r in misses:
            L.append(
                f"| {r['fiscal_quarter']} | {r['call']} | {fmt_num(r['tone_delta'])} | {fmt_pct(r['yoy_accel_next'])} | "
                f"{fmt_pct(r['defect_rate'], False)} | {fmt_pct(r['quality_decline_rate'], False)} | {fmt_pct(r['price_rate'], False)} |"
            )
        L.append("")
    else:
        L += ["None recorded." + (" (Nothing was evaluated.)" if not res["rows"] else ""), ""]
    L += ["## 5. Secondary: nowcast (reported only)", ""]
    nc = [r for r in res["rows"] if r["nowcast_outcome"]]
    if nc:
        L.append(f"Same calls against the change in quarter q's own growth: {sum(r['nowcast_outcome'] == 'HIT' for r in nc)} of {len(nc)} hits.")
    else:
        L.append("Nothing to report.")
    L += ["", "See README.md for limitations and data gaps.", ""]
    return "\n".join(L)


def data_note_for(qt: pd.DataFrame, rev: pd.DataFrame) -> str:
    if qt.empty:
        tone = "No reviews collected yet, so there are no tone quarters."
    else:
        counts = qt["history_status"].value_counts().to_dict()
        tone = f"{qt['fiscal_quarter'].nunique()} fiscal quarters of reviews on file; source-quarter status counts: {counts}."
    if rev.empty:
        revn = "No SEC revenue data on file (companyfacts not yet downloaded)."
    else:
        revn = f"SEC revenue on file for {len(rev)} fiscal quarters ({rev['fiscal_quarter'].iloc[0]} to {rev['fiscal_quarter'].iloc[-1]})."
    return f"{tone} {revn}"


def run(root, ticker: str, acknowledge: bool = False, as_of=None) -> dict:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    model_cfg = load_model_config(root)
    rule_check = check_rule(model_cfg, acknowledge)
    qt = load_quarterly(paths)
    comp = composite(qt)
    joined = read_csv(paths.revenue_join_csv, JOIN_COLUMNS)
    rev = read_csv(paths.revenue_quarterly_csv, REV_COLUMNS)
    res = evaluate(joined, rev, comp, model_cfg["preregistered_rule"])
    as_of = str(as_of or today())
    paths.backtest_md.write_text(render_report(company, model_cfg, rule_check, res, as_of, data_note_for(qt, rev)))
    out = {"ticker": company["ticker"], "as_of": as_of, "rule_id": model_cfg["preregistered_rule"]["id"], "rule_check": rule_check, **res}
    out = to_jsonable(out)
    write_json(out, paths.backtest_json)
    return out


def main(argv=None):
    p = base_parser(__doc__)
    p.add_argument("--acknowledge-rule-change", action="store_true")
    p.add_argument("--as-of")
    args = p.parse_args(argv)
    for t in tickers_from_args(args):
        try:
            res = run(args.root, t, args.acknowledge_rule_change, args.as_of)
        except RuleChanged as e:
            raise SystemExit(f"{t}: {e}")
        state = "INSUFFICIENT" if res["insufficient"] else "complete"
        print(f"{t}: evaluated {res['evaluated']}/{res['required']} ({state}); hits {res['hits']}, misses {res['misses']}")


if __name__ == "__main__":
    main()
