"""Tests for pid_transit's lazy public API (PEP 562).

Guards two things: that laziness does not change what the package exports, and
that importing the package stays free of the heavy optional backends.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import pid_transit


PACKAGE_INIT = Path(pid_transit.__file__)


def _typechecking_imports() -> set[str]:
    """Names imported inside the `if TYPE_CHECKING:` block of __init__.py."""
    tree = ast.parse(PACKAGE_INIT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.ImportFrom):
                    names.update(alias.asname or alias.name for alias in stmt.names)
    return names


def _import_probe(code: str) -> dict:
    """Run `code` in a fresh interpreter and return its JSON stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


class TestPublicSurface:
    def test_all_matches_lazy_map(self):
        assert set(pid_transit.__all__) == set(pid_transit._LAZY_ATTRS)

    def test_all_has_no_duplicates(self):
        assert len(pid_transit.__all__) == len(set(pid_transit.__all__))

    def test_typechecking_block_mirrors_lazy_map(self):
        # The static mirror exists for type checkers; drift would silently cost
        # users their autocomplete without failing at runtime.
        assert _typechecking_imports() == set(pid_transit._LAZY_ATTRS)

    @pytest.mark.parametrize("name", pid_transit.__all__)
    def test_every_public_name_resolves(self, name):
        assert getattr(pid_transit, name) is not None

    def test_resolved_name_is_cached_in_module_namespace(self):
        pid_transit.__getattr__("TransitDataset")
        assert "TransitDataset" in vars(pid_transit)

    def test_subpackages_remain_reachable(self):
        assert pid_transit.core.dataset.TransitDataset is pid_transit.TransitDataset

    def test_unknown_attribute_raises(self):
        with pytest.raises(AttributeError, match="no attribute 'Nonexistent'"):
            pid_transit.Nonexistent

    def test_dir_lists_public_names(self):
        listed = dir(pid_transit)
        assert set(pid_transit.__all__) <= set(listed)
        assert "core" in listed


class TestImportFootprint:
    """`import pid_transit` must not drag in optional or heavy backends."""

    def test_bare_import_loads_no_optional_backends(self):
        # Measured as what the import *adds*, not what ends up in sys.modules:
        # a sitecustomize, a .pth hook or a coverage bootstrap can have loaded
        # sqlite3 before the probe runs, and that is not this package's doing.
        loaded = _import_probe(
            "import sys, json\n"
            "before = set(sys.modules)\n"
            "import pid_transit\n"
            "added = set(sys.modules) - before\n"
            "print(json.dumps({\n"
            "  'openpyxl': any(m == 'openpyxl' or m.startswith('openpyxl.') for m in added),\n"
            "  'urllib_request': 'urllib.request' in added,\n"
            "  'pydantic': 'pydantic' in added,\n"
            "  'sqlite3': 'sqlite3' in added,\n"
            "}))"
        )
        assert loaded == {
            "openpyxl": False,
            "urllib_request": False,
            "pydantic": False,
            "sqlite3": False,
        }

    def test_openpyxl_loads_only_for_the_xlsx_path(self):
        loaded = _import_probe(
            "import sys, json\n"
            "from pid_transit import SpreadsheetImporter\n"
            "SpreadsheetImporter(format='csv')\n"
            "print(json.dumps({'openpyxl': 'openpyxl' in sys.modules}))"
        )
        assert loaded == {"openpyxl": False}

    def test_touching_a_name_loads_its_own_module_only(self):
        loaded = _import_probe(
            "import sys, json\n"
            "import pid_transit\n"
            "pid_transit.SpreadsheetImporter\n"
            "print(json.dumps({\n"
            "  'adapter': 'pid_transit.adapters.spreadsheet_importer' in sys.modules,\n"
            "  'openpyxl': 'openpyxl' in sys.modules,\n"
            "}))"
        )
        # The adapter arrives; its optional backend still waits for an xlsx call.
        assert loaded == {"adapter": True, "openpyxl": False}

    def test_openpyxl_loads_when_the_xlsx_path_asks_for_it(self):
        loaded = _import_probe(
            "import sys, json\n"
            "from pid_transit.adapters.spreadsheet_importer import _load_openpyxl\n"
            "_load_openpyxl()\n"
            "print(json.dumps({'openpyxl': 'openpyxl' in sys.modules}))"
        )
        assert loaded == {"openpyxl": True}
