"""Vinted filter discovery on the adapter, with a fake transport on real (trimmed) responses. No network."""
import pytest

from pch_marketplace_kit.testing import FakeTime
from vinted_scanner.testing import DiscoveryVinted, adapter_with


@pytest.fixture
def time():
    return FakeTime()


@pytest.fixture
def fake():
    return DiscoveryVinted()


@pytest.fixture
def vinted(fake, time):
    return adapter_with(fake, time)


def paths(fake) -> list[str]:
    return [c["url"].split("vinted.it", 1)[1] or "/" for c in fake.calls]


def test_catalogs_have_readable_paths_and_accent_insensitive_search(vinted):
    results = vinted.search_catalogs("vinted.it", "VIDEOGIOCHI")
    labels = [r["label"] for r in results]
    assert "Elettronica > Videogiochi e console > Giochi" in labels
    assert all("videogiochi" in label.casefold() for label in labels)
    assert {"id": "3026", "label": "Elettronica > Videogiochi e console > Giochi"} in results


def test_catalog_tree_is_cached_per_domain_and_expires(vinted, fake, time):
    vinted.search_catalogs("vinted.it", "giochi")
    vinted.search_catalogs("vinted.it", "console")
    assert paths(fake).count("/svc-navigation/navigation_catalogs") == 1
    time.now += 3601  # one hour later
    vinted.search_catalogs("vinted.it", "giochi")
    assert paths(fake).count("/svc-navigation/navigation_catalogs") == 2


def test_filters_are_contextual_to_the_catalog(vinted, fake):
    filters = vinted.list_filters("vinted.it", "3026")
    assert {f["code"] for f in filters} == {"brand", "status", "color", "video_game_platform"}  # price has its own fields
    assert next(f for f in filters if f["code"] == "brand")["searchable"] is True
    assert fake.calls[-1]["params"] == {"attribute_ids[catalog]": "3026"}


def test_searchable_filter_options_use_the_search_endpoint(vinted, fake):
    options = vinted.filter_options("vinted.it", "brand", "3026", "capcom", searchable=True)
    assert options[0] == {"id": "308275", "label": "Capcom", "items_count": None}
    assert fake.calls[-1]["params"]["filter_search_text"] == "capcom"


def test_list_filter_options_use_facets_and_local_accent_insensitive_search(vinted, fake):
    all_options = vinted.filter_options("vinted.it", "video_game_platform", "3026", "")
    assert len(all_options) > 3 and all_options[0]["items_count"] is not None
    acer = vinted.filter_options("vinted.it", "video_game_platform", "3026", "ACER")
    assert acer and all("acer" in o["label"].casefold() for o in acer)
    assert fake.calls[-1]["params"]["filter_code"] == "video_game_platform"


@pytest.mark.parametrize("domain", ["evil.example/x?vinted.it", "vinted.it.evil.example"])
def test_adapter_refuses_to_build_urls_for_other_hosts(vinted, fake, domain):
    with pytest.raises(ValueError):
        vinted.search_catalogs(domain, "")
    assert fake.calls == []
