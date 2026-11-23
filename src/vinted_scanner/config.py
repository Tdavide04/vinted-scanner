"""Scanner configuration: the HTTP pacing and retry policy."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VintedScannerConfig:
    """How the scanner talks to Vinted. The defaults are the policy the Vinted adapter has always used.

    min_interval_s: minimum gap between two requests (rate limit).
    timeout_s: timeout of each request.
    max_retries: retries after the first attempt, for network errors, 5xx and 429.
    backoff_base_s, backoff_cap_s: exponential backoff (base * 2**attempt, capped, with jitter).
    max_retry_after_s: a longer `Retry-After` fails at once as transient instead of waiting.
    """

    min_interval_s: float = 3.0
    timeout_s: float = 30.0
    max_retries: int = 3
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 60.0
    max_retry_after_s: float = 120.0

    def __post_init__(self) -> None:
        for name in ("min_interval_s", "backoff_base_s", "backoff_cap_s", "max_retry_after_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")


DEFAULT_CONFIG = VintedScannerConfig()
