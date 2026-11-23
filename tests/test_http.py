"""Rate-limited HTTP client (ported with the client from pch-marketplace-kit, MIT), tested with a fake transport, clock and sleep (no network, no real waits)."""
import json
import logging
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
import requests

from vinted_scanner.config import VintedScannerConfig as HttpPolicy
from vinted_scanner.errors import PermanentVintedError, TransientVintedError
from vinted_scanner.http import RateLimitedHttp
from vinted_scanner.testing import WALL_START, FakeSession, FakeTime, response

POLICY = HttpPolicy(min_interval_s=0.0, timeout_s=10.0, max_retries=3, backoff_base_s=1.0, backoff_cap_s=8.0, max_retry_after_s=60.0)
SPACED = HttpPolicy(min_interval_s=2.0, timeout_s=10.0, max_retries=3, backoff_base_s=1.0, backoff_cap_s=8.0, max_retry_after_s=60.0)


@pytest.fixture
def time():
    return FakeTime()


def client(time: FakeTime, outcomes: list, policy: HttpPolicy = POLICY) -> tuple[RateLimitedHttp, FakeSession]:
    session = FakeSession(time, outcomes)
    http = RateLimitedHttp(
        policy, session=session, clock=time.clock, sleep=time.sleep, wall_clock=time.wall, jitter=lambda delay: delay
    )
    return http, session


def test_success_returns_parsed_json_with_timeout_and_headers(time):
    http, session = client(time, [response(200, {"items": [1]})])
    assert http.get_json("https://marketplace.test/api", params={"q": "re"}, headers={"Accept": "application/json"}) == {"items": [1]}
    (call,) = session.calls
    assert (call["method"], call["params"], call["timeout"]) == ("GET", {"q": "re"}, 10.0)
    assert call["headers"]["Accept"] == "application/json"


def test_503_then_200_is_retried_with_exponential_backoff(time):
    http, session = client(time, [response(503), response(502), response(200, {"ok": True})])
    assert http.get_json("https://marketplace.test/api") == {"ok": True}
    assert len(session.calls) == 3
    assert time.sleeps == [1.0, 2.0]


def test_backoff_is_capped(time):
    policy = HttpPolicy(min_interval_s=0, timeout_s=5, max_retries=5, backoff_base_s=4.0, backoff_cap_s=10.0, max_retry_after_s=60)
    http, _ = client(time, [response(500)] * 5 + [response(200)], policy)
    http.get_json("https://marketplace.test/api")
    assert time.sleeps == [4.0, 8.0, 10.0, 10.0, 10.0]


def test_jitter_is_applied_to_backoff(time):
    session = FakeSession(time, [response(503), response(200)])
    http = RateLimitedHttp(POLICY, session=session, clock=time.clock, sleep=time.sleep, wall_clock=time.wall, jitter=lambda d: d / 2)
    http.get_json("https://marketplace.test/api")
    assert time.sleeps == [0.5]


def test_default_jitter_stays_within_half_and_full_delay(time):
    session = FakeSession(time, [response(503), response(200)])
    RateLimitedHttp(POLICY, session=session, clock=time.clock, sleep=time.sleep).get_json("https://marketplace.test/api")
    assert 0.5 <= time.sleeps[0] <= 1.0


def test_429_honours_retry_after_in_seconds(time):
    http, _ = client(time, [response(429, headers={"Retry-After": "7"}), response(200)])
    http.get_json("https://marketplace.test/api")
    assert time.sleeps == [7.0]


def test_429_honours_retry_after_as_http_date(time):
    retry_at = format_datetime(datetime.fromtimestamp(WALL_START.timestamp() + 30, UTC), usegmt=True)
    http, _ = client(time, [response(429, headers={"Retry-After": retry_at}), response(200)])
    http.get_json("https://marketplace.test/api")
    assert time.sleeps == [30.0]


def test_429_without_retry_after_uses_backoff(time):
    http, _ = client(time, [response(429), response(200)])
    http.get_json("https://marketplace.test/api")
    assert time.sleeps == [1.0]


def test_retry_after_beyond_the_limit_fails_fast_as_transient(time):
    # waiting an hour would stall the caller: give up and let the next search try
    http, session = client(time, [response(429, headers={"Retry-After": "3600"})])
    with pytest.raises(TransientVintedError, match="Retry-After"):
        http.get_json("https://marketplace.test/api")
    assert len(session.calls) == 1 and time.sleeps == []


@pytest.mark.parametrize(
    "error", [requests.Timeout("read timed out"), requests.ConnectionError("connection refused")], ids=["timeout", "connection"]
)
def test_network_errors_are_retried_until_exhausted_then_transient(time, error):
    http, session = client(time, [error] * 4)
    with pytest.raises(TransientVintedError):
        http.get_json("https://marketplace.test/api")
    assert len(session.calls) == 4  # first attempt + max_retries
    assert time.sleeps == [1.0, 2.0, 4.0]


def test_5xx_exhausted_is_transient(time):
    http, _ = client(time, [response(503)] * 4)
    with pytest.raises(TransientVintedError, match="503"):
        http.get_json("https://marketplace.test/api")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_4xx_other_than_429_is_permanent_without_retry(time, status):
    http, session = client(time, [response(status)])
    with pytest.raises(PermanentVintedError, match=str(status)):
        http.get_json("https://marketplace.test/api")
    assert len(session.calls) == 1 and time.sleeps == []


def test_non_json_body_when_json_expected_is_permanent(time):
    http, session = client(time, [response(200, raw=b"<html>blocked</html>", headers={"Content-Type": "text/html"})])
    with pytest.raises(PermanentVintedError, match="JSON"):
        http.get_json("https://marketplace.test/api")
    assert len(session.calls) == 1


def test_minimum_interval_between_requests_is_enforced_on_the_clock(time):
    http, session = client(time, [response(200), response(200), response(200)], SPACED)
    http.get_json("https://marketplace.test/a")
    time.now += 0.5  # caller did other work
    http.get_json("https://marketplace.test/b")
    time.now += 5.0  # long pause: no wait needed
    http.get_json("https://marketplace.test/c")

    starts = [call["at"] for call in session.calls]
    assert starts[1] - starts[0] == pytest.approx(2.0)
    assert starts[2] - starts[1] >= 5.0
    assert time.sleeps == [pytest.approx(1.5)]


def test_minimum_interval_also_spaces_retries(time):
    policy = HttpPolicy(min_interval_s=3.0, timeout_s=5, max_retries=2, backoff_base_s=1.0, backoff_cap_s=8.0, max_retry_after_s=60)
    http, session = client(time, [response(503), response(200)], policy)
    http.get_json("https://marketplace.test/api")
    starts = [call["at"] for call in session.calls]
    # one wait covering both: the longer of backoff (1 s) and the remaining interval (3 s)
    assert starts[1] - starts[0] == pytest.approx(3.0)
    assert time.sleeps == [pytest.approx(3.0)]


def test_get_returns_the_response_for_non_json_content(time):
    http, _ = client(time, [response(200, raw=b"<html>ok</html>", headers={"Content-Type": "text/html"})])
    assert http.get("https://marketplace.test/page").text == "<html>ok</html>"


def test_each_retry_is_logged_without_query_parameters(time, caplog):
    http, _ = client(time, [response(503), response(200)])
    with caplog.at_level(logging.WARNING, logger="vinted_scanner.http"):
        http.get_json("https://marketplace.test/api?token=SECRET123", params={"session": "SECRET456"})

    (record,) = [r for r in caplog.records if r.event == "http_retry"]
    assert (record.marketplace, record.attempt, record.status) == ("vinted", 1, 503)
    assert record.delay_s == 1.0
    logged = json.dumps({k: str(v) for k, v in vars(record).items()})
    assert "SECRET" not in logged
    assert record.url == "https://marketplace.test/api"
