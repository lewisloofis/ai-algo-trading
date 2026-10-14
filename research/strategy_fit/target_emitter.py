"""
strategy_fit/target_emitter.py — daily target emitter (plan 18 §8 step 2a).

Turns a grid cell's research signal into the ``target_portfolio.json`` that
``strategy-run --strategy rebalance`` consumes (contract: plan 18 §3.1, Go
loader: strategy/portfolio/target.go). Run once pre-open on day D.

    python -m strategy_fit.target_emitter emit --strategy bnbr_momo_h2 \
        --as-of 2026-10-15 --out /path/target.json [--source local|live]
    python -m strategy_fit.target_emitter replay --strategy bnbr_momo_h2

The signal is NOT reimplemented: ``SPECS[...].signal_fn`` is the very function
object the grid scored (families_single_name.daily_momentum_signal), the
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

Weight -> exactly 1 lot
-----------------------
The Go rebalance strategy sizes floor(weight*base / (snapTick(price)*100)).
The emitter emits weight 1.0 (or 0.0) and sets base_notional_idr =
ceil(ONE_LOT_HEADROOM * last_close * 100), so any price in
(base/200, base/100] floors to exactly 1 lot. See ``base_notional_for``.

Output
------
The Go loader rejects unknown fields (target.go Parse), so the explanatory
fields live in a sidecar ``<out stem>.signal.json``. Both files are written
atomically (temp file + os.replace) and contain no wall-clock values, so the
same inputs give byte-identical files.

The live path needs ``SB_BEARER_TOKEN`` in the environment and never reads a
token file. It is optional; the default source is the local daily parquet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import pandas as pd

from lib.market_calendar import CALENDAR, MarketCalendar
from strategy_fit.families_single_name import daily_momentum_signal
from strategy_fit.harness import deoverlap_sequential
from strategy_fit.long_only import long_only_mask
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
AS_OF_HOUR_WIB, AS_OF_MINUTE_WIB = 8, 30
COMPLETE_BY_HOUR_WIB = 15
MAX_PARTICIPATION_RATE = 0.05
BOARD = "RG"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"
TOKEN_ENV = "SB_BEARER_TOKEN"

EXIT_DATA_ERROR = 2
EXIT_NO_TOKEN = 3


class EmitterError(Exception):
    """Base class: the emitter refuses rather than guess."""


class NotATradingDayError(EmitterError):
    pass


class StaleDataError(EmitterError):
    pass


class MissingTokenError(EmitterError):
    pass


@dataclass(frozen=True)
class EmitterSpec:
    strategy_id: str
    symbol: str
    family: str
    hold: int
    signal_fn: Callable[..., pd.DataFrame]
    capital_model: str = "owned_cash"


# The plan-10 cell BNBR daily_momentum, hold=2 (grid: SINGLE_NAME_SIGNAL_FNS /
# SINGLE_NAME_HOLDS in run_strategy_fit.py).
SPECS: dict[str, EmitterSpec] = {
    "bnbr_momo_h2": EmitterSpec(
        strategy_id="bnbr_momo_h2",
        symbol="BNBR",
        family="daily_momentum",
        hold=2,
        signal_fn=daily_momentum_signal,
    ),
}


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


def load_local_bars(path: Path) -> pd.DataFrame:
    return normalise_bars(pd.read_parquet(path))


def fetch_live_bars(symbol: str, end: date) -> pd.DataFrame:
    """Chartbit daily bars via LiveDataSource. Needs SB_BEARER_TOKEN in env."""
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
) -> Decision:
    """Target weight for trading day `as_of`, from bars strictly before it."""
    return decide_normalised(normalise_bars(bars), as_of, spec, calendar)


def decide_normalised(
    bars: pd.DataFrame,
    as_of: date,
    spec: EmitterSpec,
    calendar: MarketCalendar = CALENDAR,
) -> Decision:
    """`decide` for bars already passed through normalise_bars (replay hot path)."""
    if not calendar.is_trading_day(as_of):
        raise NotATradingDayError(f"{as_of} is not an IDX trading day")
    prev_day = calendar.prev_trading_day(as_of)

    window = bars[(bars["date"] >= HISTORY_START) & (bars["date"] < as_of.isoformat())]
    window = window.reset_index(drop=True)
    if window.empty:
        raise StaleDataError(f"no bars for {spec.symbol} between {HISTORY_START} and {as_of}")
    last_bar = date.fromisoformat(window["date"].iloc[-1])
    if last_bar != prev_day:
        raise StaleDataError(
            f"stale data: latest bar is {last_bar} but the session before "
            f"{as_of} is {prev_day}; refresh the bars (--source live)"
        )
    if len(window) < 2:  # one bar yields no signal row
        raise StaleDataError(f"only {len(window)} bars since {HISTORY_START}")

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

def base_notional_for(last_close: float) -> int:
    """Base notional that makes weight 1.0 floor to exactly one lot."""
    return int(math.ceil(ONE_LOT_HEADROOM * last_close * LOT_SHARES))


def _wib(d: date, hour: int, minute: int = 0) -> str:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=WIB).isoformat()


def build_target(decision: Decision, last_close: float, spec: EmitterSpec) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": _wib(decision.as_of, AS_OF_HOUR_WIB, AS_OF_MINUTE_WIB),
        "strategy_id": spec.strategy_id,
        "capital_model": spec.capital_model,
        "base_notional_idr": base_notional_for(last_close),
        "targets": [{"symbol": spec.symbol, "weight": decision.weight}],
        "constraints": {
            "max_participation_rate": MAX_PARTICIPATION_RATE,
            "complete_by": _wib(decision.as_of, COMPLETE_BY_HOUR_WIB),
            "board": BOARD,
        },
    }


def build_sidecar(decision: Decision, spec: EmitterSpec, doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": spec.strategy_id,
        "symbol": spec.symbol,
        "family": spec.family,
        "hold_sessions": spec.hold,
        "as_of_day": decision.as_of.isoformat(),
        "weight": decision.weight,
        "reason": decision.reason,
        "signal_day": decision.signal_day.isoformat(),
        "signal_value": decision.signal_value,
        "on_deoverlap_grid": decision.on_grid,
        "entry_day": decision.entry_day.isoformat() if decision.entry_day else None,
        "exit_day": decision.exit_day.isoformat() if decision.exit_day else None,
        "last_close": decision.last_close,
        "base_notional_idr": doc["base_notional_idr"],
        "one_lot_price_band": list(ONE_LOT_PRICE_BAND),
        "history_start": HISTORY_START,
        "grid_anchor_day": decision.anchor_day.isoformat(),
        "n_bars": decision.n_bars,
        "bars_sha256": decision.bars_sha256,
        "deoverlap": "sequential_long_only (D2 fixed): enter s+1, exit s+1+hold, resume at exit day",
        "timing": "signal at close D-1, trade D; backtest enters at close_{s+1} (D1 fixed)",
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
# CLI
# ---------------------------------------------------------------------------

def _load_bars(spec: EmitterSpec, source: str, as_of: date, data_dir: Path,
               calendar: MarketCalendar) -> pd.DataFrame:
    if source == "live":
        return fetch_live_bars(spec.symbol, calendar.prev_trading_day(as_of))
    path = data_dir / f"{spec.symbol}.parquet"
    if not path.exists():
        raise EmitterError(f"local bars not found: {path}")
    return load_local_bars(path)


def _cmd_emit(args: argparse.Namespace) -> int:
    spec = SPECS[args.strategy]
    as_of = date.fromisoformat(args.as_of)
    bars = _load_bars(spec, args.source, as_of, Path(args.data_dir), CALENDAR)
    decision = decide(bars, as_of, spec, CALENDAR)
    doc = build_target(decision, decision.last_close, spec)
    out = Path(args.out)
    write_outputs(out, doc, build_sidecar(decision, spec, doc))
    print(f"{spec.strategy_id} {as_of}: weight={decision.weight} base={doc['base_notional_idr']} "
          f"-> {out}\n  {decision.reason}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from strategy_fit.target_emitter_replay import calendar_from_bars, format_result, replay

    spec = SPECS[args.strategy]
    bars = load_local_bars(Path(args.data_dir) / f"{spec.symbol}.parquet")
    result = replay(bars, spec, calendar_from_bars(bars, CALENDAR))
    print(format_result(result, spec))
    return 0 if result.entry_match_rate == 1.0 and not result.weight_mismatches else 1


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
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.strategy not in SPECS:
        print(f"error: unknown strategy {args.strategy!r}; known: {sorted(SPECS)}", file=sys.stderr)
        return EXIT_DATA_ERROR
    try:
        return _cmd_emit(args) if args.cmd == "emit" else _cmd_replay(args)
    except MissingTokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_TOKEN
    except (EmitterError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR


if __name__ == "__main__":
    sys.exit(main())
