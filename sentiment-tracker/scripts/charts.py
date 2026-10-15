"""The three report charts, per company: quarterly tone, tone vs. revenue, negative swings.

One y-scale per panel (two measures get two stacked panels, never a dual axis). When a
company has no usable data the chart is still written, as an explicit placeholder.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aggregate_quarters import composite, load_quarterly  # noqa: E402
from common import Paths, base_parser, load_company, load_model_config, read_csv, tickers_from_args  # noqa: E402
from join_revenue import JOIN_COLUMNS  # noqa: E402

# Blastoffmax palette; blue/red/yellow validated for colour-vision deficiency on this surface.
INK = "#122232"
INK_2 = "#4a6379"
MUTED = "#688197"
GRID = "#ebf0f4"
AXIS = "#b8c7d3"
SURFACE = "#ffffff"
BLUE = "#008cc7"
BLUE_LIGHT = "#66badd"
RED = "#d2403c"
YELLOW = "#eda100"
NEUTRAL = "#b8c7d3"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
        "font.size": 9,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    }
)


def _finish(fig, path, note: str | None, synthetic: bool, bottom_ax=None):
    if note:
        import textwrap

        wrapped = "\n".join(textwrap.wrap(note, 120))
        if bottom_ax is not None:  # below the rotated tick labels, never on top of them
            bottom_ax.annotate(wrapped, xy=(0, 0), xycoords="axes fraction", xytext=(-40, -62), textcoords="offset points", color=MUTED, fontsize=7.5, ha="left", va="top")
        else:
            fig.text(0.01, 0.01, wrapped, color=MUTED, fontsize=7.5, ha="left", va="bottom")
    if synthetic:
        fig.text(0.99, 0.99, "SYNTHETIC SAMPLE DATA: not a real company", color=RED, fontsize=8, ha="right", va="top", weight="bold")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def placeholder(path, title: str, reason: str, synthetic: bool = False):
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.text(0.5, 0.55, "Insufficient data: not yet collected", ha="center", va="center", color=INK_2, fontsize=12, weight="semibold", transform=ax.transAxes)
    ax.text(0.5, 0.38, reason, ha="center", va="center", color=MUTED, fontsize=9, transform=ax.transAxes, wrap=True)
    _finish(fig, path, None, synthetic)


def _x(labels):
    return np.arange(len(labels)), [q.replace("FY20", "FY") for q in labels]


def quarterly_tone(comp: pd.DataFrame, path, company: dict, min_reviews: int, synthetic=False):
    title = f"{company['ticker']}: quarterly review tone vs. prior-4-quarter baseline"
    if comp.empty or comp["composite_mean_tone"].notna().sum() == 0:
        return placeholder(path, title, "No reviews on file. Run weekly_drop.py --refresh with APIFY_TOKEN set.", synthetic)
    x, labels = _x(list(comp["fiscal_quarter"]))
    ok = (comp["history_status"].isin(["ok", "no_baseline"])).to_numpy()
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.12})
    tone = comp["composite_mean_tone"].astype(float).to_numpy()
    base = tone - comp["composite_tone_delta"].astype(float).to_numpy()  # blank where no usable baseline
    ax.plot(x, base, color=MUTED, lw=2, solid_capstyle="round", label="Prior-4-quarter baseline")
    ax.plot(x, tone, color=BLUE, lw=2, solid_capstyle="round", label="Mean tone")
    ax.scatter(x[ok], tone[ok], s=36, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.scatter(x[~ok], tone[~ok], s=36, facecolor=SURFACE, edgecolor=BLUE, linewidth=1.5, zorder=3, label="Sparse, truncated or partial")
    ax.set_ylabel("Tone (-2 to +2)")
    ax.set_title(title, pad=26)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8, ncol=3, borderaxespad=0.2)
    ax2.bar(x, comp["n_reviews"], width=0.6, color=BLUE_LIGHT)
    ax2.axhline(min_reviews, color=MUTED, lw=1)
    ax2.text(x[0] - 0.45, min_reviews, f" sparse below {min_reviews}", color=MUTED, fontsize=7.5, va="bottom", ha="left")
    ax2.set_ylabel("Reviews")
    ax2.set_xticks(x, labels, rotation=45, ha="right")
    _finish(fig, path, "Hollow markers: quarter is sparse, truncated by a source cap, or not finished. Baseline shown only where all four prior quarters are usable.", synthetic, ax2)


def tone_vs_revenue(joined: pd.DataFrame, path, company: dict, rule: dict, synthetic=False):
    title = f"{company['ticker']}: tone change vs. next quarter's change in revenue growth"
    if joined.empty or joined["composite_tone_delta"].notna().sum() == 0:
        reason = "Needs tone deltas (12 quarters of reviews) and SEC revenue." if joined.empty or joined["revenue_status"].eq("no_revenue_data").all() else "No quarter has a usable tone baseline yet."
        return placeholder(path, title, reason, synthetic)
    j = joined.copy()
    x, labels = _x(list(j["fiscal_quarter"]))
    delta = j["composite_tone_delta"].astype(float).to_numpy()
    accel = j["yoy_accel_next"].astype(float).to_numpy() * 100
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 5.4), sharex=True, gridspec_kw={"hspace": 0.18})
    ax.bar(x, np.nan_to_num(delta), width=0.6, color=[RED if d <= rule["down_threshold"] else BLUE if d >= rule["up_threshold"] else NEUTRAL for d in np.nan_to_num(delta)])
    ax.axhline(0, color=AXIS, lw=1)
    for t in (rule["down_threshold"], rule["up_threshold"]):
        ax.axhline(t, color=MUTED, lw=0.8)
    ax.set_ylabel("Tone vs. baseline")
    ax.set_title(title)
    ax2.bar(x, np.nan_to_num(accel), width=0.6, color=[RED if a < 0 else BLUE for a in np.nan_to_num(accel)])
    ax2.axhline(0, color=AXIS, lw=1)
    ax2.set_ylabel("Next-quarter growth\nchange (pts)")
    ax2.set_xticks(x, labels, rotation=45, ha="right")
    _finish(fig, path, f"Top: quarter q's tone minus its baseline; red at or below {rule['down_threshold']}, blue at or above +{rule['up_threshold']}. Bottom: revenue YoY growth in q+1 minus YoY growth in q (SEC filings). Blank: not yet available.", synthetic, ax2)


def negative_swings(comp: pd.DataFrame, path, company: dict, rule: dict, synthetic=False):
    title = f"{company['ticker']}: negative tone swings and what reviewers blamed"
    if comp.empty or comp["composite_tone_delta"].notna().sum() == 0:
        return placeholder(path, title, "Needs at least five usable quarters to compare against a baseline.", synthetic)
    x, labels = _x(list(comp["fiscal_quarter"]))
    delta = comp["composite_tone_delta"].astype(float).to_numpy()
    swing = np.nan_to_num(delta) <= rule["down_threshold"]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True, gridspec_kw={"height_ratios": [2, 1.4], "hspace": 0.15})
    ax.bar(x, np.nan_to_num(delta), width=0.6, color=[RED if s else NEUTRAL for s in swing])
    ax.axhline(0, color=AXIS, lw=1)
    ax.axhline(rule["down_threshold"], color=MUTED, lw=0.8)
    ax.text(x[0] - 0.4, rule["down_threshold"], f" DOWN threshold {rule['down_threshold']}", color=MUTED, fontsize=7.5, va="top")
    ax.set_ylabel("Tone vs. baseline")
    ax.set_title(title)
    w = 0.26
    ax2.bar(x - w, comp["defect_rate"].astype(float) * 100, width=w, color=RED, label="Defect")
    ax2.bar(x, comp["quality_decline_rate"].astype(float) * 100, width=w, color=YELLOW, label="Quality decline")
    ax2.bar(x + w, comp["price_rate"].astype(float) * 100, width=w, color=BLUE, label="Price")
    ax2.set_ylabel("% of reviews")
    ax2.legend(frameon=False, fontsize=8, ncol=3, loc="upper left")
    ax2.set_xticks(x, labels, rotation=45, ha="right")
    _finish(fig, path, "Red bars: quarters at or below the DOWN threshold. Lower panel: share of reviews tagged with each reason.", synthetic, ax2)


def run(root, ticker: str, synthetic: bool = False):
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    cfg = load_model_config(root)
    comp = composite(load_quarterly(paths))
    joined = read_csv(paths.revenue_join_csv, JOIN_COLUMNS)
    rule = cfg["preregistered_rule"]
    quarterly_tone(comp, paths.charts / "quarterly_tone.png", company, cfg["aggregation"]["sparse_min_reviews"], synthetic)
    tone_vs_revenue(joined, paths.charts / "tone_vs_revenue.png", company, rule, synthetic)
    negative_swings(comp, paths.charts / "negative_swings.png", company, rule, synthetic)
    return sorted(paths.charts.glob("*.png"))


def main(argv=None):
    args = base_parser(__doc__).parse_args(argv)
    for t in tickers_from_args(args):
        files = run(args.root, t)
        print(f"{t}: {', '.join(f.name for f in files)}")


if __name__ == "__main__":
    main()
