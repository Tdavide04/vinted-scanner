# vinted-scanner

Vinted adapter for [Personal Collection Hub](https://github.com/Tdavide04/personal-collection-hub), built on the [pch-marketplace-kit](https://github.com/Tdavide04/pch-marketplace-kit) contract.

It reads the web catalogue API (`api.<domain>/svc-catalogue/items`) with an anonymous session, read-only, through the kit's rate-limited HTTP client, and turns each item into a `ListingCandidate`: price and buyer-protection fee, main photo, seller, condition mapped from the Italian label (original kept in `condition_raw`). It also offers filter discovery (category tree, filters per category, filter options) for building queries. It keeps no state beyond its HTTP session and stores nothing.

## Use

The hub discovers it through the `pch.marketplaces` entry point (`vinted`). On its own:

```python
from vinted_scanner import VintedAdapter

vinted = VintedAdapter()
params = vinted.validate_params({"search_text": "resident evil", "filters": {"catalog": ["3026"]}})
page = vinted.fetch_page(params, None)  # next page: vinted.fetch_page(params, page.next_cursor)
vinted.search_catalogs("vinted.it", "giochi")
```

Query params: `domain` (default `vinted.it`), `search_text`, `order` (`newest_first`, `relevance`, `price_low_to_high`, `price_high_to_low`), `per_page`, `price_from`, `price_to`, `currency`, `filters` (Vinted filter code → list of option ids).

`vinted_scanner.testing` provides fake sessions and real, anonymised responses (`FakeVinted`, `DiscoveryVinted`, `load_fixture`, `adapter_with`) for tests that use the adapter without network.

## Licence

GPL-3.0-only. The parameter handling is ported from a fork of VintedScanner, which is GPL-3.0-only (PCH ADR-0002).

## Development

```sh
uv sync
uv run pytest
```
