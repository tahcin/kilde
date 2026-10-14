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
