"""Vinted scanner: params validation, normalization of a real (anonymized) catalogue page, session and paging
with a fake transport. No network."""
from decimal import Decimal

import pytest

from vinted_scanner import InvalidQueryError, PermanentVintedError, VintedQuery, VintedScanner, normalize
from vinted_scanner.testing import FAST, FakeTime, FakeVinted, load_fixture, response

PAGE = load_fixture("catalog_page")


# --- params -----------------------------------------------------------------------------------

def test_params_defaults_and_normalisation():
    params = VintedScanner().validate_params({"search_text": " resident evil ", "filters": {"brand": [123, "456"]}})
    assert params == {
        "domain": "vinted.it", "search_text": "resident evil", "order": "newest_first", "per_page": 24,
        "filters": {"brand": ["123", "456"]},
    }


@pytest.mark.parametrize(
    ("raw", "loc"),
    [
        ({}, ("search_text",)),  # neither text nor filters
        ({"search_text": "x", "domain": "evil.example"}, ("domain",)),
        ({"search_text": "x", "order": "random"}, ("order",)),
        ({"search_text": "x", "per_page": 500}, ("per_page",)),
        ({"search_text": "x", "price_from": 5}, ("price_from",)),  # amounts are decimal strings
        ({"search_text": "x", "price_from": "10.00", "price_to": "5.00"}, ("price_to",)),
        ({"search_text": "x", "filters": {"brand": []}}, ("filters", "brand")),
        ({"search_text": "x", "filters": {"Bad Code!": ["1"]}}, ("filters", "Bad Code!")),
        ({"search_text": "x", "filters": {"brand": [""]}}, ("filters", "brand")),
        ({"search_text": "x", "exclude_title_terms": ["cover"]}, ("exclude_title_terms",)),  # use the profile's Excluded Terms
    ],
)
def test_invalid_params_point_at_the_field(raw, loc):
    with pytest.raises(InvalidQueryError) as exc:
        VintedScanner().validate_params(raw)
    assert loc in [error_loc for error_loc, _ in exc.value.errors]


# --- normalization ----------------------------------------------------------------------------

def test_real_catalogue_page_normalises_to_candidates():
    candidates = [normalize(item, "vinted.it") for item in PAGE["items"]]
    first = candidates[0]
    assert (first.external_id, first.title) == ("9000000001", "Figurine ada wong")
    assert first.url == "https://www.vinted.it/items/9000000001-figurine-ada-wong"
    assert (first.price_status, first.price_amount, first.currency) == ("priced", Decimal("32.00"), "EUR")
    assert (first.fees_amount, first.shipping_status, first.shipping_amount) == (Decimal("2.30"), "unknown", None)
    assert first.images == ["https://images.example.test/vinted/1.webp"]
    assert (first.seller_external_id, first.seller_name) == ("101", "venditore_1")
    assert (first.condition, first.condition_raw) == ("good", "Buone")
    assert candidates[1].condition == "like_new" and candidates[1].condition_raw == "Ottime"
    assert first.raw_payload == PAGE["items"][0]
    assert all(c.published_at is None and c.description is None and c.location is None for c in candidates)


@pytest.mark.parametrize(
    ("label", "condition"),
    [("Condizioni: Nuovo con cartellino", "new"), ("Condizioni: Nuovo senza cartellino", "like_new"), ("Condizioni: Discrete", "fair"), ("Condizioni: Strane", None)],
)
def test_condition_mapping(label, condition):
    item = {**PAGE["items"][0], "item_box": {"accessibility_label": f"Titolo, {label}, 5.00 €"}}
    assert normalize(item, "vinted.it").condition == condition


def test_item_without_price_is_missing_not_zero():
    item = {key: value for key, value in PAGE["items"][0].items() if key not in ("price", "service_fee", "total_item_price")}
    candidate = normalize(item, "vinted.it")
    assert (candidate.price_status, candidate.price_amount, candidate.fees_amount) == ("missing", None, None)


# --- session and paging with a fake transport -------------------------------------------------

def scanner(fake: FakeVinted) -> VintedScanner:
    time = FakeTime()
    return VintedScanner(config=FAST, session=fake, clock=time.clock, sleep=time.sleep)


def test_first_page_initialises_the_anonymous_session_and_sends_filters():
    fake = FakeVinted([response(200, PAGE)])
    vinted = scanner(fake)
    params = vinted.validate_params({"search_text": "resident evil", "filters": {"brand": ["123"], "status": ["2", "3"]}})

    page = vinted.fetch_page(params, None)

    home, catalogue = fake.calls
    assert home["url"] == "https://www.vinted.it"
    assert catalogue["url"] == "https://api.vinted.it/svc-catalogue/items"
    assert catalogue["params"] == {
        "search_text": "resident evil", "order": "newest_first", "per_page": 24, "page": 1,
        "attribute_ids[brand]": "123", "attribute_ids[status]": "2,3",
    }
    assert catalogue["headers"]["X-Anon-Id"] == "anon-1" and catalogue["headers"]["Locale"] == "it-IT"
    assert len(page.listings) == 5 and page.next_cursor == 2 and page.newest_first is True


def test_last_page_has_no_next_cursor_and_relevance_is_not_newest_first():
    last = {**PAGE, "pagination": {**PAGE["pagination"], "current_page": 192, "total_pages": 192}}
    fake = FakeVinted([response(200, last)])
    vinted = scanner(fake)
    page = vinted.fetch_page(vinted.validate_params({"search_text": "x", "order": "relevance"}), 192)
    assert (page.next_cursor, page.newest_first) == (None, False)
    assert fake.calls[-1]["params"]["page"] == 192


def test_expired_session_is_renewed_once():
    fake = FakeVinted([response(401, {"message": "unauthorized"}), response(200, PAGE)])
    vinted = scanner(fake)
    page = vinted.fetch_page(vinted.validate_params({"search_text": "x"}), None)
    assert len(page.listings) == 5
    assert [c["url"].split("/")[2] for c in fake.calls] == ["www.vinted.it", "api.vinted.it", "www.vinted.it", "api.vinted.it"]
    assert fake.calls[-1]["headers"]["X-Anon-Id"] == "anon-3"


def test_repeated_401_is_permanent():
    fake = FakeVinted([response(401, {}), response(401, {})])
    vinted = scanner(fake)
    with pytest.raises(PermanentVintedError):
        vinted.fetch_page(vinted.validate_params({"search_text": "x"}), None)


def test_unexpected_schema_is_permanent():
    fake = FakeVinted([response(200, {"message": "blocked"})])
    vinted = scanner(fake)
    with pytest.raises(PermanentVintedError, match="items"):
        vinted.fetch_page(vinted.validate_params({"search_text": "x"}), None)


def test_malformed_items_become_rejected():
    broken = {**PAGE, "items": [PAGE["items"][0], {"id": 1, "title": ""}, "nope"]}
    fake = FakeVinted([response(200, broken)])
    vinted = scanner(fake)
    page = vinted.fetch_page(vinted.validate_params({"search_text": "x"}), None)
    assert len(page.listings) == 1 and len(page.rejected) == 2


def test_capabilities_declare_what_the_catalogue_provides():
    assert VintedScanner.capabilities == frozenset({"images", "seller", "condition"})


# --- typed query, pages and search ------------------------------------------------------------

def test_query_round_trips_through_params():
    query = VintedQuery(search_text="re", filters={"brand": ["1"]}, price_from="5.00")
    assert query.to_params() == {"domain": "vinted.it", "search_text": "re", "order": "newest_first", "per_page": 24, "price_from": "5.00", "filters": {"brand": ["1"]}}
    assert VintedQuery.from_params(query.to_params()) == query
    with pytest.raises(InvalidQueryError):
        VintedQuery()


def test_pages_stop_at_max_pages_and_search_dedupes():
    fake = FakeVinted([response(200, PAGE), response(200, PAGE)])
    vinted = scanner(fake)
    listings = vinted.search(VintedQuery(search_text="x"), max_pages=2)
    assert len(listings) == 5  # the same items on both pages appear once
    assert [c["params"]["page"] for c in fake.calls if c["url"].startswith("https://api.")] == [1, 2]
    with pytest.raises(ValueError):
        next(vinted.pages(VintedQuery(search_text="x"), max_pages=0))


def test_pages_end_on_the_last_page():
    last = {**PAGE, "pagination": {**PAGE["pagination"], "total_pages": 1}}
    vinted = scanner(FakeVinted([response(200, last)]))
    assert len(list(vinted.pages(VintedQuery(search_text="x"), max_pages=3))) == 1
