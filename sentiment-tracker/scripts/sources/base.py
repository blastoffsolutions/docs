"""Source adapter contract.

A source turns some public channel (Amazon reviews, Reddit posts, app-store reviews...)
into ReviewRecord rows. Everything downstream (scoring, quarterly aggregation, factors,
backtest) works on these rows and groups by the `source` column, so adding a channel
means writing one adapter and listing it in a company's config `sources`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

REVIEW_COLUMNS = [
    "source",
    "company",
    "review_id",
    "product_id",
    "parent_id",
    "review_date",
    "rating",
    "title",
    "body",
    "verified",
    "variant",
    "locale",
    "source_url",
    "fetched_at",
]

PRODUCT_COLUMNS = [
    "source",
    "company",
    "parent_asin",
    "asin",
    "variant_attrs",
    "title",
    "brand",
    "category",
    "url",
    "discovered_via",
    "first_seen",
    "last_seen",
]


@dataclass
class ReviewRecord:
    source: str
    company: str
    review_id: str
    product_id: str
    parent_id: str
    review_date: str  # ISO date
    rating: float | None
    title: str
    body: str
    verified: bool | None
    variant: str
    locale: str
    source_url: str
    fetched_at: str

    def as_row(self) -> dict:
        return asdict(self)


assert [f.name for f in fields(ReviewRecord)] == REVIEW_COLUMNS


class Source:
    """Base adapter. Subclasses set `name` and implement the fetch methods."""

    name = "base"
    description = ""

    def __init__(self, settings: dict):
        self.settings = settings or {}

    def fetch_products(self, company: dict, log) -> list[dict]:
        raise NotImplementedError

    def fetch_reviews(self, company: dict, products: list[dict], log) -> tuple[list[ReviewRecord], dict]:
        """Return (records, per-product fetch metadata: count, earliest, cap_hit)."""
        raise NotImplementedError


REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    REGISTRY[cls.name] = cls
    return cls


def get_source(name: str, settings: dict) -> Source:
    if name not in REGISTRY:
        raise KeyError(f"unknown source {name!r}; implemented: {sorted(REGISTRY)}; planned: {sorted(PLANNED)}")
    return REGISTRY[name](settings)


# Channels designed into the schema but not implemented yet. The dashboard lists them.
PLANNED = {
    "reddit": "Brand and product subreddits via the official Reddit Data API (OAuth). Early defect chatter and enthusiast sentiment.",
    "app_store": "Apple App Store / Google Play reviews of the company's apps. Matters most for DTC brands and retailers.",
    "google_trends": "Search interest for brand and flagship product names. A demand-side signal, not tone.",
    "trustpilot": "Service and delivery experience for direct-to-consumer storefronts.",
}
