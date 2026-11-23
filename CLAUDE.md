# vinted-scanner

Standalone Vinted scanner: a Python library and a `vinted-scanner` command line tool, with no dependency on Personal Collection Hub, pch-marketplace-kit or Opportunity Hunter (a test enforces it). Decisions live in the hub repo (`docs/adr/`): ADR-0002 (ported code, GPL-3.0), ADR-0011 (read-only access) and ADR-0013 (four-repository architecture).

Source code, identifiers, commit messages and `README.md` are in English. Talk to the user in Italian.

The package stays GPL-3.0-only: parts are ported from VintedScanner by Andrea Draghetti (GPL-3.0-only), marked in their docstrings. The user chose (2026-11-23) to keep GPL rather than rewrite for MIT; never declare it MIT.

Acquisition behaviour (endpoints, params, headers, User-Agent, session, rate limit, backoff, Retry-After, paging, normalisation) is frozen: changing it is a separate decision, not part of a refactor.

Fixtures in `src/vinted_scanner/fixtures/` are real responses with ids, sellers and photo URLs replaced by fake values; they ship with the package because applications' tests use them through `vinted_scanner.testing`.
