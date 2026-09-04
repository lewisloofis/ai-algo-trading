"""Guards against undeclared third-party dependencies.

Context: in one session, `pyarrow`, `certifi`, `pytz`, and `boto3` were each
imported by code in this tree without being declared in
`research/requirements.txt`, and every failure looked like something else
entirely -- a parquet reader that "should" work, a TLS chain that looked
like a MITM proxy, a test suite with a mystery `ModuleNotFoundError`, and a
health check that just seemed to hang. `pytz` in particular was imported
lazily *inside a function body* in `strategy_fit/fixtures_generator.py`,
which is exactly the shape of import a naive (regex or top-level-only) scan
would miss.

This module parses every `.py` file under `research/`, `scripts/`, and
`calendar/` with `ast`, walks the *entire* tree (not just module level) to
find every `import`/`from ... import` statement -- including ones nested in
functions, methods, `try` blocks, and `if TYPE_CHECKING:` guards -- and
cross-checks each third-party root module against `research/requirements.txt`.
"""
from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_REQUIREMENTS_PATH = Path(__file__).parent.parent / "requirements.txt"

# Trees scanned for imports. scripts/ and calendar/ live outside the
# research/ package but run against the same venv, so their third-party
# imports must be declared in the same requirements.txt.
_SCAN_ROOTS = ("research", "scripts", "calendar")

# Directory components that are never source we own.
_EXCLUDE_DIR_NAMES = {".venv", "__pycache__", "build", "dist"}


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDE_DIR_NAMES or part.endswith(".egg-info") for part in path.parts)


def _iter_python_files(
    repo_root: Path = _REPO_ROOT, scan_roots: Iterable[str] = _SCAN_ROOTS
) -> Iterator[Path]:
    """Yield every `.py` file under the scanned trees, skipping vendored/build dirs."""
    for root_name in scan_roots:
        base = repo_root / root_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if not _is_excluded(path):
                yield path


@dataclass(frozen=True)
class ImportRef:
    """One `import`/`from ... import` statement, wherever it appears in the AST."""

    root_module: str  # top-level name, e.g. "pytz" from "import pytz.tzinfo"
    file: Path
    lineno: int


def _extract_imports(path: Path) -> list[ImportRef]:
    """Return every absolute import in `path`, no matter how deeply nested.

    Uses `ast.walk`, which visits every node in the tree regardless of
    nesting depth -- so an import inside a function, a method, a `try`
    block, or an `if TYPE_CHECKING:` guard is found exactly the same way as
    a module-level one. Relative imports (`from . import x`, `from .foo
    import y`) are skipped: by definition they resolve to a module in the
    same first-party package, never a third-party distribution.
    """
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(ImportRef(alias.name.split(".")[0], path, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import -- first-party by construction
            if node.module is None:
                continue
            refs.append(ImportRef(node.module.split(".")[0], path, node.lineno))
    return refs


def _stdlib_roots() -> frozenset[str]:
    return sys.stdlib_module_names


def _first_party_roots(
    repo_root: Path = _REPO_ROOT, scan_roots: Iterable[str] = _SCAN_ROOTS
) -> set[str]:
    """Names importable as a bare root module from inside this repo.

    Derived from what's actually on disk rather than a hardcoded list, so
    it doesn't rot as packages are added, renamed, or removed:

    - a directory directly under a scan root that is itself a package
      (has `__init__.py`) -- e.g. `research/lib` -> "lib", importable as
      `lib.xxx` because tests run with `research/` as the cwd.
    - a `.py` file directly under a scan root with no wrapping package --
      e.g. `scripts/s3_readonly.py` -> "s3_readonly". Scripts under
      `scripts/` and `calendar/` import same-directory siblings by bare
      filename (they aren't packages), so those need to be recognized as
      first-party too, not just formal packages.
    """
    roots: set[str] = set()
    for root_name in scan_roots:
        base = repo_root / root_name
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "__init__.py").is_file():
                roots.add(child.name)
            elif child.is_file() and child.suffix == ".py":
                roots.add(child.stem)
    return roots


def _is_third_party(
    root_module: str, stdlib_roots: frozenset[str], first_party_roots: set[str]
) -> bool:
    return root_module not in stdlib_roots and root_module not in first_party_roots


def _normalize(name: str) -> str:
    """PEP 503 normalization: lowercase, collapse `-`/`_`/`.` runs to a single `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


# Import name -> PyPI distribution name, for the (known) cases where they
# differ. Anything imported that isn't stdlib, isn't first-party, and isn't
# in this map is looked up in requirements.txt under its own name; if that
# also doesn't match, it's a real, reportable gap. Extend this table only
# when a genuine import/distribution name mismatch is found -- never as a
# way to make an actually-missing dependency stop being flagged.
_IMPORT_TO_DISTRIBUTION = {
    "yaml": "PyYAML",
    "dateutil": "python-dateutil",
    "sklearn": "scikit-learn",
}


def _distribution_name(import_root: str) -> str:
    return _IMPORT_TO_DISTRIBUTION.get(import_root, import_root)


def _requirement_names(path: Path) -> list[str]:
    """Raw distribution names pinned in a requirements.txt, in file order.

    Strips comments, blank lines, `-r`/`-e`/`--` option lines, extras,
    version specifiers, and environment markers -- e.g.
    `foo[extra]==1.2.3; python_version<'4'` -> "foo".
    """
    names: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-r", "-e", "--")):
            continue
        name = re.split(r"[\[<>=!~; ]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def _parse_requirements(path: Path) -> set[str]:
    """Normalized distribution names pinned in requirements.txt."""
    return {_normalize(name) for name in _requirement_names(path)}


def _collect_third_party_imports(
    repo_root: Path = _REPO_ROOT, scan_roots: Iterable[str] = _SCAN_ROOTS
) -> list[ImportRef]:
    stdlib_roots = _stdlib_roots()
    first_party_roots = _first_party_roots(repo_root, scan_roots)
    return [
        ref
        for path in _iter_python_files(repo_root, scan_roots)
        for ref in _extract_imports(path)
        if _is_third_party(ref.root_module, stdlib_roots, first_party_roots)
    ]


# ---------------------------------------------------------------------------
# Nested-import detection (the exact shape of bug that let `pytz` through)
# ---------------------------------------------------------------------------

_NESTED_IMPORT_FIXTURE = '''\
"""Synthetic module exercising every nesting shape a real import can hide in."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fixture_typecheck_pkg


def top_level_function():
    import fixture_function_pkg


class SomeClass:
    def some_method(self):
        import fixture_method_pkg


def uses_try():
    try:
        import fixture_try_pkg
    except ImportError:
        fixture_try_pkg = None
'''


def test_nested_import_detection_covers_functions_methods_try_and_type_checking(
    tmp_path: Path,
) -> None:
    # Arrange
    fixture_path = tmp_path / "nested_import_fixture.py"
    fixture_path.write_text(_NESTED_IMPORT_FIXTURE)

    # Act
    found_roots = {ref.root_module for ref in _extract_imports(fixture_path)}

    # Assert
    assert "fixture_function_pkg" in found_roots, "import inside a function body was missed"
    assert "fixture_method_pkg" in found_roots, "import inside a method body was missed"
    assert "fixture_try_pkg" in found_roots, "import inside a try block was missed"
    assert "fixture_typecheck_pkg" in found_roots, "import inside an `if TYPE_CHECKING:` guard was missed"


def test_first_party_roots_from_package_dirs_and_sibling_scripts(tmp_path: Path) -> None:
    # Arrange: a package dir, a bare sibling script, and a nested non-package
    # dir (whose .py files must NOT be treated as bare-importable roots).
    (tmp_path / "research" / "mypkg").mkdir(parents=True)
    (tmp_path / "research" / "mypkg" / "__init__.py").touch()
    (tmp_path / "research" / "notapkg").mkdir(parents=True)
    (tmp_path / "research" / "notapkg" / "helper.py").touch()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "myscript.py").touch()
    (tmp_path / "calendar").mkdir()
    (tmp_path / "calendar" / "check_deadlines.py").touch()

    # Act
    roots = _first_party_roots(repo_root=tmp_path, scan_roots=("research", "scripts", "calendar"))

    # Assert
    assert roots == {"mypkg", "myscript", "check_deadlines"}


def test_normalize_collapses_separators_and_lowercases() -> None:
    assert _normalize("PyYAML") == "pyyaml"
    assert _normalize("python_dateutil") == "python-dateutil"
    assert _normalize("Foo.Bar--Baz") == "foo-bar-baz"


def test_requirement_names_strips_extras_markers_and_comments(tmp_path: Path) -> None:
    # Arrange
    req_path = tmp_path / "requirements.txt"
    req_path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "plain-package",
                "foo[extra]==1.2.3; python_version<'4'",
                "-e ./local-pkg",
                "Bar>=2.0  # inline comment",
            ]
        )
    )

    # Act
    names = _requirement_names(req_path)

    # Assert
    assert names == ["plain-package", "foo", "Bar"]


# ---------------------------------------------------------------------------
# The real checks
# ---------------------------------------------------------------------------


def test_all_third_party_imports_declared_in_requirements() -> None:
    """Every third-party import anywhere in the tree must be pinned.

    Failure means: some module can be imported today only because it
    happens to already be installed in this venv (as a transitive
    dependency, or because someone `pip install`ed it by hand) -- not
    because `pip install -r requirements.txt` actually provides it. That is
    precisely how `pyarrow`/`certifi`/`pytz`/`boto3` went missing before.
    """
    third_party_imports = _collect_third_party_imports()
    declared = _parse_requirements(_REQUIREMENTS_PATH)

    missing: dict[str, list[ImportRef]] = {}
    for ref in third_party_imports:
        dist = _distribution_name(ref.root_module)
        if _normalize(dist) not in declared:
            missing.setdefault(dist, []).append(ref)

    if not missing:
        return

    req_display = _REQUIREMENTS_PATH.relative_to(_REPO_ROOT)
    lines = [f"{len(missing)} third-party module(s) imported but not declared in {req_display}:"]
    for dist in sorted(missing):
        refs = sorted(missing[dist], key=lambda r: (str(r.file), r.lineno))
        import_name = refs[0].root_module
        lines.append(f"\n  '{dist}' (import name: '{import_name}') -- add it to {req_display}. Imported at:")
        for ref in refs[:5]:
            lines.append(f"    {ref.file.relative_to(_REPO_ROOT)}:{ref.lineno}")
        if len(refs) > 5:
            lines.append(f"    ... and {len(refs) - 5} more location(s)")
    pytest.fail("\n".join(lines), pytrace=False)


def test_requirements_pins_are_all_imported_somewhere() -> None:
    """Informational only: flags requirements.txt entries nothing imports.

    Deliberately not a hard failure -- `pytest` and other build-only
    tooling can legitimately sit in requirements.txt without being
    `import`ed by anything this scan walks.
    """
    third_party_imports = _collect_third_party_imports()
    imported_distributions = {_normalize(_distribution_name(ref.root_module)) for ref in third_party_imports}

    unused = [
        name
        for name in _requirement_names(_REQUIREMENTS_PATH)
        if _normalize(name) not in imported_distributions
    ]

    if unused:
        req_display = _REQUIREMENTS_PATH.relative_to(_REPO_ROOT)
        print(
            f"\nNOTE: {len(unused)} requirement(s) pinned in {req_display} but not imported "
            f"anywhere under {', '.join(_SCAN_ROOTS)}: {', '.join(sorted(unused))} "
            "(informational only -- may be build/test-only tooling)."
        )
