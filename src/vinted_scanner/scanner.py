"""Vinted scanner: the web catalogue API (`api.<domain>/svc-catalogue/items`) with an anonymous session.

Parts of the parameter handling and of the filter discovery are ported from VintedScanner by Andrea Draghetti
(GPL-3.0-only), as marked below; this package is therefore GPL-3.0-only too (see the README). Access is
read-only and goes through the rate-limited client: one anonymous session, no login, no CAPTCHA handling.
"""
import re
import unicodedata
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from vinted_scanner.config import DEFAULT_CONFIG, VintedScannerConfig
from vinted_scanner.errors import PermanentVintedError
from vinted_scanner.http import RateLimitedHttp
from vinted_scanner.models import VintedListing, VintedPage, VintedRejected
from vinted_scanner.query import DOMAIN, VintedQuery, validate_params

LOCALES = {"vinted.it": "it-IT", "vinted.fr": "fr-FR", "vinted.es": "es-ES", "vinted.de": "de-DE"}
CATALOGUE_PATH = "/svc-catalogue/items"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0"

# Vinted exposes the condition only as localized text ("Condizioni: Buone"); unknown labels stay in condition_raw
CONDITIONS = {
    "nuovo con cartellino": "new",
    "nuovo senza cartellino": "like_new",
    "ottime": "like_new",
    "ottime condizioni": "like_new",
    "buone": "good",
    "buone condizioni": "good",
    "discrete": "fair",
    "discrete condizioni": "fair",
}
CONDITION_LABEL = re.compile(r"Condizioni:\s*([^,]+)")
CATALOG_CACHE_SECONDS = 3600
DISCOVERY_LIMIT = 50
NOT_DISCOVERABLE = {"price", "catalog"}  # price has its own fields; catalog is chosen from the tree


class VintedScanner:
    """Searches the Vinted catalogue. One instance per process: its HTTP client paces every request it makes.

    `pages()` and `search()` take a `VintedQuery`; `validate_params()` and `fetch_page()` work on plain params
    dictionaries, for applications that store queries as JSON. `search_catalogs()`, `list_filters()` and
    `filter_options()` help build queries.
    """

    marketplace = "vinted"
    # the catalogue list has no description, location or publication date
    capabilities = frozenset({"images", "seller", "condition"})

    def __init__(
        self,
        config: VintedScannerConfig = DEFAULT_CONFIG,
        session: Any | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        jitter: Callable[[float], float] | None = None,
    ):
        options: dict[str, Any] = {
            name: value
            for name, value in (("clock", clock), ("sleep", sleep), ("wall_clock", wall_clock), ("jitter", jitter))
            if value is not None
        }
        self.config = config
        self._http = RateLimitedHttp(config, session=session, **options)
        self._session_domain: str | None = None
        self._catalog_cache: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}

    # --- params ---------------------------------------------------------------------------------

    def validate_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalised params, or InvalidQueryError listing every problem."""
        return validate_params(raw)

    # --- acquisition ----------------------------------------------------------------------------

    def fetch_page(self, params: dict[str, Any], cursor: int | None) -> VintedPage:
        """One page of results for already validated params; `cursor` is the page number from the previous page."""
        domain = params["domain"]
        page_number = cursor or 1
        query = {key: params[key] for key in ("search_text", "order", "per_page", "price_from", "price_to", "currency") if params.get(key) not in (None, "")}
        query["page"] = page_number
        for code, ids in params.get("filters", {}).items():
            query[f"attribute_ids[{code}]"] = ",".join(ids)

        data = self._catalogue(domain, query)
        items, pagination = data.get("items"), data.get("pagination") or {}
        if not isinstance(items, list):
            raise PermanentVintedError("vinted: response without an 'items' list (the API may have changed or blocked the request)")

        listings: list[VintedListing] = []
        rejected: list[VintedRejected] = []
        for raw in items:
            try:
                listings.append(normalize(raw, domain))
            except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError) as exc:
                rejected.append(VintedRejected(external_id=str(raw.get("id")) if isinstance(raw, dict) and raw.get("id") else None, reason=str(exc)))
        total_pages = pagination.get("total_pages")
        next_cursor = page_number + 1 if isinstance(total_pages, int) and page_number < total_pages else None
        return VintedPage(listings=listings, rejected=rejected, next_cursor=next_cursor, newest_first=params["order"] == "newest_first")

    def pages(self, query: VintedQuery, max_pages: int = 3) -> Iterator[VintedPage]:
        """Pages in the query's order, stopping after `max_pages` or at the last page. Requests are made lazily."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        params = validate_params(query.to_params())
        cursor: int | None = None
        for _ in range(max_pages):
            page = self.fetch_page(params, cursor)
            yield page
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    def search(self, query: VintedQuery, max_pages: int = 1) -> list[VintedListing]:
        """Listings from up to `max_pages` pages, each item once (items can move between pages)."""
        seen: set[str] = set()
        listings: list[VintedListing] = []
        for page in self.pages(query, max_pages=max_pages):
            for listing in page.listings:
                if listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
        return listings

    # --- filter discovery ----------------------------------------------------------------------

    def search_catalogs(self, domain: str, text: str) -> list[dict[str, str]]:
        """Categories with a readable path ("Elettronica > Videogiochi e console > Giochi"), accent/case-insensitive."""
        key = (domain, LOCALES.get(domain, "en-GB"))
        cached = self._catalog_cache.get(key)
        if cached is None or self._http.clock() - cached[0] > CATALOG_CACHE_SECONDS:
            data = self._get(domain, "/svc-navigation/navigation_catalogs", {"virtual_categories": "false"})
            if not isinstance(data.get("catalogs"), list):
                raise PermanentVintedError("vinted: no category tree in the response")
            cached = (self._http.clock(), _flatten(data["catalogs"]))
            self._catalog_cache[key] = cached
        return _search(cached[1], text)

    def list_filters(self, domain: str, catalog_id: str) -> list[dict[str, Any]]:
        data = self._get(domain, "/svc-filters/filters", {"attribute_ids[catalog]": catalog_id})
        filters = data.get("filters")
        if not isinstance(filters, list):
            raise PermanentVintedError("vinted: no filter list in the response")
        return [
            {"code": f["code"], "title": f.get("title") or f["code"], "searchable": f.get("display_type") == "list_search"}
            for f in filters if isinstance(f, dict) and f.get("code") and f["code"] not in NOT_DISCOVERABLE
        ]

    def filter_options(self, domain: str, code: str, catalog_id: str, text: str, searchable: bool = False) -> list[dict[str, Any]]:
        """Searchable filters (e.g. brand, thousands of values) ask Vinted; list filters fetch their facets and are
        searched locally."""
        base = {"attribute_ids[catalog]": catalog_id} if catalog_id else {}
        if searchable and text.strip():
            searched = self._get(domain, "/svc-filters/filters/search", {**base, "filter_search_code": code, "filter_search_text": text.strip()})
            if searched.get("options"):
                return _options(searched["options"])[:DISCOVERY_LIMIT]
        facets = self._get(domain, "/svc-filters/filters/facets", {**base, "filter_code": code})
        if not isinstance(facets.get("options"), list):
            raise PermanentVintedError(f"vinted: no options for filter {code}")
        return _search(_options(facets["options"]), text)

    def _get(self, domain: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._json(domain, f"https://api.{domain}{path}", params)

    def _catalogue(self, domain: str, query: dict[str, Any]) -> dict[str, Any]:
        return self._json(domain, f"https://api.{domain}{CATALOGUE_PATH}", query)

    def _json(self, domain: str, url: str, query: dict[str, Any]) -> dict[str, Any]:
        _require_vinted_domain(domain)
        for attempt in range(2):
            if self._session_domain != domain:
                self._start_session(domain)
            try:
                data = self._http.get_json(url, params=query, headers=self._api_headers(domain))
            except PermanentVintedError as exc:
                if exc.status == 401 and attempt == 0:
                    self._session_domain = None  # anonymous session expired: start a new one, once
                    continue
                raise
            if not isinstance(data, dict):
                raise PermanentVintedError("vinted: response is not a JSON object")
            return data
        raise AssertionError("unreachable")

    def _start_session(self, domain: str) -> None:
        _require_vinted_domain(domain)
        # the home page sets the anonymous session cookies; they hold tokens, so they are never logged
        self._http.get(f"https://www.{domain}", headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "it-IT,it;q=0.8,en;q=0.5"})
        self._session_domain = domain

    def _api_headers(self, domain: str) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "it-IT,it;q=0.8,en;q=0.5",
            "Platform": "web",
            "x-next-app": "marketplace-web",
            "Origin": f"https://www.{domain}",
            "Referer": f"https://www.{domain}/",
            "Locale": LOCALES.get(domain, "en-GB"),
        }
        anon_id = self._http.session.cookies.get("anon_id")
        if anon_id:
            headers["X-Anon-Id"] = anon_id
        return headers


def _require_vinted_domain(domain: str) -> None:
    """Every URL is built from the domain: refuse anything but an exact Vinted domain (defence against SSRF)."""
    if not isinstance(domain, str) or not DOMAIN.fullmatch(domain):
        raise ValueError(f"not a Vinted domain: {domain!r}")


# _fold and _search follow VintedScanner's `normalize_search_text` and `search_entries` (GPL-3.0-only)
def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _search(entries: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    needle = _fold(text).strip()
    found = [e for e in entries if needle in _fold(e["label"])] if needle else entries
    return found[:DISCOVERY_LIMIT]


def _flatten(nodes: list[Any], path: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Category tree → flat list with readable paths. Ported from VintedScanner (`flatten_catalogs`), GPL-3.0-only."""
    flat = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("id") is None or not node.get("title"):
            continue
        titles = (*path, str(node["title"]))
        flat.append({"id": str(node["id"]), "label": " > ".join(titles)})
        flat.extend(_flatten(node.get("catalogs") or [], titles))
    return flat


def _options(options: list[Any], path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Facet options, flattening groups such as sizes. Ported from VintedScanner (`flatten_options`), GPL-3.0-only."""
    flat = []
    for option in options:
        if not isinstance(option, dict) or option.get("id") is None or not option.get("title"):
            continue
        titles = (*path, str(option["title"]))
        children = option.get("options") or []
        if isinstance(children, list) and children:
            flat.extend(_options(children, titles))
        else:
            flat.append({"id": str(option["id"]), "label": " > ".join(titles), "items_count": option.get("items_count")})
    return flat


def _money(value: Any) -> tuple[Decimal, str] | None:
    if not isinstance(value, dict) or value.get("amount") in (None, "") or not value.get("currency_code"):
        return None
    return Decimal(str(value["amount"])).quantize(Decimal("0.01")), str(value["currency_code"])


def normalize(raw: Any, domain: str) -> VintedListing:
    """One catalogue item → VintedListing. Shipping is not in the list: it stays unknown, never zero."""
    if not isinstance(raw, dict):
        raise ValueError("item is not a JSON object")
    price = _money(raw.get("price"))
    fee = _money(raw.get("service_fee"))
    photo: dict[str, Any] = raw["photo"] if isinstance(raw.get("photo"), dict) else {}
    user: dict[str, Any] = raw["user"] if isinstance(raw.get("user"), dict) else {}
    label = (raw.get("item_box") or {}).get("accessibility_label") or ""
    found = CONDITION_LABEL.search(label)
    condition_raw = found.group(1).strip() if found else None
    return VintedListing(
        external_id=str(raw["id"]) if raw.get("id") is not None else None,  # type: ignore[arg-type]  # None is rejected by VintedListing
        title=raw.get("title"),  # type: ignore[arg-type]
        url=urljoin(f"https://www.{domain}/", str(raw.get("url") or "")) if raw.get("url") else None,  # type: ignore[arg-type]
        price_status="priced" if price else "missing",
        price_amount=price[0] if price else None,
        currency=price[1] if price else None,
        fees_amount=fee[0] if fee and price and fee[1] == price[1] else None,
        images=[photo["full_size_url"]] if photo.get("full_size_url") else [],
        seller_external_id=str(user["id"]) if user.get("id") is not None else None,
        seller_name=user.get("login"),
        condition=CONDITIONS.get(condition_raw.casefold()) if condition_raw else None,  # type: ignore[arg-type]
        condition_raw=condition_raw,
        raw_payload=raw,
    )
