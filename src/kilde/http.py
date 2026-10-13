"""Budgeted, polite HTTP client.

Every outbound request goes through `Fetcher.get`, which
- counts against the run's request budget and stops before the deadline,
- checks robots.txt for company sites (registry APIs are documented open APIs),
- limits concurrency per host,
- captures the response as a `Snapshot` for evidence.

`KILDE_CACHE_DIR` turns on a disk cache for development. Cached responses are not
outbound requests and keep their original retrieval time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from protego import Protego

from . import __version__
from .model import Snapshot, utc_now

USER_AGENT = f"KildeSignalpost/{__version__} (+https://github.com/tahcin/kilde)"
ROBOTS_TOKEN = "KildeSignalpost"
MAX_BYTES = 3_000_000


class BudgetExhausted(Exception):
    pass


class Blocked(Exception):
    """robots.txt disallows the path."""


class Budget:
    def __init__(self, max_requests: int, seconds: float):
        self.max_requests = max_requests
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.used = 0
        self.dns_lookups = 0
        self.cache_hits = 0
        self.by_host: dict[str, int] = defaultdict(int)

    def remaining_requests(self) -> int:
        return self.max_requests - self.used

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def take(self, host: str, reserve: int = 0) -> None:
        if self.used + 1 > self.max_requests - reserve:
            raise BudgetExhausted("request budget exhausted")
        if self.remaining_seconds() < 5:
            raise BudgetExhausted("time budget exhausted")
        self.used += 1
        self.by_host[host] += 1


class Fetcher:
    def __init__(self, budget: Budget, *, per_host: int = 2, total: int = 48, timeout: float = 12.0,
                 cache_dir: str | None = None):
        self.budget = budget
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.7"},
            timeout=httpx.Timeout(timeout, connect=6.0),
            follow_redirects=True,
            max_redirects=6,
            limits=httpx.Limits(max_connections=total, max_keepalive_connections=total),
        )
        self.total = asyncio.Semaphore(total)
        self.per_host_limit = per_host
        # Documented open APIs with no published rate limit get slightly more parallelism.
        self.host_limits = {"data.brreg.no": 6}
        # Slow but permitted sources get a longer read timeout (NAV's feed took up to 60 s on Oct 13).
        self.host_timeouts = {"pam-stilling-feed.nav.no": 60.0}
        self.hosts: dict[str, asyncio.Semaphore] = {}
        self.robots: dict[str, Protego | None] = {}
        self.robots_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.host_min_interval: dict[str, float] = {}
        self.host_last: dict[str, float] = defaultdict(float)
        cache_dir = cache_dir or os.environ.get("KILDE_CACHE_DIR")
        self.cache = Path(cache_dir) if cache_dir else None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        await self.client.aclose()

    def set_rate(self, host: str, min_interval: float) -> None:
        self.host_min_interval[host] = min_interval

    # Cache --------------------------------------------------------------
    def _cache_path(self, url: str, headers: dict | None) -> Path | None:
        if not self.cache:
            return None
        key = hashlib.sha256((url + json.dumps(headers or {}, sort_keys=True)).encode()).hexdigest()
        return self.cache / key[:2] / f"{key}.json"

    def _cache_get(self, url: str, headers: dict | None) -> Snapshot | None:
        p = self._cache_path(url, headers)
        if not p or not p.exists():
            return None
        d = json.loads(p.read_text())
        self.budget.cache_hits += 1
        return Snapshot(url=d["url"], final_url=d["final_url"], status=d["status"], retrieved_at=d["retrieved_at"],
                        content=bytes.fromhex(d["content"]), content_type=d["content_type"],
                        redirects=d.get("redirects", []), from_cache=True)

    def _cache_put(self, snap: Snapshot, headers: dict | None) -> None:
        p = self._cache_path(snap.url, headers)
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": snap.url, "final_url": snap.final_url, "status": snap.status,
                                 "retrieved_at": snap.retrieved_at, "content": snap.content.hex(),
                                 "content_type": snap.content_type, "redirects": snap.redirects}))

    # Robots -------------------------------------------------------------
    async def allowed(self, url: str) -> bool:
        """RFC 9309: 4xx robots.txt allows everything (we still skip 401/403 to be safe);
        5xx or an unreachable robots.txt means complete disallow."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self.robots_locks[origin]:
            if origin not in self.robots:
                rp: Protego | None
                try:
                    snap = await self._raw_get(origin + "/robots.txt", None, cacheable=True)
                    if snap.status in (401, 403) or snap.status >= 500:
                        rp = None
                    elif snap.status >= 400:
                        rp = Protego.parse("")
                    else:
                        rp = Protego.parse(snap.text())
                except BudgetExhausted:
                    raise
                except Exception:
                    rp = None
                self.robots[origin] = rp
        rp = self.robots[origin]
        if rp is None:
            return False
        return rp.can_fetch(url, ROBOTS_TOKEN)

    # Fetch --------------------------------------------------------------
    async def get(self, url: str, *, headers: dict | None = None, robots: bool = False,
                  cacheable: bool = True, reserve: int = 0) -> Snapshot:
        if robots and not await self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        return await self._raw_get(url, headers, cacheable=cacheable, reserve=reserve)

    async def _raw_get(self, url: str, headers: dict | None, *, cacheable: bool, reserve: int = 0) -> Snapshot:
        if cacheable:
            hit = self._cache_get(url, headers)
            if hit:
                return hit
        host = urlsplit(url).netloc.lower()
        sem = self.hosts.setdefault(host, asyncio.Semaphore(self.host_limits.get(host, self.per_host_limit)))
        async with self.total, sem:
            self.budget.take(host, reserve)
            gap = self.host_min_interval.get(host)
            if gap:
                wait = self.host_last[host] + gap - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.host_last[host] = time.monotonic()
            retrieved = utc_now()
            r = await self._request_with_retry(url, headers)
            content = r.content[:MAX_BYTES]
            snap = Snapshot(url=url, final_url=str(r.url), status=r.status_code, retrieved_at=retrieved,
                            content=content, content_type=r.headers.get("content-type", ""),
                            redirects=[str(h.url) for h in r.history])
        if cacheable and (snap.status < 500):
            self._cache_put(snap, headers)
        return snap

    async def _request_with_retry(self, url: str, headers: dict | None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(2):
            if attempt:
                self.budget.take(urlsplit(url).netloc.lower())  # a retry is another outbound request
            try:
                slow = self.host_timeouts.get(urlsplit(url).netloc.lower())
                r = await self.client.get(url, headers=headers, timeout=slow) if slow else \
                    await self.client.get(url, headers=headers)
                if r.status_code in (429, 503) and attempt == 0:
                    await asyncio.sleep(min(float(r.headers.get("retry-after", "2") or 2), 5))
                    continue
                return r
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise last or RuntimeError("request failed")
