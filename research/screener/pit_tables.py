"""
screener/pit_tables.py - normalise raw PIT responses into flat CSV rows (plan 23).

Stdlib only (ships in the box .pyz). The raw body is the record; these tables
are regenerable from it. ``tradeable`` is emitted for boards only: on the
sector route it is always 0 and is NOT a tradeability flag (plan 23 section 2.1).
"""
from __future__ import annotations

import csv
import gzip
import io
from typing import Iterable, Mapping

from screener.emitten import company_status_text, notation_text, _tradeable_flag

BOARD_COLUMNS = ("dt", "board", "symbol", "company_status", "notation_codes", "uma", "tradeable")
INDEX_COLUMNS = ("dt", "index", "symbol", "status", "notation_codes", "uma")
LISTED_COLUMNS = ("dt", "sector_id", "symbol", "company_status", "notation_codes", "uma")


def _codes(row: Mapping) -> str:
    items = row.get("notations")
    if items is None:
        items = row.get("notation")
    return "|".join(notation_text(n) for n in items or [])


def _symbol(row: Mapping) -> str:
    return str(row.get("symbol") or "").strip().upper()


def _flag(raw: object) -> str:
    return "" if raw is None else str(int(bool(raw)))


def board_rows(dt: str, board: str, body: Mapping) -> list[dict]:
    out = []
    for group in body.get("data") or []:
        for row in group.get("companies") or []:
            if not _symbol(row):
                continue
            tradeable = _tradeable_flag(row.get("tradeable"))
            out.append({
                "dt": dt, "board": board, "symbol": _symbol(row),
                "company_status": company_status_text(row.get("company_status")),
                "notation_codes": _codes(row), "uma": _flag(row.get("uma")),
                "tradeable": "" if tradeable is None else str(int(tradeable)),
            })
    return out


def index_rows(dt: str, index: str, pages: Iterable[Mapping]) -> list[dict]:
    return [
        {"dt": dt, "index": index, "symbol": _symbol(r),
         "status": company_status_text(r.get("status")),
         "notation_codes": _codes(r), "uma": _flag(r.get("uma"))}
        for page in pages for r in page.get("data") or [] if _symbol(r)
    ]


def listed_rows(dt: str, sector_id: str, pages: Iterable[Mapping]) -> list[dict]:
    return [
        {"dt": dt, "sector_id": sector_id, "symbol": _symbol(r),
         "company_status": company_status_text(r.get("company_status")),
         "notation_codes": _codes(r), "uma": _flag(r.get("uma"))}
        for page in pages for r in page.get("data") or [] if _symbol(r)
    ]


def gzip_bytes(data: bytes) -> bytes:
    """Deterministic gzip (mtime 0) so identical input gives identical bytes."""
    return gzip.compress(data, mtime=0)


def csv_gz(columns: tuple[str, ...], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return gzip_bytes(buf.getvalue().encode())
