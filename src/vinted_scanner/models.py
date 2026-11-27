"""Public result types: a normalised Vinted listing, a page of them, and items that could not be normalised."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

PriceStatus = Literal["priced", "free", "on_request", "missing"]
# item condition on a common scale; the original Vinted label stays in condition_raw
Condition = Literal["new", "like_new", "good", "fair", "poor", "not_applicable"]
# unknown shipping is never zero: it is simply not known
ShippingStatus = Literal["known", "unknown", "pickup_only"]

PRICE_STATUSES: tuple[str, ...] = get_args(PriceStatus)
CONDITIONS_SCALE: tuple[str, ...] = get_args(Condition)
SHIPPING_STATUSES: tuple[str, ...] = get_args(ShippingStatus)
CENT = Decimal("0.01")


def is_web_url(value: object) -> bool:
    """http(s) with a host: marketplace data is untrusted and may end up in links (no javascript:, data:, ...)."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value.strip())
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


@dataclass(frozen=True)
class VintedListing:
    """One Vinted catalogue item in normalised form.

    Construction enforces the minimum a listing needs (id, title, http(s) URL, coherent price and shipping);
    an item that fails becomes a `VintedRejected` on its page. Optional fields are None when Vinted does not
    give them. `raw_payload` is the item exactly as the API returned it.
    """

    external_id: str
    title: str
    url: str
    price_status: PriceStatus
    raw_payload: dict[str, Any]
    price_amount: Decimal | None = None
    currency: str | None = None
    description: str | None = None
    images: list[str] = field(default_factory=list)
    location: dict[str, Any] | None = None
    seller_external_id: str | None = None
    seller_name: str | None = None
    condition: Condition | None = None
    condition_raw: str | None = None
    published_at: datetime | None = None
    shipping_status: ShippingStatus = "unknown"
    shipping_amount: Decimal | None = None
    fees_amount: Decimal | None = None  # buyer protection fee, in the price's currency

    def __post_init__(self) -> None:
        for name in ("external_id", "title", "url"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not is_web_url(self.url):
            raise ValueError("url must be an http(s) URL")
        if not all(is_web_url(image) for image in self.images):
            raise ValueError("image URLs must be http(s) URLs")
        if self.price_status not in PRICE_STATUSES:
            raise ValueError(f"unknown price status {self.price_status!r}")
        if (self.price_status == "priced") != (self.price_amount is not None):
            raise ValueError("an amount is required exactly when the price status is 'priced'")
        if self.price_amount is not None:
            if not isinstance(self.price_amount, Decimal) or not self.price_amount.is_finite():
                raise ValueError("price amount must be a finite Decimal")
            if self.price_amount < 0 or self.price_amount != self.price_amount.quantize(CENT):
                raise ValueError("price amount must be non-negative with at most two decimals")
            if not self.currency or len(self.currency) != 3:
                raise ValueError("a three-letter currency is required with a price amount")
        if self.shipping_status not in SHIPPING_STATUSES:
            raise ValueError(f"unknown shipping status {self.shipping_status!r}")
        if (self.shipping_status == "known") != (self.shipping_amount is not None):
            raise ValueError("a shipping amount is required exactly when shipping is 'known'")
        for name in ("shipping_amount", "fees_amount"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value != value.quantize(CENT)):
                raise ValueError(f"{name} must be a non-negative Decimal with at most two decimals")
        if self.condition is not None and self.condition not in CONDITIONS_SCALE:
            raise ValueError(f"unknown condition {self.condition!r}")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        """JSON-ready form: amounts as strings (no float rounding), dates in ISO 8601."""
        data: dict[str, Any] = {
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "price_status": self.price_status,
            "price_amount": _money(self.price_amount),
            "currency": self.currency,
            "description": self.description,
            "images": list(self.images),
            "location": self.location,
            "seller_external_id": self.seller_external_id,
            "seller_name": self.seller_name,
            "condition": self.condition,
            "condition_raw": self.condition_raw,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "shipping_status": self.shipping_status,
            "shipping_amount": _money(self.shipping_amount),
            "fees_amount": _money(self.fees_amount),
        }
        if include_raw:
            data["raw_payload"] = self.raw_payload
        return data


@dataclass(frozen=True)
class VintedItemPricing:
    """Delivery and buyer-protection prices of one item, read from its page as Vinted shows them to a buyer with no
    address. With several delivery options the amount is the one Vinted shows first: the checkout can differ."""

    shipping_status: ShippingStatus
    shipping_amount: Decimal | None = None
    fees_amount: Decimal | None = None  # buyer protection
    currency: str | None = None
    multiple_shipping_options: bool = False

    def __post_init__(self) -> None:
        if self.shipping_status not in SHIPPING_STATUSES:
            raise ValueError(f"unknown shipping status {self.shipping_status!r}")
        if (self.shipping_status == "known") != (self.shipping_amount is not None):
            raise ValueError("a shipping amount is required exactly when shipping is 'known'")
        for name in ("shipping_amount", "fees_amount"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value != value.quantize(CENT)):
                raise ValueError(f"{name} must be a non-negative Decimal with at most two decimals")
        if (self.shipping_amount is not None or self.fees_amount is not None) and (not self.currency or len(self.currency) != 3):
            raise ValueError("a three-letter currency is required with an amount")


@dataclass(frozen=True)
class VintedRejected:
    """An item Vinted returned without the minimum data to become a listing."""

    external_id: str | None
    reason: str


@dataclass(frozen=True)
class VintedPage:
    """One page of search results. `next_cursor` is the number of the next page, None on the last one."""

    listings: list[VintedListing]
    rejected: list[VintedRejected] = field(default_factory=list)
    next_cursor: int | None = None
    # True only for order=newest_first: then a fully known page means everything after it is known too
    newest_first: bool = False


def _money(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None
