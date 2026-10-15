"""Shared helpers: paths, config, fiscal calendars, access logging, polite HTTP.

Research only. Nothing in this package places or recommends trades.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
FROZEN_KEYS = ("tone_model", "tags", "aggregation", "preregistered_rule")


# --------------------------------------------------------------------------- paths


@dataclass(frozen=True)
class Paths:
    """Every file location for one company workspace (the spec layout, per ticker)."""

    root: Path
    ticker: str

    @property
    def company_dir(self) -> Path:
        return self.root / "companies" / self.ticker.lower()

    @property
    def raw(self) -> Path:
        return self.company_dir / "data" / "raw"

    @property
    def processed(self) -> Path:
        return self.company_dir / "data" / "processed"

    @property
    def reports(self) -> Path:
        return self.company_dir / "reports"

    @property
    def charts(self) -> Path:
        return self.reports / "charts"

    @property
    def drops(self) -> Path:
        return self.reports / "drops"

    products_csv = property(lambda s: s.raw / "products.csv")
    asin_seed_csv = property(lambda s: s.raw / "asin_seed.csv")
    reviews_csv = property(lambda s: s.raw / "reviews.csv")
    fetch_meta_json = property(lambda s: s.raw / "fetch_meta.json")
    access_log = property(lambda s: s.raw / "access_log.md")
    sources_md = property(lambda s: s.raw / "sources.md")
    company_profile = property(lambda s: s.raw / "company_profile.json")
    companyfacts_json = property(lambda s: s.raw / "sec_companyfacts.json")
    tone_scores_csv = property(lambda s: s.processed / "tone_scores.csv")
    quarterly_tone_csv = property(lambda s: s.processed / "quarterly_tone.csv")
    revenue_quarterly_csv = property(lambda s: s.processed / "revenue_quarterly.csv")
    revenue_join_csv = property(lambda s: s.processed / "revenue_join.csv")
    factors_csv = property(lambda s: s.processed / "factors.csv")
    backtest_md = property(lambda s: s.reports / "backtest_report.md")
    backtest_json = property(lambda s: s.reports / "backtest_results.json")

    def ensure(self) -> "Paths":
        for d in (self.raw, self.processed, self.charts):
            d.mkdir(parents=True, exist_ok=True)
        return self


def model_config_path(root: Path) -> Path:
    return root / "models" / "tone_model_config.json"


def company_config_path(root: Path, ticker: str) -> Path:
    return root / "config" / "companies" / f"{ticker.lower()}.json"


def list_companies(root: Path) -> list[str]:
    folder = root / "config" / "companies"
    return sorted(p.stem.upper() for p in folder.glob("*.json"))


def load_company(root: Path, ticker: str) -> dict:
    with open(company_config_path(root, ticker)) as f:
        return json.load(f)


def load_sources_config(root: Path) -> dict:
    path = root / "config" / "sources.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_model_config(root: Path) -> dict:
    with open(model_config_path(root)) as f:
        return json.load(f)


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="tracker root (default: this repo folder)")
    p.add_argument("--ticker", action="append", help="ticker to process; repeatable (default: every configured company)")
    return p


def tickers_from_args(args) -> list[str]:
    return [t.upper() for t in args.ticker] if args.ticker else list_companies(args.root)


# ------------------------------------------------------------------ fiscal calendar


def _fye_month(fye_mmdd: str) -> int:
    month = int(str(fye_mmdd)[:2])
    if not 1 <= month <= 12:
        raise ValueError(f"bad fiscal_year_end {fye_mmdd!r}; expected MMDD")
    return month


def fiscal_quarter(date, fye_mmdd: str) -> str:
    """Map a date to a fiscal-quarter label such as 'FY2026Q1'.

    Fiscal years are named for the calendar year they end in (the SEC convention).
    52/53-week calendars are treated as ending on the last day of the FYE month,
    which is how most filers present their quarters.
    """
    d = pd.Timestamp(date)
    fye = _fye_month(fye_mmdd)
    start_month = fye % 12 + 1
    fy = d.year if d.month <= fye else d.year + 1
    q = (d.month - start_month) % 12 // 3 + 1
    return f"FY{fy}Q{q}"


def parse_quarter(label: str) -> tuple[int, int]:
    fy, q = label.upper().removeprefix("FY").split("Q")
    return int(fy), int(q)


def quarter_ordinal(label: str) -> int:
    fy, q = parse_quarter(label)
    return fy * 4 + (q - 1)


def quarter_from_ordinal(n: int) -> str:
    return f"FY{n // 4}Q{n % 4 + 1}"


def shift_quarter(label: str, k: int) -> str:
    return quarter_from_ordinal(quarter_ordinal(label) + k)


def quarter_bounds(label: str, fye_mmdd: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    fy, q = parse_quarter(label)
    fye = _fye_month(fye_mmdd)
    start_month = fye % 12 + 1
    start_year = fy if start_month == 1 else fy - 1
    first = pd.Timestamp(year=start_year, month=start_month, day=1) + pd.DateOffset(months=3 * (q - 1))
    last = first + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    return first, last


def quarter_range(first: str, last: str) -> list[str]:
    return [quarter_from_ordinal(n) for n in range(quarter_ordinal(first), quarter_ordinal(last) + 1)]


# ---------------------------------------------------------------------- utilities


def today() -> dt.date:
    return dt.date.today()


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    """Read a CSV if present, otherwise an empty frame with the expected columns."""
    if path.exists() and path.stat().st_size > 0:
        df = pd.read_csv(path)
        for c in columns:
            if c not in df.columns:
                df[c] = pd.NA
        return df
    return pd.DataFrame(columns=columns)


def write_csv(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.reindex(columns=columns) if len(df) else pd.DataFrame(columns=columns)
    out.to_csv(path, index=False, float_format="%.6g")


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=_json_default)
        f.write("\n")


def _json_default(o):
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o)}")


def clean_number(x):
    """NaN/None -> None so JSON stays valid; numpy scalars -> Python."""
    if x is None or x is pd.NA:
        return None
    try:
        if isinstance(x, float) and math.isnan(x):
            return None
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return x.item() if hasattr(x, "item") else x


def to_jsonable(obj):
    """Recursively replace NaN/NA with None and numpy scalars with Python ones (valid JSON for JS)."""
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, dt.date, dt.datetime)):
        return obj.isoformat()
    return clean_number(obj)


def frozen_hash(model_config: dict) -> str:
    """SHA-256 over everything that defines the signal and the pre-registered rule."""
    frozen = {k: model_config[k] for k in FROZEN_KEYS}
    canonical = json.dumps(frozen, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# -------------------------------------------------------------------- access log

ACCESS_LOG_HEADER = """# Access log

Every request to an outside data source is recorded here: what was asked for, what came back,
and any limit hit (robots.txt, rate limits, result caps, login walls, network policy).
Nothing in this pipeline works around an access control; a limit is logged, not bypassed.

| UTC time | Source | Host | Status | Note |
|---|---|---|---|---|
"""


def log_access(paths: Paths, source: str, host: str, status: str, note: str) -> None:
    paths.raw.mkdir(parents=True, exist_ok=True)
    new = not paths.access_log.exists()
    with open(paths.access_log, "a") as f:
        if new:
            f.write(ACCESS_LOG_HEADER)
        note = note.replace("|", "/").replace("\n", " ")
        f.write(f"| {utc_now_iso()} | {source} | {host} | {status} | {note} |\n")


def write_section(paths: Paths, title: str, body: str) -> None:
    """Replace (or append) a '## title' section at the end of the access log."""
    paths.raw.mkdir(parents=True, exist_ok=True)
    text = paths.access_log.read_text() if paths.access_log.exists() else ACCESS_LOG_HEADER
    marker = f"\n## {title}\n"
    if marker in text:
        head, _, tail = text.partition(marker)
        nxt = tail.find("\n## ")
        tail = tail[nxt:] if nxt >= 0 else ""
        text = head + tail
    text = text.rstrip("\n") + "\n" + marker + "\n" + body.strip() + "\n"
    paths.access_log.write_text(text)


# ----------------------------------------------------------------- polite HTTP


class AccessDenied(RuntimeError):
    pass


class PoliteSession:
    """requests wrapper: identifies itself, honours robots.txt for web pages,
    spaces requests out, and never retries past an explicit refusal."""

    def __init__(self, user_agent: str, min_interval_s: float = 1.0, check_robots: bool = True):
        import requests

        self._requests = requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = user_agent
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.check_robots = check_robots
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        if not self.check_robots:
            return True
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self.s.get(base + "/robots.txt", timeout=20)
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except self._requests.RequestException:
                rp.parse([])  # unreachable robots.txt: treat as "no rules", the request itself will still fail loudly
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.user_agent, url)

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last = time.monotonic()

    def get(self, url: str, **kw):
        if not self.allowed(url):
            raise AccessDenied(f"robots.txt disallows {url}")
        self._wait()
        return self.s.get(url, timeout=kw.pop("timeout", 60), **kw)

    def post(self, url: str, **kw):
        self._wait()
        return self.s.post(url, timeout=kw.pop("timeout", 300), **kw)


def sec_session() -> PoliteSession:
    """SEC fair-access policy: declare who you are, stay under 10 requests/second."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise SystemExit(
            "SEC_USER_AGENT is not set. SEC EDGAR requires a descriptive User-Agent with contact "
            "details, e.g. export SEC_USER_AGENT='Your Name research you@example.com'"
        )
    # data.sec.gov is an API (robots.txt does not govern it); keep a conservative pace anyway.
    return PoliteSession(ua, min_interval_s=0.2, check_robots=False)
