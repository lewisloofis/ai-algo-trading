"""
strategy_fit/target_emitter.py — daily target emitter (plan 18 §8 step 2a).

Turns a grid cell's research signal into the ``target_portfolio.json`` that
``strategy-run --strategy rebalance`` consumes (contract: plan 18 §3.1, Go
loader: strategy/portfolio/target.go). Run once pre-open on day D.

    python -m strategy_fit.target_emitter emit --strategy <id> \
        --as-of 2026-10-15 --out /path/target.json [--source local|live] \
        [--rebalance-at 09:30] [--allow-retired]
    python -m strategy_fit.target_emitter replay --strategy <id>

Strategy-agnostic: every strategy is one entry in strategy_fit/emitter_specs.py
(id -> signal fn, symbols, hold, lots, status). ``make emit-target STRATEGY=<id>``
wraps this and prints the matching ``live-session`` command (never runs it):
a ``--sink paper --source live`` command for a ``paper`` spec, a live-sink command
only for a ``candidate`` spec. ``make paper-day`` (strategy_fit/paper_day.py) emits
and then runs the paper command.
Exit codes: 0 ok, 2 data/arg error, 3 no token, 4 not a trading day (nothing
written), 5 retired spec without --allow-retired, 6 rebalance time outside window.

The signal is NOT reimplemented: ``spec.signal_fn`` is the very function
object the grid scored (e.g. families_single_name.daily_momentum_signal), the
de-overlap is harness.deoverlap_daily and the long-only filter is
long_only.long_only_mask.

Timing and de-overlap (D1 and D2 are FIXED in the harness; this mirrors it)
------------------------------------------------------------------------
The research cell no longer transacts at the signal close: a signal on close s
is entered on the next session E = s+1 at close_E and exited at
close_{E+hold} (strategy_fit/entry_convention.py, default next_close). The
emitter acts on a signal on the close of D-1 at D's rebalance, using only bars
strictly before D.

De-overlap is SEQUENTIAL (harness.deoverlap_sequential) on long-only signals:
scan in order; when flat and a long signal fires at s, enter s+1, exit
s+1+hold, resume scanning at the exit day. Unlike the pre-fix stride-over-all-
rows grid, no signal is dropped for being "off grid". The history window is
still anchored at HISTORY_START so the emitter trades exactly the entries the
scored cell did (the sequential chain depends on where the scan starts).

Hold and exit
-------------
An entry decided on s is entered on E = next trading day after s, held for
``hold`` sessions (E .. E+hold-1) and its target weight is 0 from
exit day E+hold (trading-day arithmetic on calendar/idx_holidays.json).
Entries never overlap, so a second entry never extends the position.

Weight -> exactly N lots (spec.lots, default 1)
-----------------------------------------------
The Go rebalance strategy sizes floor(weight*base / (snapTick(price)*100)).
One name: weight 1.0 (or 0.0), base = ceil(1.3 * last_close * 100 * lots).
Several names: each needs need_i = ceil(1.3 * close_i * 100 * lots); base is
sum(need_i) plus 1e-6 slack and each active weight is need_i*(1+1e-7)/base
rounded up, so weights sum below 1.0 (schema rule) and every active name floors
to exactly ``lots`` lots across the one-lot price band. See ``size_targets``.

Rebalance window
----------------
as_of / complete_by are the window (default session 1, 09:00-11:30 WIB) and the
bot's --rebalance-at must fall inside [start, end); the bot's own default 15:00
does not, so it is rejected (exit 6) and the printed command always carries the
in-window time. On a non-trading day nothing is written (exit 4).

Output
------
The Go loader rejects unknown fields (target.go Parse), so the explanatory
fields live in a sidecar ``<out stem>.signal.json``. Both files are written
atomically (temp file + os.replace) and contain no wall-clock values, so the
same inputs give byte-identical files.

The live path needs ``SB_ACCESS_TOKEN`` in the environment and never reads a
token file. It is optional; the default source is the local daily parquet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import pandas as pd

from lib.market_calendar import CALENDAR, MarketCalendar
from strategy_fit.emitter_specs import SPECS, EmitterSpec, SpecError
from strategy_fit.harness import deoverlap_sequential
from strategy_fit.long_only import long_only_mask
from strategy_fit.paper_stores import paper_net_lots
from strategy_fit.run_strategy_fit import HISTORY_START

SCHEMA_VERSION = 1
SIDECAR_SUFFIX = ".signal.json"
LOT_SHARES = 100
# base = ceil(headroom * close * 100): one lot for any fill price in
# (0.65, 1.30] x close. IDX auto-reject is +/-35% below Rp 200, a 2.08x range
# that no single base can cover; the band below is what is guaranteed.
ONE_LOT_HEADROOM = 1.3
ONE_LOT_PRICE_BAND = (0.70, 1.30)
WIB = timezone(timedelta(hours=7))
SIZING_SLACK = 1e-6          # base_notional headroom so weights sum strictly below 1.0
WEIGHT_MARGIN = 1e-7         # each weight overshoots its need so int() cannot truncate a lot away
WEIGHT_DECIMALS = 1e9
MAX_PARTICIPATION_RATE = 0.05
BOARD = "RG"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"
TOKEN_ENV = "SB_ACCESS_TOKEN"

EXIT_DATA_ERROR = 2
EXIT_NO_TOKEN = 3
EXIT_NOT_TRADING_DAY = 4
EXIT_RETIRED = 5
EXIT_BAD_WINDOW = 6


class EmitterError(Exception):
    """Base class: the emitter refuses rather than guess."""


class NotATradingDayError(EmitterError):
    pass


class StaleDataError(EmitterError):
    pass


class MissingTokenError(EmitterError):
    pass


class RetiredSpecError(EmitterError):
    pass


class BadWindowError(EmitterError):
    pass


def parse_hhmm(text: str) -> time:
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise BadWindowError(f"time {text!r} must be HH:MM (24h, WIB)")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        raise BadWindowError(f"time {text!r} is not a valid time of day")
    return time(hour, minute)


@dataclass(frozen=True)
class RebalanceWindow:
    """The target's [as_of, complete_by] window and the bot's --rebalance-at.

    Default = IDX session 1 (09:00-11:30 WIB). The bot's own --rebalance-at
    default (15:00) is after continuous trading ends and is rejected here.
    """

    start: time = time(9, 0)
    end: time = time(11, 30)
    rebalance_at: time = time(9, 30)

    def __post_init__(self) -> None:
        if not self.start < self.end:
            raise BadWindowError(f"window {self.start:%H:%M}-{self.end:%H:%M} is empty or inverted")
        if not self.start <= self.rebalance_at < self.end:
            raise BadWindowError(
                f"rebalance time {self.rebalance_at:%H:%M} WIB is outside the window "
                f"{self.start:%H:%M}-{self.end:%H:%M} (session 1); the bot's default 15:00 is "
                "after continuous trading ends"
            )


DEFAULT_WINDOW = RebalanceWindow()


@dataclass(frozen=True)
class Decision:
    as_of: date
    weight: float
    signal_day: date
    signal_value: Optional[float]
    on_grid: bool
    entry_day: Optional[date]
    exit_day: Optional[date]
    reason: str
    last_close: float
    n_bars: int
    anchor_day: date
    bars_sha256: str
    symbol: str = ""


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def normalise_bars(df: pd.DataFrame) -> pd.DataFrame:
    """date -> ISO string (what the grid fed the signal), sorted, deduped."""
    out = df[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["close"])
    out = out.drop_duplicates("date", keep="last").sort_values("date")
    return out.reset_index(drop=True)


def load_local_bars(path: Path, require_traded_last_bar: Optional[date] = None) -> pd.DataFrame:
    """Normalised local bars. With ``require_traded_last_bar`` (the session before the
    emit day), a zero-volume bar on that day is refused: it is the refresh's in-progress
    placeholder (close = prior close), not a real close, and a signal from it is wrong."""
    raw = pd.read_parquet(path)
    if require_traded_last_bar is not None and "volume" in raw.columns:
        day = require_traded_last_bar.isoformat()
        d = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
        vol = pd.to_numeric(raw.loc[d == day, "volume"], errors="coerce")
        if len(vol) and (vol.fillna(0) <= 0).all():
            raise StaleDataError(
                f"{path.stem}: the {day} bar has zero volume (an in-progress placeholder written "
                "during market hours, not a close); re-run `make refresh-prices` after the close"
            )
    return normalise_bars(raw)


def fetch_live_bars(symbol: str, end: date) -> pd.DataFrame:
    """Chartbit daily bars via LiveDataSource. Needs SB_ACCESS_TOKEN in env."""
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise MissingTokenError(
            f"--source live needs {TOKEN_ENV} in the environment (never read "
            "from a file); it is unset. Use --source local for offline data."
        )
    from screener.screen_universe import LiveDataSource

    ds = LiveDataSource(bearer_token=token)
    return normalise_bars(ds.daily_history(symbol, HISTORY_START, end.isoformat()))


def _bars_hash(bars: pd.DataFrame) -> str:
    payload = "\n".join(f"{d},{c!r}" for d, c in zip(bars["date"], bars["close"]))
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def _grid_entries(window: pd.DataFrame, spec: EmitterSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(all signal rows, sequential long entries) — the backtest's own steps.

    drop_unpriced=False keeps the newest signal rows, which have no forward
    return yet (it needs close_{s+1+hold}). The scan is causal, so entries
    among earlier rows are identical to the backtest's.
    """
    sig = spec.signal_fn(window, hold=spec.hold, drop_unpriced=False)
    longs = deoverlap_sequential(
        sig, hold=spec.hold, eligible=long_only_mask(sig["signal"].to_numpy(dtype=float))
    )
    return sig, longs


def decide(
    bars: pd.DataFrame,
    as_of: date,
    spec: EmitterSpec,
    calendar: MarketCalendar = CALENDAR,
    symbol: Optional[str] = None,
) -> Decision:
    """Target weight of one symbol for trading day `as_of`, from bars strictly before it.

    `symbol` defaults to the spec's sole symbol; multi-name specs must pass it.
    """
    return decide_normalised(normalise_bars(bars), as_of, spec, calendar, symbol)


def decide_normalised(
    bars: pd.DataFrame,
    as_of: date,
    spec: EmitterSpec,
    calendar: MarketCalendar = CALENDAR,
    symbol: Optional[str] = None,
) -> Decision:
    """`decide` for bars already passed through normalise_bars (replay hot path)."""
    symbol = symbol if symbol is not None else spec.symbol
    if symbol not in spec.symbols:
        raise EmitterError(f"{symbol} is not one of {spec.strategy_id}'s symbols {spec.symbols}")
    if not calendar.is_trading_day(as_of):
        raise NotATradingDayError(f"{as_of} is not an IDX trading day")
    prev_day = calendar.prev_trading_day(as_of)

    window = bars[(bars["date"] >= HISTORY_START) & (bars["date"] < as_of.isoformat())]
    window = window.reset_index(drop=True)
    if window.empty:
        raise StaleDataError(f"no bars for {symbol} between {HISTORY_START} and {as_of}")
    last_bar = date.fromisoformat(window["date"].iloc[-1])
    if last_bar != prev_day:
        raise StaleDataError(
            f"stale data ({symbol}): latest bar is {last_bar} but the session before "
            f"{as_of} is {prev_day}; refresh the bars (--source live)"
        )
    if len(window) < 2:  # one bar yields no signal row
        raise StaleDataError(f"{symbol}: only {len(window)} bars since {HISTORY_START}")

    sig, longs = _grid_entries(window, spec)
    entries = [date.fromisoformat(d) for d in longs["date"]]
    last_row = sig.iloc[-1]
    on_grid = sig.index[-1] in longs.index  # newest signal is a sequential entry
    signal_value = float(last_row["signal"])
    base = dict(
        as_of=as_of,
        signal_day=prev_day,
        signal_value=signal_value,
        on_grid=on_grid,
        last_close=float(window["close"].iloc[-1]),
        n_bars=len(window),
        anchor_day=date.fromisoformat(sig["date"].iloc[0]),
        bars_sha256=_bars_hash(window),
        symbol=symbol,
    )

    windows = [(s, calendar.next_trading_day(s)) for s in entries]
    windows = [(s, e, calendar.add_trading_days(e, spec.hold)) for s, e in windows]
    live = [(s, e, x) for s, e, x in windows if e <= as_of < x]
    if live:
        s, e, x = live[-1]
        entry_signal = float(longs.loc[longs["date"] == s.isoformat(), "signal"].iloc[0])
        verb = "enter today" if e == as_of else f"holding since {e}"
        return Decision(
            weight=1.0, entry_day=e, exit_day=x,
            reason=(f"long: signal {entry_signal:+.4f} on grid row at close {s} -> "
                    f"{verb}, {spec.hold} sessions, exit leg {x}"),
            **base,
        )

    exit_today = [x for _, _, x in windows if x == as_of]
    if signal_value <= 0.0:
        why = f"signal <= 0 ({signal_value:+.4f}) at close {prev_day}"
    elif not on_grid:
        why = (f"signal {signal_value:+.4f} at close {prev_day} fired while a "
               f"prior entry was still open (sequential de-overlap, hold {spec.hold})")
    else:
        why = f"signal {signal_value:+.4f} at close {prev_day}: no entry"
    if exit_today:
        why = f"exit leg: hold of {spec.hold} sessions ends {as_of}; {why}"
    return Decision(
        weight=0.0, entry_day=None, exit_day=exit_today[0] if exit_today else None,
        reason=f"flat: {why}", **base,
    )




# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def one_lot_need(last_close: float, lots: int = 1) -> int:
    """IDR needed so `lots` lots floor exactly, across the one-lot price band."""
    return int(math.ceil(ONE_LOT_HEADROOM * last_close * LOT_SHARES * lots))


def price_band(lots: int = 1) -> tuple[float, float]:
    """Fill-price band (as a multiple of last close) where sizing floors to exactly `lots`.

    Upper edge is the headroom; the lower edge is where a further lot would fit
    (1.3*lots/(lots+1)), never tighter than the one-lot ONE_LOT_PRICE_BAND.
    """
    low = max(ONE_LOT_PRICE_BAND[0], round(ONE_LOT_HEADROOM * lots / (lots + 1) + 0.01, 3))
    return low, ONE_LOT_PRICE_BAND[1]


def base_notional_for(last_close: float, lots: int = 1) -> int:
    """Base notional that makes weight 1.0 on ONE name floor to exactly `lots` lots."""
    return one_lot_need(last_close, lots)


def size_targets(decisions: Sequence[Decision], lots: int) -> tuple[int, list[float]]:
    """(base_notional_idr, weights in decision order): active names get exactly `lots` lots.

    Weights are 0.0 for flat names. A single name keeps weight 1.0 and the
    one-name base; several names share one base, each weight just covering its
    own need, so the weights sum below 1.0 whatever subset is active.
    """
    needs = [one_lot_need(d.last_close, lots) for d in decisions]
    if len(decisions) == 1:
        return needs[0], [decisions[0].weight]
    base = int(math.ceil(sum(needs) * (1.0 + SIZING_SLACK)))
    weights = [
        math.ceil(n * (1.0 + WEIGHT_MARGIN) / base * WEIGHT_DECIMALS) / WEIGHT_DECIMALS
        if d.weight > 0 else 0.0
        for n, d in zip(needs, decisions)
    ]
    return base, weights


def _wib(d: date, at: time) -> str:
    return datetime(d.year, d.month, d.day, at.hour, at.minute, tzinfo=WIB).isoformat()


def build_target(
    decisions: Sequence[Decision], spec: EmitterSpec, window: RebalanceWindow = DEFAULT_WINDOW
) -> dict[str, Any]:
    """The target_portfolio.json document (fields exactly as the Go loader allows)."""
    if not decisions or len({d.as_of for d in decisions}) != 1:
        raise EmitterError("build_target needs decisions for one trading day")
    base, weights = size_targets(decisions, spec.lots)
    as_of = decisions[0].as_of
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": _wib(as_of, window.start),
        "strategy_id": spec.strategy_id,
        "capital_model": spec.capital_model,
        "base_notional_idr": base,
        "targets": [{"symbol": d.symbol, "weight": w} for d, w in zip(decisions, weights)],
        "constraints": {
            "max_participation_rate": MAX_PARTICIPATION_RATE,
            "complete_by": _wib(as_of, window.end),
            "board": BOARD,
        },
    }


def _symbol_sidecar(d: Decision) -> dict[str, Any]:
    return {
        "symbol": d.symbol,
        "reason": d.reason,
        "signal_day": d.signal_day.isoformat(),
        "signal_value": d.signal_value,
        "on_deoverlap_grid": d.on_grid,
        "entry_day": d.entry_day.isoformat() if d.entry_day else None,
        "exit_day": d.exit_day.isoformat() if d.exit_day else None,
        "last_close": d.last_close,
        "grid_anchor_day": d.anchor_day.isoformat(),
        "n_bars": d.n_bars,
        "bars_sha256": d.bars_sha256,
    }


def build_sidecar(
    decisions: Sequence[Decision], spec: EmitterSpec, doc: dict[str, Any],
    window: RebalanceWindow = DEFAULT_WINDOW,
) -> dict[str, Any]:
    return {
        "strategy_id": spec.strategy_id,
        "spec_status": spec.status,
        "spec_status_note": spec.status_note,
        "family": spec.family,
        "hold_sessions": spec.hold,
        "lots_per_name": spec.lots,
        "as_of_day": decisions[0].as_of.isoformat(),
        "rebalance_window": f"{window.start:%H:%M}-{window.end:%H:%M} WIB",
        "rebalance_at": f"{window.rebalance_at:%H:%M}",
        "base_notional_idr": doc["base_notional_idr"],
        "exact_lots_price_band": list(price_band(spec.lots)),
        "history_start": HISTORY_START,
        "deoverlap": "sequential_long_only (D2 fixed): enter s+1, exit s+1+hold, resume at exit day",
        "timing": "signal at close D-1, trade D; backtest enters at close_{s+1} (D1 fixed)",
        "symbols": [
            {**_symbol_sidecar(d), "weight": t["weight"]}
            for d, t in zip(decisions, doc["targets"])
        ],
    }


def sidecar_path(out: Path) -> Path:
    return out.with_name(out.stem + SIDECAR_SUFFIX)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, indent=2, sort_keys=False) + "\n"


def write_outputs(out: Path, doc: dict[str, Any], sidecar: dict[str, Any]) -> None:
    """Sidecar first, target last: a target file never exists without its why."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(sidecar_path(out), _dump(sidecar))
    _atomic_write(out, _dump(doc))


# ---------------------------------------------------------------------------
# live-session command (printed, never executed here)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_SESSION_BIN = REPO_ROOT / "bin" / "live-session"
PAPER_EQUITY_IDR = 15_000_000   # risk caps are fractions of this; paper only


def live_session_command(
    spec: EmitterSpec, out: Path, window: RebalanceWindow,
    equity_idr: int = PAPER_EQUITY_IDR, runs_dir: Optional[Path] = None,
) -> str:
    """The live-session invocation for this target.

    A paper spec (or any non-candidate) always gets ``--sink paper --source live``:
    market data only, no trading token, no arming. Only a ``candidate`` gets a live
    sink, with the account and equity left as shell variables the operator must set.
    Store naming and the prior-stores glob are live-session's own (a paper sink's
    stores are ``<date>-<id>-paper-aN.db``, so they never share a glob with live).
    """
    out = Path(out).resolve()
    runs = Path(runs_dir).resolve() if runs_dir else out.parent
    parts = [shlex.quote(str(LIVE_SESSION_BIN))]
    if spec.can_go_live:
        parts += ["--sink live", "--source live", '--account "$ACCOUNT"', '--equity-idr "$EQUITY_IDR"']
    else:
        parts += ["--sink paper", "--source live", f"--equity-idr {equity_idr}"]
    parts += [
        "--strategy rebalance",
        f"--target-portfolio {shlex.quote(str(out))}",
        f"--symbols {','.join(spec.symbols)}",
        f"--rebalance-at {window.rebalance_at:%H:%M}",
        f"--runs-dir {shlex.quote(str(runs))}",
    ]
    return " ".join(parts)


def live_session_argv(
    spec: EmitterSpec, out: Path, window: RebalanceWindow, equity_idr: int = PAPER_EQUITY_IDR,
    runs_dir: Optional[Path] = None,
) -> list[str]:
    """argv form of a PAPER live-session command (paper_day runs this, never via a shell)."""
    if spec.can_go_live:
        raise EmitterError(f"{spec.strategy_id} is a live candidate: paper_day only runs paper specs")
    return shlex.split(live_session_command(spec, out, window, equity_idr, runs_dir))


def guard_paper_midhold(decision: Decision, held_lots: int) -> Decision:
    """A paper forward test never buys into a trade already under way.

    A 5-session hold enters on E and carries E..E+hold-1. If the paper stores hold
    no position for the name but the chain says it is already past its entry day,
    the entry was missed (the test started mid-hold, or that day's run failed):
    buying now would be a different, late trade, so the target stays flat until the
    next entry day. With a paper position held the target is unchanged, so the day-2
    run keeps it and the exit day (weight 0) sells it to zero.
    """
    if decision.weight <= 0 or decision.entry_day is None or decision.entry_day >= decision.as_of:
        return decision
    if held_lots > 0:
        return decision
    return replace(
        decision, weight=0.0,
        reason=(f"flat (paper): entry day {decision.entry_day} was missed and no paper position is "
                f"held; not entering mid-hold; waiting for the next entry. Chain said: {decision.reason}"),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_bars(symbol: str, source: str, as_of: date, data_dir: Path,
               calendar: MarketCalendar) -> pd.DataFrame:
    if source == "live":
        return fetch_live_bars(symbol, calendar.prev_trading_day(as_of))
    path = data_dir / f"{symbol}.parquet"
    if not path.exists():
        raise EmitterError(f"local bars not found for {symbol}: {path}")
    return load_local_bars(path, require_traded_last_bar=calendar.prev_trading_day(as_of))


def _window_from_args(args: argparse.Namespace) -> RebalanceWindow:
    return RebalanceWindow(
        start=parse_hhmm(args.window_start),
        end=parse_hhmm(args.window_end),
        rebalance_at=parse_hhmm(args.rebalance_at),
    )


def _cmd_emit(args: argparse.Namespace, spec: EmitterSpec) -> int:
    if not (spec.is_candidate or spec.is_paper) and not args.allow_retired:
        raise RetiredSpecError(
            f"{spec.strategy_id} is {spec.status} ({spec.status_note or 'not a live candidate'}); "
            "refusing to emit it for live. Pass --allow-retired to emit it deliberately."
        )
    window = _window_from_args(args)
    as_of = date.fromisoformat(args.as_of)
    if not CALENDAR.is_trading_day(as_of):
        raise NotATradingDayError(f"{as_of} is not an IDX trading day; nothing emitted")
    data_dir = Path(args.data_dir)
    out = Path(args.out)
    runs_dir = Path(args.runs_dir) if args.runs_dir else out.resolve().parent
    decisions = [
        decide(_load_bars(sym, args.source, as_of, data_dir, CALENDAR), as_of, spec, CALENDAR, symbol=sym)
        for sym in spec.symbols
    ]
    if spec.is_paper:
        held = paper_net_lots(runs_dir, spec.strategy_id)
        decisions = [guard_paper_midhold(d, held.get(d.symbol, 0)) for d in decisions]
    doc = build_target(decisions, spec, window)
    write_outputs(out, doc, build_sidecar(decisions, spec, doc, window))
    print(f"{spec.strategy_id} {as_of}: base={doc['base_notional_idr']} -> {out}")
    for d, t in zip(decisions, doc["targets"]):
        print(f"  {d.symbol} weight={t['weight']}: {d.reason}")
    if not spec.is_candidate:
        print(f"  NOTE: spec status is {spec.status} ({spec.status_note}); the command below is PAPER only")
    print(f"live-session command: {live_session_command(spec, out, window, args.equity_idr, runs_dir)}")
    return 0


def _cmd_replay(args: argparse.Namespace, spec: EmitterSpec) -> int:
    from strategy_fit.target_emitter_replay import calendar_from_bars, format_result, replay

    rc = 0
    for sym in spec.symbols:
        bars = load_local_bars(Path(args.data_dir) / f"{sym}.parquet")
        result = replay(bars, spec, calendar_from_bars(bars, CALENDAR), symbol=sym)
        print(format_result(result, spec, symbol=sym))
        if result.entry_match_rate != 1.0 or result.weight_mismatches:
            rc = 1
    return rc


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strategy_fit.target_emitter", description=__doc__.split("\n")[1])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("emit", "replay"):
        s = sub.add_parser(name)
        s.add_argument("--strategy", required=True)
        s.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    emit = sub.choices["emit"]
    emit.add_argument("--as-of", required=True, help="trading day D, YYYY-MM-DD")
    emit.add_argument("--out", required=True)
    emit.add_argument("--source", choices=("local", "live"), default="local")
    emit.add_argument("--rebalance-at", default=f"{DEFAULT_WINDOW.rebalance_at:%H:%M}",
                      help="bot --rebalance-at, HH:MM WIB; must fall inside the window")
    emit.add_argument("--window-start", default=f"{DEFAULT_WINDOW.start:%H:%M}")
    emit.add_argument("--window-end", default=f"{DEFAULT_WINDOW.end:%H:%M}")
    emit.add_argument("--runs-dir", default="",
                      help="where live-session keeps stores (default: the --out directory)")
    emit.add_argument("--equity-idr", type=int, default=PAPER_EQUITY_IDR,
                      help="sleeve equity for the printed PAPER command (risk caps are fractions of it)")
    emit.add_argument("--allow-retired", action="store_true",
                      help="emit a retired/example spec anyway (never for live use)")
    return p


def main(argv: Optional[Sequence[str]] = None,
         registry: Optional[dict[str, EmitterSpec]] = None) -> int:
    registry = SPECS if registry is None else registry
    args = _parser().parse_args(argv)
    if args.strategy not in registry:
        print(f"error: unknown strategy {args.strategy!r}; known: {sorted(registry)}", file=sys.stderr)
        return EXIT_DATA_ERROR
    spec = registry[args.strategy]
    try:
        return _cmd_emit(args, spec) if args.cmd == "emit" else _cmd_replay(args, spec)
    except MissingTokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_TOKEN
    except NotATradingDayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_TRADING_DAY
    except RetiredSpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RETIRED
    except BadWindowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_WINDOW
    except (EmitterError, SpecError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR


if __name__ == "__main__":
    sys.exit(main())
