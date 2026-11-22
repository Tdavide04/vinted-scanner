"""The adapter honours the pch-marketplace-kit contract and is discoverable through its entry point."""
from importlib.metadata import entry_points

from pch_marketplace_kit import ENTRY_POINT_GROUP, discover_adapters
from pch_marketplace_kit.testing import assert_adapter_shape, assert_valid_page, response
from vinted_scanner import VintedAdapter, create_adapter
from vinted_scanner.testing import FakeVinted, adapter_with, load_fixture


def test_shape():
    assert_adapter_shape(create_adapter({}))


def test_entry_point_builds_the_adapter():
    (entry,) = [e for e in entry_points(group=ENTRY_POINT_GROUP) if e.name == "vinted"]
    assert isinstance(discover_adapters({}, [entry])["vinted"], VintedAdapter)


def test_the_fixture_page_is_valid():
    vinted = adapter_with(FakeVinted([response(200, load_fixture("catalog_page"))]))
    page = vinted.fetch_page(vinted.validate_params({"search_text": "x"}), None)
    assert page.candidates
    assert_valid_page(page, vinted.capabilities)
