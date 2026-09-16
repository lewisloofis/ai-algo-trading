"""
strategy_fit/portfolio_json.py — machine-readable sidecar for the strategy-fit grid.

``emit_strategy_portfolio`` renders ``CellResult`` straight to markdown.  That
makes the grid readable once and diffable never: the 271 KB coverage table in
``research/out_live/strategy_portfolio.md`` cannot be filtered, joined, or
compared against the previous run.  This module writes the SAME ``cells``
sequence to ``strategy_portfolio.json`` so a run becomes an artifact something
else can read (plan-19 §3, Stage 1).

What is and is not in the sidecar
---------------------------------
**In:** every ``CellResult`` field, verbatim, plus *run provenance* — the values
this run actually used (impact coefficients, sleeve sizing, capital model,
universe, holds, git SHA).

**Out:** thresholds and verdicts' defining constants.  ``DECAY_THRESHOLD_SHARPE``,
``MIN_OOS_EFF_N`` and friends are deliberately NOT copied here: a reader must
import them from the module that owns them (plan-19 §2 D2), or the sidecar
becomes a second, stale answer to "what is the threshold".

The distinction is between *what this run did* (provenance — belongs here,
because it can differ from today's module constant, which is exactly the
ADR-0017 failure ``impact_priced_position_notional_idr`` exists to prevent) and
*what the rule is* (a threshold — import it).

Non-finite floats
-----------------
``NaN`` and ``±Infinity`` are not valid JSON.  They are written as ``null``.
This loses nothing the markdown report distinguishes — ``report._fmt_float``
already renders ``NaN`` and ``None`` identically as ``—`` — and it keeps the
sidecar strict, parseable JSON for every consumer.  ``cells_from_sidecar``
therefore round-trips a ``NaN`` field back as ``None``; see
``normalize_nonfinite``.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import json
import math
import os
import subprocess
from typing import Any, Mapping, Sequence

from strategy_fit.capital_model import ACTIVE_CAPITAL_MODEL
from strategy_fit.harness import CellResult
from strategy_fit.impact import POSITION_NOTIONAL_IDR, SQRT_IMPACT_COEFF
from strategy_fit.sleeve_sizing import ACTIVE_SLEEVE_SIZING

SCHEMA_VERSION: int = 1
"""
Bumped whenever the sidecar's *shape* changes in a way a reader must notice.
Adding a ``CellResult`` field does NOT bump it — cells are serialised
field-for-field by ``dataclasses.asdict``, so new fields appear automatically
and the round-trip test (test_portfolio_json.py) fails if one is dropped.
"""

SIDECAR_FILENAME: str = "strategy_portfolio.json"

# Coefficient-source labels for the impact provenance block.
COEFF_SOURCE_MEASURED: str = "measured"
COEFF_SOURCE_GLOBAL_DEFAULT: str = "global_default"
COEFF_SOURCE_MIXED: str = "mixed"
COEFF_SOURCE_UNPRICED: str = "unpriced"


# ---------------------------------------------------------------------------
# Non-finite handling
# ---------------------------------------------------------------------------

def normalize_nonfinite(value: Any) -> Any:
    """
    Replace non-finite floats with None, recursively through dicts and lists.

    Applied on write (so the file is strict JSON) and on the expected side of
    the round-trip test (so a NaN in, None out is an assertion, not a
    surprise).  See the module docstring.
    """
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, Mapping):
        return {k: normalize_nonfinite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_nonfinite(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def git_provenance(repo_root: str | None = None) -> dict[str, Any]:
    """
    The git SHA of the tree that produced this run, and whether it was dirty.

    Never raises: a run outside a checkout, or without git installed, records
    ``{"sha": None, "error": ...}`` rather than failing the report.  A dirty
    tree is recorded, not warned about — "which code produced this number" is
    unanswerable if the tree had uncommitted edits, and the sidecar should say
    so instead of implying the SHA is the whole story.

    Cached per *repo_root*: three subprocess calls per emitted report is a
    measurable cost in a test suite that emits hundreds, and the answer cannot
    meaningfully change inside one process.
    """
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                args,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            raise _GitUnavailable(str(exc)) from exc
        if out.returncode != 0:
            raise _GitUnavailable((out.stderr or "").strip() or f"exit {out.returncode}")
        return out.stdout.strip()

    try:
        sha = _run(["git", "rev-parse", "HEAD"])
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        porcelain = _run(["git", "status", "--porcelain"])
    except _GitUnavailable as exc:
        return {"sha": None, "branch": None, "dirty": None, "error": str(exc)}
    return {
        "sha": sha,
        "branch": branch,
        "dirty": bool(porcelain),
        "error": None,
    }


class _GitUnavailable(RuntimeError):
    """Internal: git could not answer. Never escapes git_provenance."""


def impact_provenance(cells: Sequence[CellResult]) -> dict[str, Any]:
    """
    How this run priced market impact, derived from the cells themselves.

    ``coeff_source`` is read off ``impact_priced_coeff`` rather than off a
    module constant, because the two can disagree: a calibrated run resolves a
    per-name coefficient while ``SQRT_IMPACT_COEFF`` keeps its flat value.
    Labels:

    ``unpriced``       — no cell had impact priced at all (impact_priced=False
                         everywhere).  NOT the same as "impact was zero".
    ``global_default`` — every priced cell used SQRT_IMPACT_COEFF.
    ``measured``       — no priced cell used SQRT_IMPACT_COEFF; all came from
                         the per-name/bucket calibration.
    ``mixed``          — both, which is the case a reader must not average over.
    """
    priced = [c for c in cells if c.impact_priced]
    coeffs = sorted({c.impact_priced_coeff for c in priced if c.impact_priced_coeff is not None})
    notionals = sorted({
        c.impact_priced_position_notional_idr
        for c in priced
        if c.impact_priced_position_notional_idr is not None
    })

    if not priced:
        source = COEFF_SOURCE_UNPRICED
    else:
        uses_default = any(c.impact_priced_coeff == SQRT_IMPACT_COEFF for c in priced)
        uses_other = any(
            c.impact_priced_coeff is not None and c.impact_priced_coeff != SQRT_IMPACT_COEFF
            for c in priced
        )
        if uses_default and uses_other:
            source = COEFF_SOURCE_MIXED
        elif uses_other:
            source = COEFF_SOURCE_MEASURED
        else:
            source = COEFF_SOURCE_GLOBAL_DEFAULT

    return {
        "coeff_source": source,
        "priced_cells": len(priced),
        "unpriced_cells": len(cells) - len(priced),
        "distinct_coeffs": normalize_nonfinite(coeffs),
        "distinct_priced_notional_idr": normalize_nonfinite(notionals),
        "module_default_coeff": SQRT_IMPACT_COEFF,
        "module_position_notional_idr": POSITION_NOTIONAL_IDR,
    }


def run_provenance(cells: Sequence[CellResult], repo_root: str | None = None) -> dict[str, Any]:
    """Everything about the run that is not a cell: universe, holds, sizing, git."""
    symbols = sorted({c.symbol for c in cells})
    families = sorted({c.family_name for c in cells})
    holds = sorted({c.horizon_trading_days for c in cells})
    shapes = sorted({c.shape for c in cells})
    return {
        "git": git_provenance(repo_root),
        "universe": {"symbols": symbols, "size": len(symbols)},
        "families": families,
        "shapes": shapes,
        "holds_trading_days": holds,
        "impact": impact_provenance(cells),
        "capital_model": dataclasses.asdict(ACTIVE_CAPITAL_MODEL),
        "sleeve_sizing": {
            **dataclasses.asdict(ACTIVE_SLEEVE_SIZING),
            "per_name_notional_idr": ACTIVE_SLEEVE_SIZING.per_name_notional_idr,
        },
    }


# ---------------------------------------------------------------------------
# Build / emit / load
# ---------------------------------------------------------------------------

def build_sidecar(
    cells: Sequence[CellResult],
    generated_at: str,
    is_synthetic: bool,
    verdict: str,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Assemble the sidecar document. Pure: no I/O apart from git provenance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "is_synthetic": is_synthetic,
        "verdict": verdict,
        "cell_count": len(cells),
        "provenance": run_provenance(cells, repo_root=repo_root),
        "cells": [normalize_nonfinite(dataclasses.asdict(c)) for c in cells],
    }


def emit_portfolio_json(
    cells: Sequence[CellResult],
    outdir: str,
    generated_at: str,
    is_synthetic: bool,
    verdict: str,
    repo_root: str | None = None,
) -> str:
    """
    Write ``strategy_portfolio.json`` to *outdir*; return the path.

    Called by ``report.emit_strategy_portfolio`` with the same arguments the
    markdown gets, so the two files can never describe different runs.
    """
    os.makedirs(outdir, exist_ok=True)
    doc = build_sidecar(
        cells=cells,
        generated_at=generated_at,
        is_synthetic=is_synthetic,
        verdict=verdict,
        repo_root=repo_root,
    )
    path = os.path.join(outdir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, allow_nan=False, sort_keys=False)
        f.write("\n")
    return path


def load_sidecar(path: str) -> dict[str, Any]:
    """Read a sidecar written by emit_portfolio_json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cells_from_sidecar(doc: Mapping[str, Any]) -> list[CellResult]:
    """
    Rebuild ``CellResult`` objects from a sidecar document.

    Raises ``ValueError`` when the sidecar carries a field ``CellResult`` no
    longer has, or is missing one it now requires — a loud failure is the
    point: a silently-dropped field is exactly the drift plan-19 §8's
    field-drift test exists to catch.
    """
    known = {f.name for f in dataclasses.fields(CellResult)}
    out: list[CellResult] = []
    for i, raw in enumerate(doc.get("cells", [])):
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"cell {i}: sidecar has field(s) CellResult does not define: "
                f"{sorted(unknown)} — the sidecar was written by a different "
                f"version of strategy_fit.harness"
            )
        out.append(CellResult(**raw))
    return out


def sidecar_generated_at_utc() -> str:
    """The canonical Z-suffixed UTC stamp used across this repo's artifacts."""
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return stamp[:-6] + "Z" if stamp.endswith("+00:00") else stamp
