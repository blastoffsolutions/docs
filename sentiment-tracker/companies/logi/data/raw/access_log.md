# Access log

Every request to an outside data source is recorded here: what was asked for, what came back,
and any limit hit (robots.txt, rate limits, result caps, login walls, network policy).
Nothing in this pipeline works around an access control; a limit is logged, not bypassed.

| UTC time | Source | Host | Status | Note |
|---|---|---|---|---|
| 2026-10-15T17:38:12+00:00 | sec_submissions | data.sec.gov | error | ProxyError: HTTPSConnectionPool(host='data.sec.gov', port=443): Max retries exceeded with url: /submissions/CIK0001032975.json (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden'))) |

## Build-environment note (2026-10-15)

The `ProxyError ... 403 Forbidden` row of 2026-10-15 came from the cloud environment this
pipeline was built in. Its outbound network policy refused data.sec.gov, www.sec.gov,
api.apify.com and www.amazon.com, and no APIFY_TOKEN was configured. No request reached SEC,
Apify or Amazon, so this is not an access limit of those services. The first real collection
run will add its own rows above.

## Collection status

As of 2026-10-15. Nothing collected yet: no products and no reviews on file. Run weekly_drop.py --refresh with APIFY_TOKEN and SEC_USER_AGENT set.

INSUFFICIENT HISTORY: 0 of 12 usable quarters. Quarters that are missing or unreliable are flagged, never imputed.
