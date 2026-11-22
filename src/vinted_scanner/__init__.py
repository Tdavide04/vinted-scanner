"""Vinted adapter for Personal Collection Hub: the web catalogue API with an anonymous session, read-only."""
from collections.abc import Mapping

from vinted_scanner.adapter import CONDITIONS, DEFAULT_POLICY, DOMAIN, FILTER_CODE, LOCALES, ORDERS, VintedAdapter, normalize

__version__ = "0.1.0"

__all__ = ["CONDITIONS", "DEFAULT_POLICY", "DOMAIN", "FILTER_CODE", "LOCALES", "ORDERS", "VintedAdapter", "create_adapter", "normalize"]


def create_adapter(settings: Mapping[str, str]) -> VintedAdapter:
    """Entry point factory (`pch.marketplaces`): Vinted needs no settings."""
    return VintedAdapter()
