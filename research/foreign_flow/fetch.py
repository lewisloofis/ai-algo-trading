"""
foreign_flow/fetch.py - resumable per-symbol download of foreign-flow history (plan 22 section 2.1).

One call per (symbol, calendar year); completed past years are recorded in a manifest and
skipped on rerun, the current year is always refetched. 401 / 403 stop the whole run
(progress is saved). Transient errors (URLError, 5xx, 429) are retried with backoff.
The token is read from SB_ACCESS_TOKEN only and is never printed.

    python -m foreign_flow.fetch [--symbols A,B] [--start-year 2016] [--end-year 2026]
                                 [--sleep 1.0] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Callable

import pandas as pd

from foreign_flow import DATA_DIR, MANIFEST_PATH, UNIVERSE_CSV
from foreign_flow.client import ForeignFlowClient, normalise
from foreign_flow.universe import download_list, read_universe_csv
from screener.sb_http import SBHttpError, classify_error

DEFAULT_START_YEAR = 2016
RETRY_ATTEMPTS = 4
BACKOFF_BASE_S = 2.0
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
NO_TOKEN_MSG = ("SB_ACCESS_TOKEN is not set. From research/ run: "
                "set -a && source ../.env.local && set +a")
TOKEN_EXPIRED_MSG = "token expired: refresh tokens then rerun; progress is saved"
WAF_MSG = ("HTTP 403 Forbidden - possible WAF/User-Agent block (not a token problem); "
           "stopping. Progress is saved.")


class RunAborted(RuntimeError):
    """401/403: the whole run must stop. The message is safe to print."""


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, SBHttpError):
        return exc.status in TRANSIENT_STATUSES
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def year_range(year: int, today: dt.date) -> tuple[str, str]:
    end = min(dt.date(year, 12, 31), today)
    return f"{year}-01-01", end.isoformat()


def load_manifest(path: Path) -> dict:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text())


def _atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_manifest(path: Path, manifest: dict) -> None:
    _atomic_write(path, lambda p: p.write_text(json.dumps(manifest, indent=1, sort_keys=True)))


def merge_symbol_frame(path: Path, new: pd.DataFrame) -> int:
    """Merge ``new`` into the symbol's parquet (date-sorted, deduped, new wins); returns total rows."""
    frames = [normalise(pd.read_parquet(path))] if Path(path).exists() else []
    frames.append(normalise(new))
    merged = (pd.concat([f for f in frames if len(f)] or [normalise(new)], ignore_index=True)
              .drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True))
    merged = normalise(merged)
    _atomic_write(path, lambda p: merged.to_parquet(p, index=False))
    return len(merged)


def fetch_with_retry(client: ForeignFlowClient, symbol: str, date_from: str, date_to: str, *,
                     sleep: Callable[[float], None] = time.sleep) -> pd.DataFrame:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return client.fetch(symbol, date_from, date_to)
        except SBHttpError as exc:
            if exc.status == 401:
                raise RunAborted(TOKEN_EXPIRED_MSG) from None
            if exc.status == 403:
                raise RunAborted(WAF_MSG) from None
            if not _is_transient(exc) or attempt == RETRY_ATTEMPTS:
                raise
        except Exception as exc:  # noqa: BLE001 - transient check below, otherwise re-raise
            if not _is_transient(exc) or attempt == RETRY_ATTEMPTS:
                raise
        sleep(BACKOFF_BASE_S * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def plan_calls(symbols: list[str], start_year: int, end_year: int, manifest: dict,
               today: dt.date) -> list[tuple[str, int]]:
    """(symbol, year) pairs still to fetch: past years in the manifest are skipped."""
    out = []
    for sym in symbols:
        done = manifest.get(sym, {})
        for year in range(start_year, end_year + 1):
            if year < today.year and str(year) in done:
                continue
            out.append((sym, year))
    return out


def run(client: ForeignFlowClient, symbols: list[str], *, start_year: int, end_year: int,
        data_dir: Path = DATA_DIR, manifest_path: Path = MANIFEST_PATH, sleep_s: float = 1.0,
        today: dt.date | None = None, sleep: Callable[[float], None] = time.sleep,
        out=sys.stdout) -> dict:
    """Returns {"fetched": n_calls, "rows": n, "failures": [...], "aborted": msg|None}."""
    today = today or dt.date.today()
    manifest = load_manifest(manifest_path)
    summary = {"fetched": 0, "rows": 0, "failures": [], "aborted": None}
    calls_made = 0
    for sym in symbols:
        years_done, sym_rows = 0, 0
        for year in range(start_year, end_year + 1):
            if year < today.year and str(year) in manifest.get(sym, {}):
                continue
            if calls_made:
                sleep(sleep_s)
            calls_made += 1
            d_from, d_to = year_range(year, today)
            try:
                frame = fetch_with_retry(client, sym, d_from, d_to, sleep=sleep)
            except RunAborted as exc:
                summary["aborted"] = str(exc)
                print(f"ABORT {sym} {year}: {exc}", file=out)
                return summary
            except Exception as exc:  # noqa: BLE001 - recorded, run continues with next call
                label = classify_error(exc)
                summary["failures"].append(f"{sym} {year}: {label}")
                print(f"FAIL {sym} {year}: {label}", file=out)
                continue
            if len(frame):
                merge_symbol_frame(Path(data_dir) / f"{sym}.parquet", frame)
            manifest.setdefault(sym, {})[str(year)] = len(frame)   # only after the data is on disk
            save_manifest(manifest_path, manifest)
            years_done += 1
            sym_rows += len(frame)
            summary["fetched"] += 1
            summary["rows"] += len(frame)
        print(f"{sym}: years fetched {years_done}, rows {sym_rows}", file=out)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch foreign-flow history per symbol-year (plan 22).")
    ap.add_argument("--symbols", help="comma-separated; default = universe download list")
    ap.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    ap.add_argument("--end-year", type=int, default=dt.date.today().year)
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between calls")
    ap.add_argument("--dry-run", action="store_true", help="print planned call count; no network")
    args = ap.parse_args(argv)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif UNIVERSE_CSV.exists():
        symbols = download_list(read_universe_csv(UNIVERSE_CSV))
    else:
        print("no universe_monthly.csv: run python -m foreign_flow.universe first", file=sys.stderr)
        return 2

    today = dt.date.today()
    planned = plan_calls(symbols, args.start_year, args.end_year, load_manifest(MANIFEST_PATH), today)
    if args.dry_run:
        total = len(symbols) * (args.end_year - args.start_year + 1)
        print(f"dry-run: {len(symbols)} symbols, years {args.start_year}-{args.end_year}: "
              f"{len(planned)} calls planned ({total - len(planned)} already complete), "
              f"~{len(planned) * args.sleep / 60:.0f} min at --sleep {args.sleep}")
        return 0

    token = os.environ.get("SB_ACCESS_TOKEN", "")
    if not token:
        print(NO_TOKEN_MSG, file=sys.stderr)
        return 2
    summary = run(ForeignFlowClient(token), symbols, start_year=args.start_year,
                  end_year=args.end_year, data_dir=DATA_DIR, manifest_path=MANIFEST_PATH,
                  sleep_s=args.sleep, today=today)
    print(f"summary: {summary['fetched']} calls ok, {summary['rows']} rows, "
          f"{len(summary['failures'])} failures")
    for line in summary["failures"]:
        print(f"  failed: {line}")
    if summary["aborted"]:
        print(f"aborted: {summary['aborted']}", file=sys.stderr)
        return 1
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
