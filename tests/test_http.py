"""Fetcher robots.txt handling (RFC 9309), on a mock transport."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from kilde.http import Blocked, Budget, Fetcher


def fetcher(robots_status: int, robots_body: str = "") -> Fetcher:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots_body)
        return httpx.Response(200, text="<html><body>ok</body></html>", headers={"content-type": "text/html"})
    f = Fetcher(Budget(50, 60))
    f.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return f


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_robots_4xx_means_no_rules(status):
    f = fetcher(status)
    snap = asyncio.run(f.get("https://fjellbekk.no/", robots=True))
    assert snap.status == 200


@pytest.mark.parametrize("status", [500, 503])
def test_robots_5xx_means_complete_disallow(status):
    f = fetcher(status)
    with pytest.raises(Blocked):
        asyncio.run(f.get("https://fjellbekk.no/", robots=True))


def test_robots_rules_obeyed():
    f = fetcher(200, "User-agent: *\nDisallow: /\n")
    with pytest.raises(Blocked):
        asyncio.run(f.get("https://fjellbekk.no/", robots=True))


def test_retry_after_header_forms():
    from kilde.http import retry_after
    assert retry_after(None) == 2.0 and retry_after("1") == 1.0 and retry_after("120") == 5.0
    assert retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # a date in the past: no wait
    assert retry_after("soon") == 2.0


def test_page_byte_cap_leaves_room_for_inline_images():
    from kilde import http
    assert http.MAX_BYTES >= 6_000_000
