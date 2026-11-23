"""Errors raised by Vinted Scanner.

`InvalidQueryError` comes from query validation; a request raises only `TransientVintedError` (worth retrying
later) or `PermanentVintedError` (retrying will not help). All three derive from `VintedError`.
"""


class VintedError(Exception):
    """Base class of every error this package raises on purpose."""


class InvalidQueryError(VintedError, ValueError):
    """Query params rejected before any request; each error is (location inside the params, message)."""

    def __init__(self, errors: list[tuple[tuple[str, ...], str]]):
        super().__init__("; ".join(f"{'.'.join(loc)}: {msg}" for loc, msg in errors))
        self.errors = errors


class TransientVintedError(VintedError):
    """Worth retrying later: network, timeout, 5xx, 429 once the client's own retries are exhausted."""


class PermanentVintedError(VintedError):
    """Retrying will not help: 4xx, unexpected response schema. `status` is the HTTP status when there is one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
