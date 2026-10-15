"""Step 4: score each review's tone on a -2..+2 scale and tag the reasons.

The model is deterministic (VADER + star rating, parameters in models/tone_model_config.json)
so `weekly_drop.py` reproduces identical scores from the same raw data. tone_scores.csv keeps
IDs, scores and tags only, never review text.
"""
from __future__ import annotations

import math
import re

import pandas as pd

from common import Paths, base_parser, fiscal_quarter, load_company, load_model_config, read_csv, tickers_from_args, write_csv
from sources.base import REVIEW_COLUMNS

TAGS = ["defect", "quality_decline", "price", "seller_shipping"]
SCORE_COLUMNS = [
    "source",
    "review_id",
    "product_id",
    "parent_id",
    "review_date",
    "fiscal_quarter",
    "rating",
    "vader_compound",
    "text_tone",
    "star_tone",
    "tone_raw",
    "tone",
    "tag_defect",
    "tag_quality_decline",
    "tag_price",
    "tag_seller_shipping",
    "tag_other",
    "seller_shipping_only",
    "model_version",
]


def compile_patterns(terms: list[str]) -> re.Pattern:
    # Lookarounds instead of \b so terms that start with a symbol (e.g. "\$\d+") still match.
    return re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")(?!\w)", re.IGNORECASE)


def text_tone(compound: float, cutpoints: list[float]) -> int:
    a, b, c, d = cutpoints
    if compound <= a:
        return -2
    if compound <= b:
        return -1
    if compound < c:
        return 0
    if compound < d:
        return 1
    return 2


def round_half_away(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


class ToneModel:
    def __init__(self, model_config: dict):
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self.cfg = model_config
        self.vader = SentimentIntensityAnalyzer()
        tm = model_config["tone_model"]
        self.cutpoints = tm["text_cutpoints"]
        self.w_text = tm["weights"]["text"]
        self.w_star = tm["weights"]["star"]
        tags = model_config["tags"]
        self.patterns = {t: compile_patterns(tags[t]) for t in TAGS}
        self.product_terms = compile_patterns(tags["product_terms"])
        self.version = model_config["model_version"]

    def score(self, title: str, body: str, rating) -> dict:
        text = f"{title or ''}. {body or ''}".strip(". ")
        compound = self.vader.polarity_scores(text)["compound"] if text else 0.0
        tt = text_tone(compound, self.cutpoints)
        has_rating = rating is not None and not (isinstance(rating, float) and math.isnan(rating))
        st = int(round(float(rating))) - 3 if has_rating else None
        raw = self.w_text * tt + self.w_star * st if st is not None else float(tt)
        tone = max(-2, min(2, round_half_away(raw)))
        hits = {t: bool(self.patterns[t].search(text)) for t in TAGS}
        product_signal = hits["defect"] or hits["quality_decline"] or hits["price"] or bool(self.product_terms.search(text))
        return {
            "vader_compound": round(compound, 4),
            "text_tone": tt,
            "star_tone": st,
            "tone_raw": raw,
            "tone": tone,
            **{f"tag_{t}": hits[t] for t in TAGS},
            "tag_other": tone <= -1 and not any(hits.values()),
            "seller_shipping_only": hits["seller_shipping"] and not product_signal,
            "model_version": self.version,
        }


def score_frame(reviews: pd.DataFrame, model: ToneModel, fye: str) -> pd.DataFrame:
    if reviews.empty:
        return pd.DataFrame(columns=SCORE_COLUMNS)
    rows = []
    for r in reviews.itertuples(index=False):
        rating = None if pd.isna(r.rating) else float(r.rating)
        s = model.score(r.title if isinstance(r.title, str) else "", r.body if isinstance(r.body, str) else "", rating)
        rows.append(
            {
                "source": r.source,
                "review_id": r.review_id,
                "product_id": r.product_id,
                "parent_id": r.parent_id,
                "review_date": r.review_date,
                "fiscal_quarter": fiscal_quarter(r.review_date, fye),
                "rating": rating,
                **s,
            }
        )
    return pd.DataFrame(rows, columns=SCORE_COLUMNS)


def run(root, ticker: str) -> pd.DataFrame:
    paths = Paths(root, ticker).ensure()
    company = load_company(root, ticker)
    reviews = read_csv(paths.reviews_csv, REVIEW_COLUMNS)
    scores = score_frame(reviews, ToneModel(load_model_config(root)), company["fiscal_year_end"])
    write_csv(scores, paths.tone_scores_csv, SCORE_COLUMNS)
    return scores


def main(argv=None):
    args = base_parser(__doc__).parse_args(argv)
    for t in tickers_from_args(args):
        df = run(args.root, t)
        print(f"{t}: scored {len(df)} reviews")


if __name__ == "__main__":
    main()
