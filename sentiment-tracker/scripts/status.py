"""History status and the limitations text shared by the weekly drop and the dashboard."""
from __future__ import annotations

from aggregate_quarters import composite, load_quarterly
from common import Paths

REQUIRED_QUARTERS = 12

LIMITATIONS = """\
# Limitations

Research only. Not financial advice and not a trading system.

- **Review history is capped.** Amazon shows logged-out visitors a limited, sorted slice of
  reviews, and this pipeline does not log in. Older quarters are often truncated or missing;
  they are flagged, never imputed.
- **Most-recent sort bias.** Collecting "most recent" reviews over-weights the latest quarters
  until weekly runs have accumulated history.
- **Catalog survivorship.** Discontinued products drop off the storefront, so past quarters
  can miss the very products that went wrong. Add known ASINs to asin_seed.csv.
- **Product mix shifts.** A launch or a discontinuation changes which products are reviewed;
  tone can move with no change in quality.
- **Amazon US is one channel.** It is a slice of global revenue whose size most companies
  do not disclose. Wholesale, other retailers and other countries are not observed.
- **Fake and incentivised reviews** exist and are not filtered beyond Amazon's own policing.
- **The tone model is a lexicon (VADER) plus stars.** It misses sarcasm and domain language;
  its parameters are frozen so results stay comparable, not because they are optimal.
- **Small samples.** Eight held-out quarters cannot establish that the signal works. Treat
  hits and misses as anecdotes until many companies and years agree.
- **Revenue timing.** Fiscal Q4 is derived (annual minus Q1-Q3). 52/53-week calendars are
  mapped to month-end quarters.
"""


def history_status(paths: Paths) -> dict:
    comp = composite(load_quarterly(paths))
    usable = int(comp["history_status"].isin(["ok", "no_baseline"]).sum()) if len(comp) else 0
    return {
        "quarters_on_file": int(len(comp)),
        "usable_quarters": usable,
        "required_quarters": REQUIRED_QUARTERS,
        "insufficient": usable < REQUIRED_QUARTERS,
        "label": (
            f"INSUFFICIENT HISTORY: {usable} of {REQUIRED_QUARTERS} usable quarters"
            if usable < REQUIRED_QUARTERS
            else f"{usable} usable quarters (requirement of {REQUIRED_QUARTERS} met)"
        ),
    }
