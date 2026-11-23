"""Search queries: the typed `VintedQuery` and validation of raw params dictionaries."""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from vinted_scanner.errors import InvalidQueryError

ORDERS = ("newest_first", "relevance", "price_low_to_high", "price_high_to_low")
DOMAIN = re.compile(r"vinted\.(it|fr|es|de|at|be|nl|lu|pt|pl|cz|sk|lt|lv|ee|hu|ro|hr|fi|dk|se|ie|gr|co\.uk|com)")
FILTER_CODE = re.compile(r"[a-z][a-z0-9_]{0,40}")
DEFAULT_PER_PAGE = 24
MAX_PER_PAGE = 96


def validate_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalised params for a catalogue search, or InvalidQueryError listing every problem.

    The shape of the params (filters as a map from Vinted filter code to option ids) follows VintedScanner's
    `build_catalog_params` (GPL-3.0, see the README).
    """
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
    per_page = raw.get("per_page", DEFAULT_PER_PAGE)
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= MAX_PER_PAGE:
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
        raise InvalidQueryError(errors)

    params: dict[str, Any] = {"domain": domain, "search_text": text, "order": order, "per_page": per_page}
    params.update({key: f"{value:.2f}" for key, value in prices.items()})
    if currency:
        params["currency"] = currency
    if filters:
        params["filters"] = filters
    return params


@dataclass(frozen=True)
class VintedQuery:
    """A Vinted catalogue search. Validated on construction (raises InvalidQueryError).

    search_text: words to look for (at least this or one filter).
    domain: a Vinted domain such as "vinted.it" (default) or "vinted.fr".
    order: one of ORDERS (default "newest_first").
    per_page: 1..96 (default 24).
    price_from, price_to: decimal strings such as "25.00". currency: three-letter code such as "EUR".
    filters: Vinted filter code -> option ids, e.g. {"catalog": ["3026"], "brand": ["308275"]}.
    """

    search_text: str = ""
    domain: str = "vinted.it"
    order: str = "newest_first"
    per_page: int = DEFAULT_PER_PAGE
    price_from: str | None = None
    price_to: str | None = None
    currency: str | None = None
    filters: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_params(self.to_params())

    def to_params(self) -> dict[str, Any]:
        """The params dictionary accepted by `validate_params` and `VintedScanner.fetch_page`."""
        raw: dict[str, Any] = {"domain": self.domain, "search_text": self.search_text, "order": self.order, "per_page": self.per_page}
        for name in ("price_from", "price_to", "currency"):
            if getattr(self, name) is not None:
                raw[name] = getattr(self, name)
        if self.filters:
            raw["filters"] = {code: list(ids) for code, ids in self.filters.items()}
        return raw

    @classmethod
    def from_params(cls, raw: dict[str, Any]) -> "VintedQuery":
        """Build a query from a params dictionary (for example one stored by an application)."""
        params = validate_params(raw)
        return cls(
            search_text=params["search_text"], domain=params["domain"], order=params["order"], per_page=params["per_page"],
            price_from=params.get("price_from"), price_to=params.get("price_to"), currency=params.get("currency"),
            filters=params.get("filters", {}),
        )
