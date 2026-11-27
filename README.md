# vinted-scanner

Read-only search of the Vinted catalogue, as a Python library and a `vinted-scanner` command line tool. It has no dependency on Personal Collection Hub, pch-marketplace-kit or Opportunity Hunter.

It reads the web catalogue API (`api.<domain>/svc-catalogue/items`) with an anonymous session, through its own rate-limited HTTP client (minimum 3 seconds between requests, timeouts, retries with backoff, `Retry-After`). Each item becomes a `VintedListing`: price and buyer-protection fee, main photo, seller, condition mapped from the Italian label (original kept in `condition_raw`). Shipping is not in the catalogue list, so a listing's shipping stays unknown, never zero; `item_pricing()` reads it from the item's page. Filter discovery (category tree, filters per category, filter options) helps build queries. Nothing is stored and nothing is sent anywhere else. No login, no CAPTCHA or bot-check handling.

## Library

```python
from vinted_scanner import VintedQuery, VintedScanner

vinted = VintedScanner()
query = VintedQuery(search_text="resident evil", filters={"catalog": ["3026"]}, price_to="50.00")
for listing in vinted.search(query, max_pages=2):
    print(listing.title, listing.price_amount, listing.url)

vinted.search_catalogs("vinted.it", "giochi")          # [{"id": "3026", "label": "Elettronica > Videogiochi e console > Giochi"}, ...]
vinted.list_filters("vinted.it", "3026")               # [{"code": "brand", "title": "Brand", "searchable": True}, ...]
vinted.filter_options("vinted.it", "brand", "3026", "capcom", searchable=True)
vinted.item_pricing("vinted.it", "10971144923")        # VintedItemPricing(shipping_status="known", shipping_amount=Decimal("4.99"), fees_amount=Decimal("1.35"), ...)
```

`item_pricing(domain, item_id)` makes one request to the item's public page and reads the delivery and buyer-protection prices Vinted shows there to a buyer with no address: `shipping_status` is `known` (with `shipping_amount`, after any seller discount), `pickup_only` or `unknown`. With `multiple_shipping_options` the amount is the option shown first, so the checkout can differ. A removed item raises `PermanentVintedError` with status 404; a page without the pricing block raises `PermanentVintedError` too. Call it only for the listings you care about: every call is one more paced request.

Applications that store queries as JSON use `validate_params(dict)` and `fetch_page(params, cursor)`; `cursor` is the page number from the previous page's `next_cursor`. Params: `domain` (default `vinted.it`), `search_text`, `order` (`newest_first`, `relevance`, `price_low_to_high`, `price_high_to_low`), `per_page` (1–96), `price_from`, `price_to` (decimal strings), `currency`, `filters` (Vinted filter code → list of option ids). The params accepted by 0.1 are accepted unchanged.

Errors: `InvalidQueryError` (bad params, before any request), `TransientVintedError` (network, timeout, 5xx, 429: try again later) and `PermanentVintedError` (4xx, unexpected response), all subclasses of `VintedError`.

`vinted_scanner.testing` provides fake sessions and clock and real, anonymised responses (`FakeVinted` with `item_pages`, `DiscoveryVinted`, `load_fixture`, `load_item_page`, `item_page_response`, `scanner_with`, `response`, `FakeTime`) for tests without network.

## Command line

```sh
vinted-scanner search "resident evil" --filter catalog=3026 --price-to 50.00 --pages 2
vinted-scanner catalogs giochi
vinted-scanner filters 3026
vinted-scanner options brand capcom --catalog 3026 --searchable
```

Output is one JSON document on stdout; logs go to stderr. Exit codes: 0 ok, 1 permanent error (including invalid arguments), 2 transient error.

## Changes in 0.3

`item_pricing()` and `VintedItemPricing`: shipping and buyer protection from the item page. The catalogue search is unchanged.

## Changes from 0.1

0.1 was a Personal Collection Hub adapter on the pch-marketplace-kit contract. 0.2 stands alone: `VintedAdapter` became `VintedScanner`, kit types became `VintedListing`, `VintedPage`, `VintedRejected` and the errors above, the `pch.marketplaces` entry point is gone, and there is a CLI. Endpoints, parameters, headers, User-Agent, session handling, rate limit, retries and normalisation are unchanged.

## Licence

GPL-3.0-only (see `LICENSE`). Parts of the parameter handling (`build_catalog_params`) and of the filter discovery (`flatten_catalogs`, `flatten_options`, `normalize_search_text`, `search_entries`) are ported from [VintedScanner](https://github.com/drego85/VintedScanner) by Andrea Draghetti, which is GPL-3.0-only; the ported functions say so in their docstrings. The HTTP client and test helpers are adapted from pch-marketplace-kit (MIT, Copyright (c) 2026 Tdavide04). Anyone distributing this package, alone or bundled with an application, distributes GPL-3.0 code.

## Development

```sh
uv sync
uv run ruff check .
uv run mypy
uv run pytest
uv build
```
