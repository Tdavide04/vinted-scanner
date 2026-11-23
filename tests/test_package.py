"""The package stands alone: no Personal Collection Hub, kit or Opportunity Hunter imports anywhere in it."""
import ast
import subprocess
import sys
from pathlib import Path

import vinted_scanner

FORBIDDEN = ("pch", "pch_marketplace_kit", "opportunity_hunter")
SOURCE = Path(vinted_scanner.__file__).parent


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_forbidden_imports_in_source():
    for path in SOURCE.rglob("*.py"):
        for name in imported_modules(path):
            assert name.split(".")[0] not in FORBIDDEN, f"{path.name} imports {name}"


def test_importing_the_package_loads_no_forbidden_module():
    code = (
        "import sys, vinted_scanner, vinted_scanner.cli, vinted_scanner.testing; "
        f"bad = [m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r}]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_version():
    assert vinted_scanner.__version__ == "0.2.0"


def test_no_pch_entry_point_and_licence_is_gpl():
    from importlib.metadata import entry_points, metadata

    assert [e for e in entry_points(group="pch.marketplaces") if e.name == "vinted"] == []
    assert metadata("vinted-scanner")["License-Expression"] == "GPL-3.0-only"
