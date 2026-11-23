"""Test helpers: fake HTTP sessions and clock, and real (anonymised) Vinted responses. No network, no real sleeps.

    from vinted_scanner.testing import DiscoveryVinted, scanner_with
    vinted = scanner_with(DiscoveryVinted())
    vinted.search_catalogs("vinted.it", "giochi")

`response`, `FakeTime` and `FakeSession` are adapted from `pch_marketplace_kit.testing` (MIT, Copyright (c) 2026 Tdavide04).
"""
import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import requests

from vinted_scanner.config import VintedScannerConfig
from vinted_scanner.scanner import VintedScanner

WALL_START = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
# no waits and no retries: each scripted response is used exactly once
FAST = VintedScannerConfig(min_interval_s=0, timeout_s=5, max_retries=0, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)
FIXTURE_NAMES = ("catalog_page", "catalogs", "filters", "brand_search", "platform_facets")


def response(status: int, body: object = None, headers: dict[str, str] | None = None, raw: bytes | None = None,
             url: str = "https://api.vinted.it/svc-catalogue/items") -> requests.Response:
    """A real `requests.Response` with a JSON body (or `raw` bytes)."""
    r = requests.Response()
    r.status_code = status
    r._content = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
    r.headers.update({"Content-Type": "application/json", **(headers or {})})
    r.url = url
    return r


class FakeTime:
    """Monotonic clock that only moves when the code under test sleeps."""

    def __init__(self, start: float = 1000.0):
        self.start = start
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def wall(self) -> datetime:
        return datetime.fromtimestamp(WALL_START.timestamp() + (self.now - self.start), UTC)


class FakeSession:
    """Plays back scripted outcomes (responses or exceptions) and records each call with its time."""

    def __init__(self, time: FakeTime, outcomes: list[Any]):
        self.time = time
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def request(self, method: str, url: str, params: Any = None, headers: Any = None, timeout: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout, "at": self.time.now, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"unexpected request: {method} {url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def load_fixture(name: str) -> dict[str, Any]:
    """`catalog_page`, `catalogs`, `filters`, `brand_search` or `platform_facets`."""
    if name not in FIXTURE_NAMES:
        raise ValueError(f"unknown fixture {name!r}")
    data: dict[str, Any] = json.loads(files("vinted_scanner").joinpath("fixtures", f"{name}.json").read_text())
    return data


class FakeVinted:
    """requests-like session: the home page sets the anonymous cookie, the catalogue answers scripted pages."""

    def __init__(self, catalogue: list[Any]):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.catalogue = list(catalogue)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, params: Any = None, headers: Any = None, timeout: Any = None) -> Any:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url.startswith("https://www."):
            self.cookies.set("anon_id", f"anon-{len(self.calls)}")
            return response(200, raw=b"<html></html>", headers={"Content-Type": "text/html"})
        outcome = self.catalogue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class DiscoveryVinted(FakeVinted):
    """Answers the filter discovery endpoints by path from the fixtures; `fail=True` answers 403 instead."""

    PATHS = {
        "/svc-navigation/navigation_catalogs": "catalogs", "/svc-filters/filters": "filters",
        "/svc-filters/filters/search": "brand_search", "/svc-filters/filters/facets": "platform_facets",
    }

    def __init__(self, fail: bool = False):
        super().__init__([])
        self.fail = fail

    def request(self, method: str, url: str, params: Any = None, headers: Any = None, timeout: Any = None) -> Any:
        if url.startswith("https://www."):
            return super().request(method, url, params, headers, timeout)
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.fail:
            return response(403, {"message": "blocked"})
        return response(200, load_fixture(self.PATHS[url.split("api.vinted.it", 1)[1]]))


def scanner_with(session: Any, time: FakeTime | None = None) -> VintedScanner:
    """A VintedScanner on a fake session and clock: no network, no real waits."""
    time = time or FakeTime()
    return VintedScanner(config=FAST, session=session, clock=time.clock, sleep=time.sleep, wall_clock=time.wall)
