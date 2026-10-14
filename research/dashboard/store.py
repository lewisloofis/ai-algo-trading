"""
dashboard/store.py — read side of the Go post-trade store.

Opens one or more SQLite files read-only and unions them into plain Python
rows.  The convention is one store file per run (engine.StorePathConvention),
so "show me several runs" means "open several files"; ``run_id`` on every row
is what keeps them apart once unioned.

Read-only on purpose: the store is the runtime's record and the dashboard has
no business writing to it.  ``file:...?mode=ro`` makes that a property of the
connection rather than a promise.
"""

from __future__ import annotations

import glob as globlib
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

LEGACY_RUN_ID = ""
"""
Matches engine.LegacyRunID: rows written before run_id existed. Rendered as an
unknown run rather than folded into whichever run shares the file.
"""

LEGACY_RUN_LABEL = "legacy (pre-run_id)"


@dataclass(frozen=True)
class RunHeader:
    """One row of the `run` table."""

    run_id: str
    started_ns: int
    ended_ns: int | None
    outcome: str
    error: str
    source: str
    pacing: str
    fill_mode: str
    sink_kind: str
    strategy_id: str
    input_spec: str
    binary_hash: str
    orders_attempted: int | None
    fills: int | None
    total_fees_idr: int | None
    round_trips: int | None
    net_pnl_bps: float | None
    store_path: str

    @property
    def crashed(self) -> bool:
        """
        A header still marked 'running' is a process that died before it could
        finish — which is exactly what writing the header at start buys.
        """
        return self.outcome == "running"


@dataclass(frozen=True)
class Attribution:
    """One completed round-trip from the `attribution` table."""

    run_id: str
    seq: int
    symbol: str
    strategy_id: str
    entry_recv_ns: int
    exit_recv_ns: int
    gross_pnl_bps: float
    net_pnl_bps: float
    entry_order_id: str
    exit_order_id: str
    hold_calendar_days: float
    as_of_date: str
    sink_kind: str
    store_path: str


@dataclass(frozen=True)
class RejectRow:
    """One row of the `order_reject` table — an intent the sink refused."""

    run_id: str
    seq: int
    recv_ns: int
    strategy_id: str
    symbol: str
    side: str
    price_idr: int
    lots: int
    order_id: str
    reason: str
    sink_kind: str
    store_path: str


@dataclass(frozen=True)
class DecisionFunnel:
    """
    Counts along decision -> order_intent -> fill for one run.

    Each drop-off is a distinct failure mode that is currently invisible: a
    strategy that never fired looks identical to one that fired and was gated,
    and to one that was admitted and never filled.
    """

    run_id: str
    strategy_id: str
    decisions: int
    signals: int
    intents: int
    fills: int
    rejects_recorded: bool = True
    """
    False when the store has no order_reject table (written before it existed):
    `rejects` is then 0 because nothing could be recorded, not because nothing
    was rejected, and the page says "n/a" rather than 0.
    """
    rejects: int = 0
    """
    Count of order_reject rows for this (run_id, strategy_id) — an intent
    the sink refused (a risk gate, a broker rejection, or the paper sink's
    ErrNoBook; see engine.RejectRow.reason for which). Defaults to 0 for a
    store written before the order_reject table existed, not just for a
    run that genuinely had none — read_store cannot tell those two apart
    from a missing table alone, and 0 is the correct rendering either way
    (nothing to show).
    """

    @property
    def signals_not_submitted(self) -> int:
        """Signals the strategy saw but did not act on."""
        return max(self.signals - self.intents, 0)

    @property
    def intents_unfilled(self) -> int:
        """
        Submitted intents with no fill: rejected by a risk gate, refused for
        want of a book (ErrNoBook), or rejected by the broker — or simply
        not yet filled. `rejects` (above) now names WHICH, when the
        order_reject table is present; this column stays as the raw
        submitted-minus-filled gap for stores that predate it.
        """
        return max(self.intents - self.fills, 0)


@dataclass
class StoreData:
    """Everything the dashboard reads out of the post-trade store(s)."""

    runs: list[RunHeader] = field(default_factory=list)
    attributions: list[Attribution] = field(default_factory=list)
    funnels: list[DecisionFunnel] = field(default_factory=list)
    rejects: list[RejectRow] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.attributions and not self.runs


def resolve_paths(patterns: Sequence[str]) -> list[str]:
    """Expand globs and directories into a sorted list of store files."""
    out: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            out.extend(globlib.glob(os.path.join(pattern, "*.db")))
        else:
            out.extend(globlib.glob(pattern))
    return sorted(set(out))


def _connect(path: str) -> sqlite3.Connection:
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _get(row: Any, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def read_store(path: str) -> StoreData:
    """Read one store file."""
    data = StoreData(paths=[path])
    conn = _connect(path)
    try:
        if _has_table(conn, "run"):
            data.runs = [_run_header(r, path) for r in conn.execute("SELECT * FROM run")]

        if _has_table(conn, "attribution"):
            attr_cols = _columns(conn, "attribution")
            has_run_id = "run_id" in attr_cols
            has_as_of = "as_of_date" in attr_cols
            # sink_kind lives on the intent, not the round-trip: a PnL row's
            # provenance is the provenance of the order that made it.
            sink_by_order = _sink_kind_by_order_id(conn)
            for r in conn.execute("SELECT * FROM attribution ORDER BY seq"):
                run_id = _get(r, "run_id", LEGACY_RUN_ID) if has_run_id else LEGACY_RUN_ID
                data.attributions.append(
                    Attribution(
                        run_id=run_id or LEGACY_RUN_ID,
                        seq=r["seq"],
                        symbol=r["symbol"],
                        strategy_id=r["strategy_id"],
                        entry_recv_ns=r["entry_recv_ns"],
                        exit_recv_ns=r["exit_recv_ns"],
                        gross_pnl_bps=r["gross_pnl_bps"],
                        net_pnl_bps=r["net_pnl_bps"],
                        entry_order_id=r["entry_order_id"],
                        exit_order_id=r["exit_order_id"],
                        hold_calendar_days=r["hold_calendar_days"],
                        as_of_date=(_get(r, "as_of_date", "") if has_as_of else ""),
                        sink_kind=sink_by_order.get(r["entry_order_id"], "unknown"),
                        store_path=path,
                    )
                )

        if _has_table(conn, "order_reject"):
            for r in conn.execute("SELECT * FROM order_reject ORDER BY seq"):
                data.rejects.append(
                    RejectRow(
                        run_id=_get(r, "run_id", LEGACY_RUN_ID) or LEGACY_RUN_ID,
                        seq=r["seq"],
                        recv_ns=r["recv_ns"],
                        strategy_id=r["strategy_id"],
                        symbol=r["symbol"],
                        side=r["side"],
                        price_idr=r["price_idr"],
                        lots=r["lots"],
                        order_id=r["order_id"],
                        reason=r["reason"],
                        sink_kind=r["sink_kind"],
                        store_path=path,
                    )
                )

        data.funnels = _read_funnels(conn)
    finally:
        conn.close()
    return data


def _run_header(r: Any, path: str) -> RunHeader:
    return RunHeader(
        run_id=r["run_id"],
        started_ns=r["started_ns"],
        ended_ns=_get(r, "ended_ns"),
        outcome=_get(r, "outcome", "running") or "running",
        error=_get(r, "error", "") or "",
        source=_get(r, "source", "") or "",
        pacing=_get(r, "pacing", "") or "",
        fill_mode=_get(r, "fill_mode", "") or "",
        sink_kind=_get(r, "sink_kind", "") or "",
        strategy_id=_get(r, "strategy_id", "") or "",
        input_spec=_get(r, "input_spec", "") or "",
        binary_hash=_get(r, "binary_hash", "") or "",
        orders_attempted=_get(r, "orders_attempted"),
        fills=_get(r, "fills"),
        total_fees_idr=_get(r, "total_fees_idr"),
        round_trips=_get(r, "round_trips"),
        net_pnl_bps=_get(r, "net_pnl_bps"),
        store_path=path,
    )


def _sink_kind_by_order_id(conn: sqlite3.Connection) -> dict[str, str]:
    if not _has_table(conn, "order_intent"):
        return {}
    return {
        r["order_id"]: r["sink_kind"]
        for r in conn.execute("SELECT order_id, sink_kind FROM order_intent")
    }


def _read_funnels(conn: sqlite3.Connection) -> list[DecisionFunnel]:
    """
    decision -> order_intent -> fill counts, grouped by (run_id, strategy_id).

    Every number here is a COUNT: the funnel is a GROUP BY over rows the
    runtime already wrote, not a model of anything.
    """
    if not _has_table(conn, "decision"):
        return []
    has_run_id = "run_id" in _columns(conn, "decision")
    run_expr = "run_id" if has_run_id else "''"

    # Aliased to `rid` rather than `run_id` throughout: in the fill join below
    # both joined tables carry a run_id, and SQLite rejects the GROUP BY as
    # ambiguous. Using one distinct alias everywhere keeps the three queries
    # shaped the same.
    decisions = {
        (r["rid"], r["strategy_id"]): (r["n"], r["signals"])
        for r in conn.execute(
            f"""SELECT {run_expr} AS rid, strategy_id, COUNT(*) AS n,
                       SUM(CASE WHEN signal_value IS NOT NULL THEN 1 ELSE 0 END) AS signals
                FROM decision GROUP BY rid, strategy_id"""
        )
    }
    intents: dict[tuple[str, str], int] = {}
    fills: dict[tuple[str, str], int] = {}
    if _has_table(conn, "order_intent"):
        intent_run = "run_id" if "run_id" in _columns(conn, "order_intent") else "''"
        intents = {
            (r["rid"], r["strategy_id"]): r["n"]
            for r in conn.execute(
                f"SELECT {intent_run} AS rid, strategy_id, COUNT(*) AS n "
                "FROM order_intent GROUP BY rid, strategy_id"
            )
        }
        if _has_table(conn, "fill"):
            # A fill is attributed through the intent that produced it: the
            # fill table has no strategy_id of its own.
            fill_run = "i.run_id" if intent_run == "run_id" else "''"
            fills = {
                (r["rid"], r["strategy_id"]): r["n"]
                for r in conn.execute(
                    f"""SELECT {fill_run} AS rid, i.strategy_id AS strategy_id, COUNT(*) AS n
                        FROM fill f JOIN order_intent i ON i.order_id = f.order_id
                        GROUP BY rid, i.strategy_id"""
                )
            }

    rejects: dict[tuple[str, str], int] = {}
    has_reject_table = _has_table(conn, "order_reject")
    if has_reject_table:
        reject_run = "run_id" if "run_id" in _columns(conn, "order_reject") else "''"
        rejects = {
            (r["rid"], r["strategy_id"]): r["n"]
            for r in conn.execute(
                f"SELECT {reject_run} AS rid, strategy_id, COUNT(*) AS n "
                "FROM order_reject GROUP BY rid, strategy_id"
            )
        }

    keys = set(decisions) | set(intents) | set(fills) | set(rejects)
    out = []
    for run_id, strategy_id in sorted(keys):
        n, signals = decisions.get((run_id, strategy_id), (0, 0))
        out.append(
            DecisionFunnel(
                run_id=run_id or LEGACY_RUN_ID,
                strategy_id=strategy_id,
                decisions=n,
                signals=signals or 0,
                intents=intents.get((run_id, strategy_id), 0),
                fills=fills.get((run_id, strategy_id), 0),
                rejects=rejects.get((run_id, strategy_id), 0),
                rejects_recorded=has_reject_table,
            )
        )
    return out


def read_stores(patterns: Sequence[str]) -> StoreData:
    """
    Read every store matching *patterns* and union them.

    A file that cannot be read is recorded in ``errors`` and skipped: one
    corrupt store must not take the whole page down, but it must also not
    vanish into a silently smaller total.
    """
    merged = StoreData()
    for path in resolve_paths(patterns):
        try:
            one = read_store(path)
        except (sqlite3.Error, OSError) as exc:
            merged.errors.append(f"{path}: {exc}")
            continue
        merged.runs.extend(one.runs)
        merged.attributions.extend(one.attributions)
        merged.funnels.extend(one.funnels)
        merged.rejects.extend(one.rejects)
        merged.paths.extend(one.paths)
    return merged


def run_label(run_id: str) -> str:
    """Human label for a run id, naming the legacy bucket explicitly."""
    return LEGACY_RUN_LABEL if run_id == LEGACY_RUN_ID else run_id


def strategy_ids(data: StoreData) -> list[str]:
    """Every strategy id appearing anywhere in the data."""
    ids = {a.strategy_id for a in data.attributions}
    ids |= {f.strategy_id for f in data.funnels}
    ids |= {r.strategy_id for r in data.runs if r.strategy_id}
    return sorted(ids)
