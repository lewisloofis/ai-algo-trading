"""
screener/pit_recorder.py - point-in-time reference-data recorder (plan 23, Phase 1).

Each run writes one append-only snapshot under
``universe/dt=YYYY-MM-DD/pit/``: raw response bodies (gzip), normalised CSV
tables and, LAST, ``manifest.json`` as the commit marker. A failed item writes
nothing; an empty body, HTTP error or incompletely read pagination is a
failure, never an empty "ok" (the plan 18 A-3 lesson). Stdlib only; one GET at
a time with a pause. No token, URL or header ever reaches the manifest or logs.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from screener import pit_tables as T
from screener.emitten import (
    CLASSIFICATION_PARENT_ID, DEFAULT_API_HOST, parse_classification,
)
from screener.sb_http import classify_error

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RECORDER_VERSION = "1.0.0"
S3_PREFIX = "universe"
INDEX_PAGE_LIMIT = 100
MAX_PAGES = 50

# (item name, classification id, parent id, may legitimately be empty)
BOARDS = (
    ("DAYTRADE", "1000004658", "88", False),
    ("TRADINGLIMIT", "1000005139", "88", False),
    ("SUSPENDED", "1000005130", "88", True),
    ("UNSUSPENDED", "1000005131", "88", True),
    ("UMA", "1000005133", "88", True),
    ("NOTASI-KHUSUS", "1000004791", "88", True),
    ("FCA-OUT", "1000005132", "88", True),
    ("HSC", "1000006758", "88", True),
    ("FCA", "3", "89", True),
)
INDEXES = ("LQ45", "IDX30", "IDX80", "KOMPAS100", "JII")
CATALOGUES = (
    ("index_catalog", "/emitten/indexes/catalog"),
    ("special_board", "/emitten/indexes/special-board"),
    ("sectors", "/emitten/sectors"),
)
SECTOR_IDS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "50", "51", "85")


class RecorderError(RuntimeError):
    """An item cannot be trusted as complete. Message carries no URL or token."""


class NoItemsRecordedError(RuntimeError):
    """Every item failed; no manifest is written so a retry can still record the day."""


@dataclass(frozen=True)
class ItemResult:
    name: str
    status: str                       # ok | failed
    raw: bytes | None = None          # bytes to store (uncompressed)
    pages: int = 1
    rows: int = 0
    http_status: int | None = None
    anomalies: tuple[str, ...] = ()
    error: str = ""
    table: str = ""                   # boards | indexes | listed
    table_rows: tuple[dict, ...] = field(default=(), repr=False)


def _url(path: str, **query: str) -> str:
    qs = ("?" + urllib.parse.urlencode(query)) if query else ""
    return f"https://{DEFAULT_API_HOST}{path}{qs}"


def _parse(raw: bytes) -> dict:
    if not raw or not raw.strip():
        raise RecorderError("empty response body")
    try:
        body = json.loads(raw.decode())
    except ValueError:
        raise RecorderError("response is not valid JSON") from None
    if not isinstance(body, dict):
        raise RecorderError("response is not a JSON object")
    return body


def _data_list(body: dict) -> list:
    data = body.get("data")
    if not isinstance(data, list):
        raise RecorderError("response has no data list")
    return data


def _join_pages(raws: list[bytes]) -> bytes:
    return b"[" + b",".join(raws) + b"]"


class _Fetcher:
    """One GET at a time, ``pause`` seconds apart."""

    def __init__(self, client, pause: float, sleep: Callable[[float], None]) -> None:
        self._client, self._pause, self._sleep, self._n = client, pause, sleep, 0

    def get(self, url: str) -> bytes:
        if self._n and self._pause:
            self._sleep(self._pause)
        self._n += 1
        return self._client.get_raw(url)

    @property
    def calls(self) -> int:
        return self._n


def fetch_board(f: _Fetcher, dt: str, name: str, ident: str, parent: str, allow_empty: bool) -> ItemResult:
    raw = f.get(_url("/emitten/classification/company", ids=ident, parent_id=parent))
    body = _parse(raw)
    _data_list(body)
    records = parse_classification(body)  # raises ClassificationError when paginated/incomplete
    anomalies: tuple[str, ...] = ()
    if not records:
        if not allow_empty:
            raise RecorderError("board returned zero rows")
        anomalies = ("empty",)
    rows = tuple(T.board_rows(dt, name, body))
    return ItemResult(f"board_{name}", "ok", raw, 1, len(rows), 200, anomalies, table="boards", table_rows=rows)


def fetch_catalogue(f: _Fetcher, name: str, path: str) -> tuple[ItemResult, dict]:
    raw = f.get(_url(path))
    body = _parse(raw)
    if not body.get("data"):
        raise RecorderError("catalogue has no data")
    return ItemResult(name, "ok", raw, 1, 0, 200), body


def catalog_totals(body: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for section in (body or {}).get("data") or []:
        for item in section.get("items") or []:
            if item.get("symbol") and isinstance(item.get("total_stock"), int):
                out[str(item["symbol"]).upper()] = item["total_stock"]
    return out


def fetch_index(f: _Fetcher, dt: str, name: str, totals: dict[str, int]) -> ItemResult:
    raws: list[bytes] = []
    bodies: list[dict] = []
    page = 1
    while True:
        raw = f.get(_url(f"/emitten/indexes/{name}", page=str(page), limit=str(INDEX_PAGE_LIMIT)))
        body = _parse(raw)
        raws.append(raw)
        bodies.append(body)
        if len(_data_list(body)) < INDEX_PAGE_LIMIT:
            break
        page += 1
        if page > MAX_PAGES:
            raise RecorderError("index paging did not terminate")
    rows = tuple(T.index_rows(dt, name, bodies))
    if not rows:
        raise RecorderError("index returned zero rows")
    anomalies = ("total_stock_mismatch",) if name in totals and totals[name] != len(rows) else ()
    return ItemResult(f"index_{name}", "ok", _join_pages(raws), len(raws), len(rows), 200,
                      anomalies, table="indexes", table_rows=rows)


def fetch_sector(f: _Fetcher, dt: str, sector_id: str) -> ItemResult:
    raws: list[bytes] = []
    bodies: list[dict] = []
    page = 1
    while True:
        raw = f.get(_url(f"/emitten/v3/sector/{sector_id}/company", page=str(page), limit="100"))
        body = _parse(raw)
        _data_list(body)
        raws.append(raw)
        bodies.append(body)
        nxt = int((body.get("pagination") or {}).get("next_page") or 0)
        if nxt == 0:
            break
        if nxt <= page or len(raws) >= MAX_PAGES:
            raise RecorderError("sector pagination did not advance")
        page = nxt
    rows = tuple(T.listed_rows(dt, sector_id, bodies))
    totals = {(b.get("pagination") or {}).get("total_data") for b in bodies} - {None}
    if totals and totals != {sum(len(b["data"]) for b in bodies)}:
        raise RecorderError("sector pagination incomplete: rows read != total_data")
    if not rows:
        raise RecorderError("sector returned zero rows")
    return ItemResult(f"listed_sector_{sector_id}", "ok", _join_pages(raws), len(raws), len(rows), 200,
                      table="listed", table_rows=rows)


def _failed(name: str, exc: BaseException) -> ItemResult:
    status = getattr(exc, "status", None)
    msg = str(exc) if isinstance(exc, RecorderError) else classify_error(exc)
    return ItemResult(name, "failed", http_status=status if isinstance(status, int) else None, error=msg)


def _guard(name: str, fn: Callable[[], ItemResult]) -> ItemResult:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - an item failure must never stop the others
        logger.warning("pit item %s failed: %s", name, _failed(name, exc).error)
        return _failed(name, exc)


def collect_items(client, dt: str, pause: float = 1.0,
                  sleep: Callable[[float], None] = time.sleep) -> tuple[list[ItemResult], int]:
    """Fetch every item, independently. Returns (results, GET count)."""
    f = _Fetcher(client, pause, sleep)
    results: list[ItemResult] = []
    for name, ident, parent, allow_empty in BOARDS:
        results.append(_guard(f"board_{name}", lambda: fetch_board(f, dt, name, ident, parent, allow_empty)))
    catalog_body: dict | None = None
    for name, path in CATALOGUES:
        def one(name=name, path=path):
            nonlocal catalog_body
            res, body = fetch_catalogue(f, name, path)
            if name == "index_catalog":
                catalog_body = body
            return res
        results.append(_guard(name, one))
    totals = catalog_totals(catalog_body)
    for name in INDEXES:
        results.append(_guard(f"index_{name}", lambda: fetch_index(f, dt, name, totals)))
    for sid in SECTOR_IDS:
        results.append(_guard(f"listed_sector_{sid}", lambda: fetch_sector(f, dt, sid)))
    return results, f.calls


def pit_key(as_of: str, rel: str) -> str:
    return f"{S3_PREFIX}/dt={as_of}/pit/{rel}"


def _entry(res: ItemResult, stored: str = "") -> dict:
    entry = {"status": res.status, "http_status": res.http_status, "fetched_at": None,
             "anomaly": list(res.anomalies)}
    if res.status == "ok":
        raw = res.raw or b""
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                     rows=res.rows, pages=res.pages, stored=stored)
    else:
        entry["error"] = res.error
    return entry


def _store_item(store, as_of: str, res: ItemResult) -> tuple[ItemResult, str]:
    """Upload one ok item's raw body. A failed upload turns the item into a failure."""
    try:
        how = store.put(pit_key(as_of, f"{res.name}.json.gz"), T.gzip_bytes(res.raw), "application/gzip")
    except Exception as exc:  # noqa: BLE001
        return _failed(res.name, exc), ""
    return res, how


def _write_tables(store, as_of: str, results: list[ItemResult]) -> dict:
    layout = {"boards": T.BOARD_COLUMNS, "indexes": T.INDEX_COLUMNS, "listed": T.LISTED_COLUMNS}
    out = {}
    for table, columns in layout.items():
        rows = [r for res in results if res.table == table for r in res.table_rows]
        if not rows:
            continue
        data = T.csv_gz(columns, rows)
        try:
            stored = store.put(pit_key(as_of, f"tables/{table}.csv.gz"), data, "application/gzip")
            out[table] = {"status": "ok", "rows": len(rows), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "stored": stored}
        except Exception as exc:  # noqa: BLE001
            out[table] = {"status": "failed", "error": classify_error(exc)}
    return out


def record(client, store, as_of: str, pause: float = 1.0,
           sleep: Callable[[float], None] = time.sleep,
           now: Callable[[], _dt.datetime] | None = None) -> dict | None:
    """
    Record one day. Returns the manifest, or None when the day already has one
    (nothing is fetched or written). Raises NoItemsRecordedError when every item
    failed, leaving no manifest so a later retry can still record the day.
    """
    manifest_key = pit_key(as_of, "manifest.json")
    if store.exists(manifest_key):
        logger.info("pit %s: manifest already present; nothing to do", as_of)
        return None
    clock = now or (lambda: _dt.datetime.now(_dt.timezone.utc))
    fetched = clock().isoformat()
    results, calls = collect_items(client, as_of, pause, sleep)
    items: dict[str, dict] = {}
    kept: list[ItemResult] = []
    for res in results:
        how = ""
        if res.status == "ok":
            res, how = _store_item(store, as_of, res)
        items[res.name] = {**_entry(res, how), "fetched_at": fetched}
        if res.status == "ok":
            kept.append(res)
    if not kept:
        raise NoItemsRecordedError("every PIT item failed; no manifest written")
    manifest = {
        "schema_version": SCHEMA_VERSION, "recorder_version": RECORDER_VERSION,
        "as_of": as_of, "recorded_at": clock().isoformat(), "get_calls": calls,
        "items": items, "tables": _write_tables(store, as_of, kept),
    }
    body = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    store.put(manifest_key, body, "application/json")  # LAST: the commit marker
    failed = [n for n, e in items.items() if e["status"] != "ok"]
    logger.info("pit %s: %d/%d items ok, %d GETs, failed=%s", as_of, len(kept), len(items), calls, failed)
    return manifest
