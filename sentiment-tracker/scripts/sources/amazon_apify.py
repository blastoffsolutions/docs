"""Amazon US products and reviews through Apify actors (a paid, third-party data service).

Why Apify and not direct scraping: Amazon's robots.txt and Conditions of Use do not permit
automated collection of review pages, and Amazon's official APIs do not return review text.
Apify actors are run under Apify's own terms; read the actor's terms before use.

Ground rules enforced here:
  * no login cookies or session tokens are ever passed (that would bypass Amazon's login wall);
  * reviewer names/avatars are not requested (includeGdprSensitive stays false);
  * if an actor caps results, the cap is recorded per product so quarters it truncates are flagged.

Actor input/output field names differ between actors and change over time, so input templates
live in config/sources.json and normalisation below accepts several common field spellings.
Confirm them on the actor's Input and Output tabs before a first real run.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from common import utc_now_iso
from sources.base import ReviewRecord, Source, register

APIFY_BASE = "https://api.apify.com/v2"
FORBIDDEN_INPUT_KEYS = {"cookies", "cookie", "sessionCookies", "loginCookies", "amazonCookies"}


def _first(item: dict, *keys, default=None):
    for k in keys:
        cur = item
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, "", []):
            return cur
    return default


def _fill(template, values: dict):
    """Recursively substitute "{placeholder}" strings in an actor input template."""
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    if isinstance(template, str) and template.startswith("{") and template.endswith("}"):
        return values.get(template[1:-1], template)
    return template


def check_input_is_clean(actor_input: dict) -> None:
    bad = FORBIDDEN_INPUT_KEYS & set(_walk_keys(actor_input))
    if bad:
        raise PermissionError(
            f"actor input contains {sorted(bad)}; passing login cookies would bypass Amazon's access "
            "controls, which this pipeline does not do. Remove them and accept the logged-out limits."
        )


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def normalize_product(item: dict, company: str, discovered_via: str, seen: str) -> dict | None:
    asin = _first(item, "asin", "productAsin", "ASIN")
    if not asin:
        return None
    parent = _first(item, "parentAsin", "parent_asin", "variantParentAsin", default=asin)
    attrs = _first(item, "variantAttributes", "variantDetails", "selectedVariant", default="")
    if isinstance(attrs, (dict, list)):
        attrs = json.dumps(attrs, sort_keys=True)
    url = _first(item, "url", "productUrl", default=f"https://www.amazon.com/dp/{asin}")
    category = _first(item, "breadCrumbs", "category", "categories", default="")
    if isinstance(category, list):
        category = " > ".join(map(str, category))
    return {
        "source": "amazon_apify",
        "company": company,
        "parent_asin": parent,
        "asin": asin,
        "variant_attrs": attrs,
        "title": _first(item, "title", "name", default=""),
        "brand": _first(item, "brand", "manufacturer", default=""),
        "category": category,
        "url": url,
        "discovered_via": discovered_via,
        "first_seen": seen,
        "last_seen": seen,
    }


def normalize_review(item: dict, company: str, parent_asin: str, fetched_at: str) -> ReviewRecord | None:
    rid = _first(item, "reviewId", "id", "review_id")
    date = _first(item, "date", "reviewDate", "reviewedAt", "date_iso")
    if not rid or not date:
        return None
    # Amazon US pages also show "Reviewed in <other country>" reviews; keep US only.
    reviewed_in = f"{_first(item, 'reviewedIn', default='')} {date}"
    if "Reviewed in" in reviewed_in and "United States" not in reviewed_in:
        return None
    country = _first(item, "country", "reviewCountry")
    if country and str(country) not in ("US", "USA", "United States"):
        return None
    parsed = pd.to_datetime(str(date).split(" on ")[-1], errors="coerce")
    if pd.isna(parsed):
        return None
    rating = _first(item, "ratingScore", "rating", "stars")
    try:
        rating = float(str(rating).split()[0]) if rating is not None else None
    except ValueError:
        rating = None
    asin = _first(item, "productAsin", "asin", default=parent_asin)
    return ReviewRecord(
        source="amazon_apify",
        company=company,
        review_id=str(rid),
        product_id=str(asin),
        parent_id=str(parent_asin),
        review_date=parsed.date().isoformat(),
        rating=rating,
        title=str(_first(item, "reviewTitle", "title", default="")),
        body=str(_first(item, "reviewDescription", "text", "body", "reviewText", default="")),
        verified=bool(_first(item, "isVerified", "verified", "verifiedPurchase", default=False)),
        variant=str(_first(item, "variant", "variantAttributes", default="")),
        locale="en_US",
        source_url=str(_first(item, "reviewUrl", "url", default=f"https://www.amazon.com/dp/{asin}")),
        fetched_at=fetched_at,
    )


@register
class AmazonApify(Source):
    name = "amazon_apify"
    description = "Amazon US product catalog and customer reviews, collected through Apify actors."

    def _token(self) -> str:
        token = os.environ.get("APIFY_TOKEN", "").strip()
        if not token:
            raise SystemExit("APIFY_TOKEN is not set; cannot run Apify actors. See README > Setup.")
        return token

    def run_actor(self, actor_id: str, actor_input: dict, log) -> list[dict]:
        import requests

        check_input_is_clean(actor_input)
        url = f"{APIFY_BASE}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items"
        try:
            resp = requests.post(
                url,
                json=actor_input,
                headers={"Authorization": f"Bearer {self._token()}"},
                params={"format": "json", "clean": "true"},
                timeout=self.settings.get("timeout_s", 900),
            )
        except requests.RequestException as e:
            log("api.apify.com", "error", f"{actor_id}: {type(e).__name__}: {e}")
            raise
        log("api.apify.com", str(resp.status_code), f"{actor_id}: {len(resp.content)} bytes")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def fetch_products(self, company: dict, log) -> list[dict]:
        amz = company.get("amazon", {})
        actor = self.settings["products_actor"]
        values = {
            "brand_query": amz.get("brand_query", company["name"]),
            "storefront_url": amz.get("storefront_url", ""),
            "max_items": self.settings.get("max_products", 200),
        }
        actor_input = _fill(self.settings["products_input"], values)
        seen = utc_now_iso()[:10]
        items = self.run_actor(actor, actor_input, log)
        brands = {b.lower() for b in amz.get("brand_names", [])}
        out = []
        for it in items:
            row = normalize_product(it, company["ticker"], f"apify:{actor}", seen)
            if not row:
                continue
            if brands and str(row["brand"]).lower() not in brands:
                continue  # third-party listings and look-alikes are excluded and disclosed as a gap
            out.append(row)
        return out

    def fetch_reviews(self, company: dict, products: list[dict], log):
        actor = self.settings["reviews_actor"]
        cap = int(self.settings.get("max_reviews_per_product", 1000))
        fetched_at = utc_now_iso()
        records: list[ReviewRecord] = []
        meta: dict[str, dict] = {}
        parents = sorted({p["parent_asin"] for p in products})
        for parent in parents:
            values = {"product_url": f"https://www.amazon.com/dp/{parent}", "max_reviews": cap, "asin": parent}
            actor_input = _fill(self.settings["reviews_input"], values)
            items = self.run_actor(actor, actor_input, log)
            recs = [r for r in (normalize_review(i, company["ticker"], parent, fetched_at) for i in items) if r]
            dates = sorted(r.review_date for r in recs)
            meta[parent] = {
                "returned": len(items),
                "kept_us": len(recs),
                "earliest": dates[0] if dates else None,
                "latest": dates[-1] if dates else None,
                "cap": cap,
                "cap_hit": len(items) >= cap,
            }
            records.extend(recs)
        return records, meta
