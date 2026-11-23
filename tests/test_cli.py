"""Command line, with the scanner on a fake transport: JSON on stdout, logs on stderr, exit codes. No network."""
import json

import pytest
import requests

from vinted_scanner import VintedScanner, VintedScannerConfig
from vinted_scanner.cli import main
from vinted_scanner.testing import DiscoveryVinted, FakeTime, FakeVinted, load_fixture, response, scanner_with

FAST = VintedScannerConfig(min_interval_s=0, timeout_s=5, max_retries=1, backoff_base_s=0, backoff_cap_s=0, max_retry_after_s=0)
PAGE = load_fixture("catalog_page")


def factory(outcomes):
    time = FakeTime()
    session = FakeVinted(outcomes)
    return (lambda: VintedScanner(config=FAST, session=session, clock=time.clock, sleep=time.sleep)), session


def test_search_prints_json_on_stdout(capsys):
    make, session = factory([response(200, PAGE)])
    code = main(["search", "resident evil", "--filter", "catalog=3026", "--filter", "status=2,3", "--price-to", "50.00"], scanner_factory=make)
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    document = json.loads(out)
    assert document["query"] == {
        "domain": "vinted.it", "search_text": "resident evil", "order": "newest_first", "per_page": 24, "price_to": "50.00",
        "filters": {"catalog": ["3026"], "status": ["2", "3"]},
    }
    assert document["pages_read"] == 1 and document["next_cursor"] == 2
    assert len(document["listings"]) == 5 and document["rejected"] == []
    first = document["listings"][0]
    assert first["external_id"] == "9000000001" and first["price_amount"] == "32.00" and first["fees_amount"] == "2.30" and "raw_payload" not in first
    home, catalogue = session.calls
    assert home["url"] == "https://www.vinted.it"
    assert catalogue["params"] == {
        "search_text": "resident evil", "order": "newest_first", "per_page": 24, "price_to": "50.00", "page": 1,
        "attribute_ids[catalog]": "3026", "attribute_ids[status]": "2,3",
    }


def test_raw_includes_payload_and_pages_follow_page_numbers(capsys):
    make, session = factory([response(200, PAGE), response(200, PAGE)])
    assert main(["search", "x", "--pages", "2", "--raw"], scanner_factory=make) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["pages_read"] == 2
    assert len(document["listings"]) == 5  # the same items on both pages are printed once
    assert document["listings"][0]["raw_payload"] == PAGE["items"][0]
    assert [c["params"]["page"] for c in session.calls if "page" in (c["params"] or {})] == [1, 2]


def test_transient_error_exits_2_with_nothing_on_stdout(capsys):
    make, _ = factory([response(503), response(503)])
    assert main(["search", "x"], scanner_factory=make) == 2
    out, err = capsys.readouterr()
    assert out == "" and "503" in err


def test_network_error_exits_2(capsys):
    make, _ = factory([requests.ConnectionError("down")] * 2)
    assert main(["search", "x"], scanner_factory=make) == 2
    assert capsys.readouterr().out == ""


def test_permanent_error_exits_1(capsys):
    make, _ = factory([response(403)])
    assert main(["search", "x"], scanner_factory=make) == 1
    out, err = capsys.readouterr()
    assert out == "" and "403" in err


@pytest.mark.parametrize(
    "argv",
    [["search"], ["search", "x", "--domain", "evil.example"], ["search", "x", "--price-to", "abc"], ["search", "x", "--per-page", "0"],
     ["search", "x", "--pages", "0"], ["search", "x", "--order", "random"], ["search", "x", "--filter", "brand"],
     ["search", "x", "--price-from", "50", "--price-to", "5"], ["catalogs", "x", "--domain", "vinted.it.evil.example"], []],
)
def test_invalid_arguments_exit_1_without_requests(argv, capsys):
    make, session = factory([])
    with pytest.raises(SystemExit) as exc:
        code = main(argv, scanner_factory=make)
        raise SystemExit(code)
    assert exc.value.code == 1
    assert session.calls == [] and capsys.readouterr().out == ""


def test_discovery_commands(capsys):
    fake = DiscoveryVinted()
    make = lambda: scanner_with(fake)  # noqa: E731
    assert main(["catalogs", "videogiochi"], scanner_factory=make) == 0
    assert {"id": "3026", "label": "Elettronica > Videogiochi e console > Giochi"} in json.loads(capsys.readouterr().out)
    assert main(["filters", "3026"], scanner_factory=make) == 0
    assert "brand" in {f["code"] for f in json.loads(capsys.readouterr().out)}
    assert main(["options", "brand", "capcom", "--catalog", "3026", "--searchable"], scanner_factory=make) == 0
    assert json.loads(capsys.readouterr().out)[0]["label"] == "Capcom"


def test_blocked_discovery_exits_1(capsys):
    fake = DiscoveryVinted(fail=True)
    assert main(["catalogs", "x"], scanner_factory=lambda: scanner_with(fake)) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [["--help"], ["search", "--help"], ["--version"]])
def test_help_and_version_exit_0(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 0
    assert "vinted-scanner" in capsys.readouterr().out
