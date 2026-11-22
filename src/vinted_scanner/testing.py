"""Fake Vinted sessions and real (anonymised) responses, for this package's tests and for the hub's.

    from vinted_scanner.testing import FAST, DiscoveryVinted, adapter_with
    vinted = adapter_with(DiscoveryVinted())
    vinted.search_catalogs("vinted.it", "giochi")
"""
import json
from importlib.resources import files
from typing import Any

import requests

from pch_marketplace_kit.http import HttpPolicy
from pch_marketplace_kit.testing import FakeTime, response
from vinted_scanner.adapter import VintedAdapter

# no waits and no retries: each scripted response is used exactly once
FAST = HttpPolicy(min_interval_s=0, timeout_s=5, max_retries=0, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)

FIXTURE_NAMES = ("catalog_page", "catalogs", "filters", "brand_search", "platform_facets")


def load_fixture(name: str) -> dict[str, Any]:
    """`catalog_page`, `catalogs`, `filters`, `brand_search` or `platform_facets`."""
    if name not in FIXTURE_NAMES:
        raise ValueError(f"unknown fixture {name!r}")
    return json.loads(files("vinted_scanner").joinpath("fixtures", f"{name}.json").read_text())


class FakeVinted:
    """requests-like session: the home page sets the anonymous cookie, the catalogue answers scripted pages."""

    def __init__(self, catalogue: list):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.catalogue = list(catalogue)
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url.startswith("https://www."):
            self.cookies.set("anon_id", f"anon-{len(self.calls)}")
            return response(200, raw=b"<html></html>", headers={"Content-Type": "text/html"})
        return self.catalogue.pop(0)


class DiscoveryVinted(FakeVinted):
    """Answers the filter discovery endpoints by path from the fixtures; `fail=True` answers 403 instead."""

    PATHS = {
        "/svc-navigation/navigation_catalogs": "catalogs", "/svc-filters/filters": "filters",
        "/svc-filters/filters/search": "brand_search", "/svc-filters/filters/facets": "platform_facets",
    }

    def __init__(self, fail: bool = False):
        super().__init__([])
        self.fail = fail

    def request(self, method, url, params=None, headers=None, timeout=None):
        if url.startswith("https://www."):
            return super().request(method, url, params, headers, timeout)
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.fail:
            return response(403, {"message": "blocked"})
        return response(200, load_fixture(self.PATHS[url.split("api.vinted.it", 1)[1]]))


def adapter_with(session: Any, time: FakeTime | None = None) -> VintedAdapter:
    """A VintedAdapter on a fake session and clock: no network, no real waits."""
    time = time or FakeTime()
    return VintedAdapter(policy=FAST, session=session, clock=time.clock, sleep=time.sleep)
