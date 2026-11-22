# vinted-scanner

Vinted adapter for Personal Collection Hub, on the pch-marketplace-kit contract. Decisions live in the hub repo (`docs/adr/`): ADR-0002 (ported code, GPL-3.0), ADR-0011 (read-only access) and ADR-0012 (adapters as packages).

Source code, identifiers, commit messages and `README.md` are in English. Talk to the user in Italian.

Fixtures in `src/vinted_scanner/fixtures/` are real responses with ids, sellers and photo URLs replaced by fake values; they ship with the package because the hub's tests use them through `vinted_scanner.testing`.
