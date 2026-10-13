"""Helpers shared by the record-cache connectors (TED, Forskningsrådet, Smilefjes, DiBK).

Those sources are bulk files or POST-only APIs, so a run does not re-fetch one URL per company.
Each matched record is kept as one compact JSON line, and that exact line becomes the evidence
snapshot. The claim span is cut from the line itself, so it is always a literal substring of the
stored snapshot, and `content_sha256` is the sha256 of exactly what is stored.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..http import Fetcher
from ..model import Snapshot, utc_now

BRREG_HUMAN = "https://virksomhet.brreg.no/oppslag/underenheter/"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_SPAN = 400


def line(obj: dict) -> str:
    """Canonical record line: compact, key order as given (important keys first)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def extract_note(retrieved_at: str, page_url: str, detail: str | None = None, *, kind: str = "cached export") -> str:
    """Evidence note for a row cut from a bulk file or API response: `source_url` is that file or endpoint,
    the human record page is named here."""
    note = f"extract from the {kind} of {(retrieved_at or '')[:10]}; human page: {page_url}"
    return note + (f" ({detail})" if detail else "")


def record_snapshot(url: str, record_line: str, retrieved_at: str) -> Snapshot:
    return Snapshot(url=url, final_url=url, status=200, retrieved_at=retrieved_at,
                    content=record_line.encode("utf-8"), content_type="application/json")


def head_span(record_line: str, limit: int = MAX_SPAN) -> str:
    """The longest prefix of the record line, up to `limit`, that ends on a pair boundary."""
    if len(record_line) <= limit:
        return record_line
    cut = record_line[:limit]
    i = cut.rfind(',"')
    return cut[:i] if i > 0 else cut


def span_with(record_line: str, *, start_key: str | None = None, must: str | None = None,
              limit: int = MAX_SPAN) -> str:
    """An exact substring of the line, starting at `start_key` (or the line start), that contains
    `must` when possible: falls back to a window centred on `must`."""
    span = head_span(record_line, limit)
    if must is None or must in span:
        return span
    start = record_line.find(f'"{start_key}":') if start_key else -1
    if start > 0:
        span = head_span(record_line[start:], limit)
        if must in span:
            return span
    i = record_line.find(must)
    if i < 0:
        return span
    lo = max(0, i - limit // 3)
    return record_line[lo:lo + limit]


def pair_span(record_line: str, key: str) -> str | None:
    """Exact `"key":value` substring of a compact record line, if present."""
    obj = json.loads(record_line)
    if key not in obj:
        return None
    s = f'"{key}":' + json.dumps(obj[key], ensure_ascii=False, separators=(",", ":"))
    return s if s in record_line else None


def load_cache(name: str, path: str | Path | None = None) -> dict | None:
    p = Path(path) if path else DATA_DIR / f"{name}.json.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def excel_date(v: Any) -> str | None:
    """Excel serial day number (1900 system) or ISO-ish string -> YYYY-MM-DD."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        n = float(s)
    except ValueError:
        return s[:10] if len(s) >= 10 and s[4] == "-" else None
    if 20000 < n < 80000:
        return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
    return None


def is_orgnr(s: str) -> bool:
    """Mod-11 check used by Enhetsregisteret."""
    if len(s) != 9 or not s.isdigit():
        return False
    w = (3, 2, 7, 6, 5, 4, 3, 2)
    r = 11 - sum(int(a) * b for a, b in zip(s[:8], w)) % 11
    r = 0 if r == 11 else r
    return r != 10 and r == int(s[8])


async def raw_request(f: Fetcher, method: str, url: str, *, json_body: Any = None, timeout: float = 60.0,
                      max_bytes: int = 60_000_000) -> tuple[int, bytes, str]:
    """A budgeted request outside `Fetcher.get` (POST bodies, or files above its 3 MB cap).

    Counts against the run's request budget like any other request. Returns
    (status, body, retrieved_at)."""
    host = urlsplit(url).netloc.lower()
    f.budget.take(host)
    retrieved = utc_now()
    async with f.total:
        r = await f.client.request(method, url, json=json_body, timeout=timeout)
    return r.status_code, r.content[:max_bytes], retrieved
