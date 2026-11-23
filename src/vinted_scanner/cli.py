"""Command line: `vinted-scanner search ...` (and the discovery commands) print one JSON document on stdout; logs go to stderr.

Exit codes: 0 success, 1 permanent error (invalid arguments, HTTP 4xx, unexpected response),
2 transient error (network, timeout, 5xx, 429 after the client's own retries). Nothing is stored and
nothing is sent anywhere else.
"""
import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from vinted_scanner import __version__
from vinted_scanner.errors import InvalidQueryError, PermanentVintedError, TransientVintedError
from vinted_scanner.query import DEFAULT_PER_PAGE, MAX_PER_PAGE, ORDERS, VintedQuery
from vinted_scanner.scanner import VintedScanner

EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT = 0, 1, 2
MAX_PAGES = 10

logger = logging.getLogger("vinted_scanner.cli")


class _Parser(argparse.ArgumentParser):
    """argparse exits with 2 on usage errors; here 2 means "try again later", so usage errors exit with 1."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_PERMANENT, f"{self.prog}: error: {message}\n")


def _filter(value: str) -> tuple[str, list[str]]:
    code, sep, ids = value.partition("=")
    if not sep or not code or not ids:
        raise argparse.ArgumentTypeError("CODE=ID[,ID...], e.g. brand=308275 or catalog=3026")
    return code, [i for i in ids.split(",") if i]


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="vinted-scanner", description="Read-only search of the Vinted catalogue.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING", help="stderr log level (default WARNING)")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    search = commands.add_parser(
        "search",
        help="search listings and print them as JSON",
        description="Search the Vinted catalogue and print the listings as one JSON document on stdout.",
        epilog="Exit codes: 0 ok, 1 permanent error, 2 transient error. Requests are at least 3 seconds apart.",
    )
    search.add_argument("search_text", nargs="?", default="", help="words to look for (optional when --filter is given)")
    search.add_argument("--domain", default="vinted.it", help="Vinted domain (default vinted.it)")
    search.add_argument("--order", choices=ORDERS, default="newest_first", help="sort order (default newest_first)")
    search.add_argument("--filter", dest="filters", type=_filter, action="append", default=[], metavar="CODE=ID[,ID]",
                        help="Vinted filter, repeatable: catalog=3026, brand=308275, status=2,3")
    search.add_argument("--price-from", metavar="AMOUNT", help='minimum price, e.g. "10.00"')
    search.add_argument("--price-to", metavar="AMOUNT", help='maximum price, e.g. "50.00"')
    search.add_argument("--currency", metavar="CODE", help="three-letter currency, e.g. EUR")
    search.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE, metavar=f"1..{MAX_PER_PAGE}", help=f"results per page (default {DEFAULT_PER_PAGE})")
    search.add_argument("--pages", type=int, default=1, metavar=f"1..{MAX_PAGES}", help="pages to read (default 1)")
    search.add_argument("--raw", action="store_true", help="include each item's raw API payload")

    catalogs = commands.add_parser("catalogs", help="find category ids by name", description="Categories whose path contains TEXT.")
    catalogs.add_argument("text", nargs="?", default="")
    catalogs.add_argument("--domain", default="vinted.it")

    filters = commands.add_parser("filters", help="list the filters of a category", description="Filters available in a category.")
    filters.add_argument("catalog_id")
    filters.add_argument("--domain", default="vinted.it")

    options = commands.add_parser("options", help="find the option ids of a filter", description="Options of a filter, optionally matching TEXT.")
    options.add_argument("code", help="filter code from the filters command, e.g. brand")
    options.add_argument("text", nargs="?", default="")
    options.add_argument("--catalog", default="", help="category id the options apply to")
    options.add_argument("--searchable", action="store_true", help="ask Vinted to search the options (filters marked searchable, e.g. brand)")
    options.add_argument("--domain", default="vinted.it")
    return parser


def main(argv: Sequence[str] | None = None, scanner_factory: Callable[[], VintedScanner] = VintedScanner) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=args.log_level, format="%(levelname)s %(name)s: %(message)s", force=True)
    try:
        if args.command == "search":
            if not 1 <= args.pages <= MAX_PAGES:
                return _fail(EXIT_PERMANENT, f"--pages must be between 1 and {MAX_PAGES}")
            filters: dict[str, list[str]] = {}
            for code, ids in args.filters:
                filters.setdefault(code, []).extend(ids)
            query = VintedQuery(
                search_text=args.search_text, domain=args.domain, order=args.order, per_page=args.per_page,
                price_from=args.price_from, price_to=args.price_to, currency=args.currency, filters=filters,
            )
            document: Any = _search(scanner_factory(), query, args.pages, args.raw)
        elif args.command == "catalogs":
            document = scanner_factory().search_catalogs(_domain(args.domain), args.text)
        elif args.command == "filters":
            document = scanner_factory().list_filters(_domain(args.domain), args.catalog_id)
        else:
            document = scanner_factory().filter_options(_domain(args.domain), args.code, args.catalog, args.text, searchable=args.searchable)
    except InvalidQueryError as exc:
        return _fail(EXIT_PERMANENT, f"invalid search: {exc}")
    except TransientVintedError as exc:
        return _fail(EXIT_TRANSIENT, str(exc))
    except PermanentVintedError as exc:
        return _fail(EXIT_PERMANENT, str(exc))
    json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK


def _domain(value: str) -> str:
    domain = value.strip().lower()
    try:
        VintedQuery(search_text="x", domain=domain)
    except InvalidQueryError:
        raise InvalidQueryError([(("domain",), "must be a Vinted domain such as vinted.it")]) from None
    return domain


def _search(scanner: VintedScanner, query: VintedQuery, max_pages: int, include_raw: bool) -> dict[str, Any]:
    listings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_read, next_cursor = 0, None
    for page in scanner.pages(query, max_pages=max_pages):
        pages_read += 1
        next_cursor = page.next_cursor
        for listing in page.listings:
            if listing.external_id not in seen:
                seen.add(listing.external_id)
                listings.append(listing.to_dict(include_raw=include_raw))
        rejected += [{"external_id": r.external_id, "reason": r.reason} for r in page.rejected]
    return {"query": query.to_params(), "pages_read": pages_read, "next_cursor": next_cursor, "listings": listings, "rejected": rejected}


def _fail(code: int, message: str) -> int:
    logger.error(message)
    return code
