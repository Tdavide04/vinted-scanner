"""Vinted adapter: the web catalogue API (`api.<domain>/svc-catalogue/items`) with an anonymous session.

Parameter handling is ported from the user's VintedScanner fork (`build_catalog_params`), which is GPL-3.0:
see PCH ADR-0002 for what that means if PCH is ever distributed. Access is read-only and goes through the
rate-limited client; the decision to use this endpoint is recorded in PCH ADR-0011.
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from pch_marketplace_kit.domain import ListingCandidate
from pch_marketplace_kit.adapter import InvalidParams, Page, PermanentMarketplaceError, Rejected
from pch_marketplace_kit.http import HttpPolicy, MarketplaceHttp

ORDERS = ("newest_first", "relevance", "price_low_to_high", "price_high_to_low")
DOMAIN = re.compile(r"vinted\.(it|fr|es|de|at|be|nl|lu|pt|pl|cz|sk|lt|lv|ee|hu|ro|hr|fi|dk|se|ie|gr|co\.uk|com)")
FILTER_CODE = re.compile(r"[a-z][a-z0-9_]{0,40}")
LOCALES = {"vinted.it": "it-IT", "vinted.fr": "fr-FR", "vinted.es": "es-ES", "vinted.de": "de-DE"}
DEFAULT_POLICY = HttpPolicy(min_interval_s=3.0, timeout_s=30.0, max_retries=3, backoff_base_s=2.0, backoff_cap_s=60.0, max_retry_after_s=120.0)
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


class VintedAdapter:
    marketplace = "vinted"
    # the catalogue list has no description, location or publication date: those are "not applicable" (PCH ADR-0005)
    capabilities = frozenset({"images", "seller", "condition"})

    def __init__(self, policy: HttpPolicy = DEFAULT_POLICY, session: Any | None = None, **http_options: Any):
        self._http = MarketplaceHttp("vinted", policy, session=session, **http_options)
        self._session_domain: str | None = None
        self._catalog_cache: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}

    # --- params ---------------------------------------------------------------------------------

    def validate_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        errors: list[tuple[tuple[str, ...], str]] = []
        allowed = {"domain", "search_text", "order", "per_page", "price_from", "price_to", "currency", "filters"}
        for key in raw:
            if key == "exclude_title_terms":
                errors.append(((key,), "use the Search Profile's Excluded Terms instead"))
            elif key not in allowed:
                errors.append(((key,), "unknown parameter"))

        domain = str(raw.get("domain", "vinted.it")).strip().lower()
        if not DOMAIN.fullmatch(domain):
            errors.append((("domain",), "must be a Vinted domain such as vinted.it"))
        text = raw.get("search_text", "")
        text = text.strip() if isinstance(text, str) else None
        if text is None:
            errors.append((("search_text",), "must be text"))
        order = raw.get("order", "newest_first")
        if order not in ORDERS:
            errors.append((("order",), f"must be one of: {', '.join(ORDERS)}"))
        per_page = raw.get("per_page", 24)
        if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= 96:
            errors.append((("per_page",), "must be a whole number between 1 and 96"))

        prices = {}
        for key in ("price_from", "price_to"):
            if key in raw and raw[key] not in (None, ""):
                try:
                    if not isinstance(raw[key], str):
                        raise InvalidOperation
                    value = Decimal(raw[key])
                    if not value.is_finite() or value < 0:
                        raise InvalidOperation
                    prices[key] = value
                except InvalidOperation:
                    errors.append(((key,), 'must be a decimal string, e.g. "25.00"'))
        if "price_from" in prices and "price_to" in prices and prices["price_from"] > prices["price_to"]:
            errors.append((("price_to",), "must not be lower than price_from"))
        currency = raw.get("currency")
        if currency not in (None, "") and not (isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency)):
            errors.append((("currency",), "must be a three-letter code such as EUR"))

        filters: dict[str, list[str]] = {}
        raw_filters = raw.get("filters", {})
        if not isinstance(raw_filters, dict):
            errors.append((("filters",), "must map a filter code to a list of IDs"))
            raw_filters = {}
        for code, ids in raw_filters.items():
            if not isinstance(code, str) or not FILTER_CODE.fullmatch(code):
                errors.append((("filters", str(code)), "filter codes are lowercase letters, digits and '_'"))
                continue
            values = [str(i).strip() for i in ids] if isinstance(ids, list) else []
            if not values or not all(v and v.isalnum() for v in values):
                errors.append((("filters", code), "needs one or more IDs"))
                continue
            filters[code] = values

        if text == "" and not filters:
            errors.append((("search_text",), "enter a search text or at least one filter"))
        if errors:
            raise InvalidParams(errors)

        params: dict[str, Any] = {"domain": domain, "search_text": text, "order": order, "per_page": per_page}
        params.update({key: f"{value:.2f}" for key, value in prices.items()})
        if currency:
            params["currency"] = currency
        if filters:
            params["filters"] = filters
        return params

    # --- acquisition ----------------------------------------------------------------------------

    def fetch_page(self, params: dict[str, Any], cursor: Any | None) -> Page:
        domain = params["domain"]
        page_number = cursor or 1
        query = {key: params[key] for key in ("search_text", "order", "per_page", "price_from", "price_to", "currency") if params.get(key) not in (None, "")}
        query["page"] = page_number
        for code, ids in params.get("filters", {}).items():
            query[f"attribute_ids[{code}]"] = ",".join(ids)

        data = self._catalogue(domain, query)
        items, pagination = data.get("items"), data.get("pagination") or {}
        if not isinstance(items, list):
            raise PermanentMarketplaceError("vinted: response without an 'items' list (the API may have changed or blocked the request)")

        candidates, rejected = [], []
        for raw in items:
            try:
                candidates.append(normalize(raw, domain))
            except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError) as exc:
                rejected.append(Rejected(external_id=str(raw.get("id")) if isinstance(raw, dict) and raw.get("id") else None, reason=str(exc)))
        total_pages = pagination.get("total_pages")
        next_cursor = page_number + 1 if isinstance(total_pages, int) and page_number < total_pages else None
        return Page(candidates=candidates, rejected=rejected, next_cursor=next_cursor, newest_first=params["order"] == "newest_first")

    # --- filter discovery (replaces vinted_query_builder) ---------------------------------------

    def search_catalogs(self, domain: str, text: str) -> list[dict[str, str]]:
        """Categories with a readable path ("Elettronica > Videogiochi e console > Giochi"), accent/case-insensitive."""
        key = (domain, LOCALES.get(domain, "en-GB"))
        cached = self._catalog_cache.get(key)
        if cached is None or self._http.clock() - cached[0] > CATALOG_CACHE_SECONDS:
            data = self._get(domain, "/svc-navigation/navigation_catalogs", {"virtual_categories": "false"})
            if not isinstance(data.get("catalogs"), list):
                raise PermanentMarketplaceError("vinted: no category tree in the response")
            cached = (self._http.clock(), _flatten(data["catalogs"]))
            self._catalog_cache[key] = cached
        return _search(cached[1], text)

    def list_filters(self, domain: str, catalog_id: str) -> list[dict[str, Any]]:
        data = self._get(domain, "/svc-filters/filters", {"attribute_ids[catalog]": catalog_id})
        filters = data.get("filters")
        if not isinstance(filters, list):
            raise PermanentMarketplaceError("vinted: no filter list in the response")
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
            raise PermanentMarketplaceError(f"vinted: no options for filter {code}")
        return _search(_options(facets["options"]), text)

    def _get(self, domain: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._json(domain, f"https://api.{domain}{path}", params)

    def _catalogue(self, domain: str, query: dict[str, Any]) -> dict[str, Any]:
        return self._json(domain, f"https://api.{domain}/svc-catalogue/items", query)

    def _json(self, domain: str, url: str, query: dict[str, Any]) -> dict[str, Any]:
        _require_vinted_domain(domain)
        for attempt in range(2):
            if self._session_domain != domain:
                self._start_session(domain)
            try:
                data = self._http.get_json(url, params=query, headers=self._api_headers(domain))
            except PermanentMarketplaceError as exc:
                if exc.status == 401 and attempt == 0:
                    self._session_domain = None  # anonymous session expired: start a new one, once
                    continue
                raise
            if not isinstance(data, dict):
                raise PermanentMarketplaceError("vinted: response is not a JSON object")
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


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _search(entries: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    needle = _fold(text).strip()
    found = [e for e in entries if needle in _fold(e["label"])] if needle else entries
    return found[:DISCOVERY_LIMIT]


def _flatten(nodes: list[Any], path: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Category tree → flat list with readable paths (ported from vinted_query_builder.flatten_catalogs)."""
    flat = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("id") is None or not node.get("title"):
            continue
        titles = (*path, str(node["title"]))
        flat.append({"id": str(node["id"]), "label": " > ".join(titles)})
        flat.extend(_flatten(node.get("catalogs") or [], titles))
    return flat


def _options(options: list[Any], path: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Facet options, flattening groups such as sizes (ported from vinted_query_builder.flatten_options)."""
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


def normalize(raw: Any, domain: str) -> ListingCandidate:
    """One catalogue item → Listing Candidate. Shipping is not in the list: it stays unknown, never zero."""
    if not isinstance(raw, dict):
        raise ValueError("item is not a JSON object")
    price = _money(raw.get("price"))
    fee = _money(raw.get("service_fee"))
    photo = raw.get("photo") if isinstance(raw.get("photo"), dict) else {}
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    label = (raw.get("item_box") or {}).get("accessibility_label") or ""
    found = CONDITION_LABEL.search(label)
    condition_raw = found.group(1).strip() if found else None
    return ListingCandidate(
        external_id=str(raw["id"]) if raw.get("id") is not None else None,
        title=raw.get("title"),
        url=urljoin(f"https://www.{domain}/", str(raw.get("url") or "")) if raw.get("url") else None,
        price_status="priced" if price else "missing",
        price_amount=price[0] if price else None,
        currency=price[1] if price else None,
        fees_amount=fee[0] if fee and price and fee[1] == price[1] else None,
        images=[photo["full_size_url"]] if photo.get("full_size_url") else [],
        seller_external_id=str(user["id"]) if user.get("id") is not None else None,
        seller_name=user.get("login"),
        condition=CONDITIONS.get(condition_raw.casefold()) if condition_raw else None,
        condition_raw=condition_raw,
        raw_payload=raw,
    )
