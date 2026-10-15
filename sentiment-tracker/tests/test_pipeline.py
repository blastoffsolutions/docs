"""Pipeline tests. Everything runs in temporary directories; synthetic data never touches companies/."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

import aggregate_quarters
import backtest
import build_dashboard
import factors
import join_revenue
import score_tone
import synthetic
import weekly_drop
from common import DEFAULT_ROOT, Paths, fiscal_quarter, frozen_hash, load_model_config, quarter_bounds, shift_quarter
from fetch_products import merge_products
from fetch_reviews import merge_reviews
from new_company import build_config, find_cik
from sources.amazon_apify import check_input_is_clean, normalize_review
from sources.base import PRODUCT_COLUMNS, REVIEW_COLUMNS


@pytest.fixture(scope="module")
def sample_root(tmp_path_factory):
    root = synthetic.write_sample(tmp_path_factory.mktemp("sample"))
    t = synthetic.TICKER
    score_tone.run(root, t)
    aggregate_quarters.run(root, t, synthetic.AS_OF)
    join_revenue.run(root, t)
    factors.run(root, t)
    backtest.run(root, t, as_of=synthetic.AS_OF)
    return root


# ------------------------------------------------------------------ fiscal calendar


@pytest.mark.parametrize(
    "date,fye,expected",
    [
        ("2025-03-31", "0331", "FY2025Q4"),  # Logitech: FY ends March 31
        ("2025-04-01", "0331", "FY2026Q1"),
        ("2025-12-31", "0331", "FY2026Q3"),
        ("2025-05-31", "0531", "FY2025Q4"),  # Nike: FY ends May 31
        ("2025-06-01", "0531", "FY2026Q1"),
        ("2026-02-28", "0531", "FY2026Q3"),
        ("2025-01-15", "1231", "FY2025Q1"),  # calendar-year filer
        ("2025-12-31", "1231", "FY2025Q4"),
    ],
)
def test_fiscal_quarter_edges(date, fye, expected):
    assert fiscal_quarter(date, fye) == expected


def test_quarter_bounds_round_trip():
    for fye in ("0331", "0531", "0630", "1231"):
        for q in ("FY2025Q1", "FY2025Q2", "FY2025Q3", "FY2025Q4"):
            start, end = quarter_bounds(q, fye)
            assert fiscal_quarter(start, fye) == q
            assert fiscal_quarter(end, fye) == q
            assert fiscal_quarter(end + pd.Timedelta(days=1), fye) == shift_quarter(q, 1)


# ------------------------------------------------------------------ company setup


def test_new_company_config_from_sec_json():
    tickers = {"0": {"cik_str": 320187, "ticker": "NKE", "title": "NIKE, Inc."}}
    cik = find_cik("nke", tickers)
    assert cik == "0000320187"
    cfg = build_config("NKE", cik, {"name": "NIKE, Inc.", "fiscalYearEnd": "0531", "sicDescription": "Rubber & Plastics Footwear"}, "manufacturer")
    assert cfg["fiscal_year_end"] == "0531"
    assert cfg["amazon"]["storefront_url"] == "TODO-confirm"
    assert cfg["sources"] == ["amazon_apify"]


def test_shipped_company_configs_are_consistent():
    for t, fye, cik in (("logi", "0331", "0001032975"), ("nke", "0531", "0000320187")):
        cfg = json.loads((DEFAULT_ROOT / "config" / "companies" / f"{t}.json").read_text())
        assert cfg["fiscal_year_end"] == fye and cfg["cik"] == cik


# ------------------------------------------------------------------ collection


def test_product_dedupe_keeps_first_and_last_seen():
    base = dict(source="amazon_apify", company="X", variant_attrs="", brand="X", category="", url="u", discovered_via="a")
    rows = [
        {**base, "parent_asin": "P1", "asin": "A1", "title": "Mouse", "first_seen": "2026-01-01", "last_seen": "2026-01-01"},
        {**base, "parent_asin": "P1", "asin": "A1", "title": "Mouse", "first_seen": "2026-02-01", "last_seen": "2026-02-01", "discovered_via": "b"},
        {**base, "parent_asin": "P1", "asin": "A2", "title": "Mouse (red)", "first_seen": "2026-02-01", "last_seen": "2026-02-01"},
    ]
    out = merge_products(pd.DataFrame(columns=PRODUCT_COLUMNS), rows)
    assert sorted(out["asin"]) == ["A1", "A2"]
    a1 = out.set_index("asin").loc["A1"]
    assert a1["first_seen"] == "2026-01-01" and a1["last_seen"] == "2026-02-01" and a1["discovered_via"] == "a;b"


def test_review_dedupe_by_source_and_id():
    r = {c: "" for c in REVIEW_COLUMNS}
    rows = [
        {**r, "source": "amazon_apify", "review_id": "R1", "review_date": "2025-01-01", "fetched_at": "2026-01-02"},
        {**r, "source": "amazon_apify", "review_id": "R1", "review_date": "2025-01-01", "fetched_at": "2026-01-01"},
        {**r, "source": "reddit", "review_id": "R1", "review_date": "2025-01-01", "fetched_at": "2026-01-01"},
    ]
    out = merge_reviews(pd.DataFrame(columns=REVIEW_COLUMNS), rows)
    assert len(out) == 2
    assert out[out["source"] == "amazon_apify"]["fetched_at"].item() == "2026-01-01"


def test_normalize_review_keeps_us_only_and_parses_dates():
    us = {"reviewId": "R1", "date": "Reviewed in the United States on March 3, 2024", "ratingScore": 4, "reviewTitle": "t", "reviewDescription": "b", "productAsin": "A1"}
    de = {**us, "reviewId": "R2", "date": "Reviewed in Germany on March 3, 2024"}
    rec = normalize_review(us, "X", "P1", "2026-01-01")
    assert rec.review_date == "2024-03-03" and rec.rating == 4.0 and rec.product_id == "A1" and rec.parent_id == "P1"
    assert normalize_review(de, "X", "P1", "2026-01-01") is None


def test_actor_input_with_cookies_is_refused():
    with pytest.raises(PermissionError):
        check_input_is_clean({"productUrls": [{"url": "x"}], "cookies": [{"name": "session-id"}]})
    check_input_is_clean({"productUrls": [{"url": "x"}], "maxReviews": 10})


# ------------------------------------------------------------------ scoring


@pytest.fixture(scope="module")
def model():
    return score_tone.ToneModel(load_model_config(DEFAULT_ROOT))


def test_tone_scale_and_tags(model):
    great = model.score("Love it", "Excellent build quality, works perfectly.", 5)
    awful = model.score("Junk", "Stopped working after two weeks. Defective.", 1)
    assert great["tone"] == 2 and awful["tone"] == -2
    assert awful["tag_defect"] and not great["tag_defect"]
    assert model.score("Meh", "Not as good as my old one.", 2)["tag_quality_decline"]
    assert model.score("Hmm", "Overpriced at $129 for what you get.", 2)["tag_price"]
    for rating in (1, 2, 3, 4, 5, None):
        assert -2 <= model.score("x", "y", rating)["tone"] <= 2


def test_seller_shipping_only_is_separated(model):
    ship = model.score("Late package", "Package arrived late and the box was damaged.", 1)
    mixed = model.score("Bad", "Package arrived late and the mouse stopped working.", 1)
    assert ship["seller_shipping_only"] is True
    assert mixed["seller_shipping_only"] is False and mixed["tag_defect"]


# ------------------------------------------------------------------ aggregation


def test_flags_baseline_and_truncation(sample_root):
    qt = pd.read_csv(Paths(sample_root, "SAMPLE").quarterly_tone_csv)
    for col in ("fiscal_quarter", "mean_tone", "n_reviews", "baseline_prior4_mean", "sparse_flag"):
        assert col in qt.columns  # acceptance criterion
    by_q = qt.set_index("fiscal_quarter")
    assert by_q.loc["FY2022Q4", "coverage_flag"] == "truncated"  # a capped product starts mid-quarter
    assert bool(by_q.loc["FY2023Q1", "sparse_flag"]) and by_q.loc["FY2023Q1", "history_status"] == "sparse"
    assert by_q.loc["FY2026Q3", "coverage_flag"] == "partial_quarter"
    # The sparse quarter blocks baselines for the four quarters after it; none are imputed.
    for q in ("FY2023Q2", "FY2023Q3", "FY2023Q4", "FY2024Q1"):
        assert pd.isna(by_q.loc[q, "baseline_prior4_mean"])
    prior = by_q.loc[["FY2023Q2", "FY2023Q3", "FY2023Q4", "FY2024Q1"], "mean_tone"].mean()
    assert by_q.loc["FY2024Q2", "baseline_prior4_mean"] == pytest.approx(prior, rel=1e-4)  # CSVs keep 6 significant digits
    assert by_q.loc["FY2024Q2", "tone_delta"] == pytest.approx(by_q.loc["FY2024Q2", "mean_tone"] - prior, abs=1e-4)


def test_shipping_only_reviews_excluded_from_tone(sample_root):
    scores = pd.read_csv(Paths(sample_root, "SAMPLE").tone_scores_csv)
    qt = pd.read_csv(Paths(sample_root, "SAMPLE").quarterly_tone_csv).set_index("fiscal_quarter")
    q = "FY2025Q1"
    g = scores[scores["fiscal_quarter"] == q]
    product = g[~g["seller_shipping_only"]]
    assert qt.loc[q, "n_reviews"] == len(product)
    assert qt.loc[q, "n_seller_shipping_excluded"] == int(g["seller_shipping_only"].sum()) > 0
    assert qt.loc[q, "mean_tone"] == pytest.approx(product["tone"].mean(), rel=1e-4)
    assert "body" not in scores.columns and "title" not in scores.columns  # no review text downstream


# ------------------------------------------------------------------ revenue


def test_q4_derived_and_first_filed_value_wins():
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"start": "2024-04-01", "end": "2024-06-30", "val": 100, "form": "10-Q", "accn": "a1", "filed": "2024-07-25"},
                            {"start": "2024-04-01", "end": "2024-06-30", "val": 999, "form": "10-Q", "accn": "a9", "filed": "2025-07-25"},
                            {"start": "2024-07-01", "end": "2024-09-30", "val": 110, "form": "10-Q", "accn": "a2", "filed": "2024-10-25"},
                            {"start": "2024-04-01", "end": "2024-09-30", "val": 210, "form": "10-Q", "accn": "a2", "filed": "2024-10-25"},
                            {"start": "2024-10-01", "end": "2024-12-31", "val": 150, "form": "10-Q", "accn": "a3", "filed": "2025-01-25"},
                            {"start": "2024-04-01", "end": "2025-03-31", "val": 480, "form": "10-K", "accn": "a4", "filed": "2025-05-20"},
                        ]
                    }
                }
            }
        }
    }
    rev = join_revenue.extract_quarterly_revenue(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax"], "0331", "0001032975").set_index("fiscal_quarter")
    assert rev.loc["FY2025Q1", "revenue_usd"] == 100  # first-filed value, not the later comparative
    assert rev.loc["FY2025Q4", "revenue_usd"] == 480 - 100 - 110 - 150
    assert bool(rev.loc["FY2025Q4", "derived_q4"]) and not bool(rev.loc["FY2025Q1", "derived_q4"])
    assert "FY2025Q2" in rev.index and len(rev) == 4  # the six-month YTD entry is ignored


# ------------------------------------------------------------------ backtest


def test_backtest_eight_quarters_rule_first_and_misses_listed(sample_root):
    res = json.loads(Paths(sample_root, "SAMPLE").backtest_json.read_text())
    assert res["evaluated"] == 8 and not res["insufficient"]
    assert res["misses"] >= 1 and res["calls"] == res["hits"] + res["misses"]
    assert res["rule_check"]["matches"]
    report = Paths(sample_root, "SAMPLE").backtest_md.read_text()
    assert report.index("Pre-registered rule") < report.index("## 3. Results") < report.index("## 4. Misses")
    for r in res["rows"]:
        if r["outcome"] == "MISS":
            assert f"| {r['fiscal_quarter']} | {r['call']} |" in report


def test_backtest_refuses_changed_rule(sample_root, tmp_path):
    root = tmp_path / "changed"
    shutil.copytree(sample_root, root)
    cfg_path = root / "models" / "tone_model_config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["preregistered_rule"]["down_threshold"] = -0.05
    cfg_path.write_text(json.dumps(cfg))
    with pytest.raises(backtest.RuleChanged):
        backtest.run(root, "SAMPLE", as_of=synthetic.AS_OF)
    res = backtest.run(root, "SAMPLE", acknowledge=True, as_of=synthetic.AS_OF)
    assert res["rule_check"]["acknowledged_change"]
    assert "does NOT match" in (root / "companies" / "sample" / "reports" / "backtest_report.md").read_text()


def test_shipped_rule_hash_matches():
    cfg = load_model_config(DEFAULT_ROOT)
    assert cfg["frozen_sha256"] == frozen_hash(cfg)


# ------------------------------------------------------------------ factors and outputs


def test_factor_panel_statuses(sample_root):
    fac = pd.read_csv(Paths(sample_root, "SAMPLE").factors_csv)
    latest = factors.latest(fac).set_index("factor")
    assert latest.loc["product_tone", "status"] == "ok" and pd.notna(latest.loc["product_tone", "zscore"])
    assert latest.loc["reddit_tone", "status"] == "planned"
    assert set(fac["factor"]) >= {"tone_delta", "review_velocity", "defect_rate", "revenue_yoy", "revenue_accel"}


def test_empty_company_produces_every_deliverable(tmp_path):
    root = tmp_path / "empty"
    shutil.copytree(DEFAULT_ROOT / "config", root / "config")
    shutil.copytree(DEFAULT_ROOT / "models", root / "models")
    for f in (root / "config" / "companies").glob("*.json"):
        if f.stem != "logi":
            f.unlink()
    weekly_drop.main(["--root", str(root), "--as-of", "2026-10-15", "--no-dashboard"])
    p = Paths(root, "LOGI")
    for f in (p.tone_scores_csv, p.quarterly_tone_csv, p.revenue_join_csv, p.factors_csv, p.backtest_md, p.sources_md):
        assert f.exists(), f
    for name in ("quarterly_tone.png", "tone_vs_revenue.png", "negative_swings.png"):
        assert (p.charts / name).exists()
    assert "INSUFFICIENT HISTORY: 0 of 8" in p.backtest_md.read_text()
    drop = p.drops / "2026-W42"
    manifest = json.loads((drop / "manifest.json").read_text())
    assert manifest["history"]["insufficient"] and manifest["raw_review_text_included"] is False
    assert (drop / "limitations.md").exists() and (drop / "sources.md").exists()


def test_dashboard_builds_with_sample_and_no_text(tmp_path):
    out = build_dashboard.build(DEFAULT_ROOT, as_of="2026-10-15", out=tmp_path / "dashboard.html")
    html = out.read_text()
    assert "__DASHBOARD_DATA__" not in html and '"synthetic":true' in html
    assert "Stopped working after two weeks" not in html  # synthetic review text never reaches the page


def test_synthetic_writer_refuses_real_root():
    with pytest.raises(PermissionError):
        synthetic.write_sample(DEFAULT_ROOT)


def test_real_workspaces_hold_no_synthetic_rows():
    for csv in (DEFAULT_ROOT / "companies").glob("*/data/**/*.csv"):
        text = csv.read_text()
        assert "SYN-" not in text and "[SYNTHETIC]" not in text, csv
