"""
foreign_flow/client.py - one-symbol, one-range reader for the foreign/domestic history endpoint.

``GET https://{SB_API_HOST|exodus.stockbit.com}/order-trade/foreign-domestic/historical
      ?symbols=SYM&market_type=MARKET_TYPE_REGULER&from=YYYY-MM-DD&to=YYYY-MM-DD``

The server refuses ranges longer than ~1 year, so callers ask for one calendar year per call.
An empty ``historical_net`` list (HTTP 200) means "no data for that range" and is not an error.

Security: the token is only ever placed in the Authorization header. Errors raised here come
from :mod:`screener.sb_http` (query string and headers stripped) or carry no response body.
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from typing import Callable

import pandas as pd

from screener.emitten import _HEADERS, build_ssl_context
from screener.sb_http import urlopen_bytes

PATH = "/order-trade/foreign-domestic/historical"
MARKET_TYPE = "MARKET_TYPE_REGULER"
TIMEOUT_S = 20

FLOW_FIELDS = ("net_foreign", "foreign_buy", "foreign_sell", "foreign_flow", "net_lot",
               "average_price", "percentage_foreign_value", "percentage_domestic_value")
COLUMNS = ["date", *FLOW_FIELDS[:5], "net_frequency", *FLOW_FIELDS[5:], "sb_close"]

HttpGet = Callable[[str, dict], bytes]
"""(url, headers) -> response bytes. Injected in tests; the default uses urlopen_bytes."""


def api_host() -> str:
    return os.environ.get("SB_API_HOST") or "exodus.stockbit.com"


def request_url(symbol: str, date_from: str, date_to: str) -> str:
    query = urllib.parse.urlencode({
        "symbols": symbol, "market_type": MARKET_TYPE, "from": date_from, "to": date_to,
    })
    return f"https://{api_host()}{PATH}?{query}"


def _raw(field: object) -> object:
    """The ``.raw`` of a ``{raw, formatted}`` field; a bare scalar is accepted as-is."""
    if isinstance(field, dict):
        return field.get("raw")
    return field


def _num(field: object) -> float:
    value = _raw(field)
    if value is None or isinstance(value, bool):
        return math.nan
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def _int_or_none(field: object) -> int | None:
    """net_frequency arrives as a string in JSON (e.g. "-62441")."""
    num = _num(field)
    return None if math.isnan(num) else int(num)


def parse_response(body: object) -> pd.DataFrame:
    """Rows of ``data.historical_net`` joined with ``data.historical_price`` close as ``sb_close``.

    Missing fields become NaN/NA; rows without a date are dropped; never raises on a
    well-formed JSON object.
    """
    data = body.get("data") if isinstance(body, dict) else None
    data = data if isinstance(data, dict) else {}
    closes: dict[str, float] = {}
    for row in data.get("historical_price") or []:
        if isinstance(row, dict) and row.get("date"):
            closes[str(row["date"])] = _num(row.get("close"))
    records = []
    for row in data.get("historical_net") or []:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        rec: dict = {"date": str(row["date"])}
        for name in FLOW_FIELDS[:5]:
            rec[name] = _num(row.get(name))
        rec["net_frequency"] = _int_or_none(row.get("net_frequency"))
        for name in FLOW_FIELDS[5:]:
            rec[name] = _num(row.get(name))
        rec["sb_close"] = closes.get(rec["date"], math.nan)
        records.append(rec)
    return normalise(pd.DataFrame(records, columns=COLUMNS))


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical dtypes: date as datetime64, net_frequency as nullable Int64, rest float64."""
    out = df.reindex(columns=COLUMNS).copy()
    out["date"] = pd.to_datetime(out["date"])
    out["net_frequency"] = pd.to_numeric(out["net_frequency"]).astype("Int64")
    for col in COLUMNS:
        if col not in ("date", "net_frequency"):
            out[col] = pd.to_numeric(out[col]).astype("float64")
    return out.reset_index(drop=True)


def _default_http_get() -> HttpGet:
    ctx = build_ssl_context()

    def get(url: str, headers: dict) -> bytes:
        return urlopen_bytes(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S, context=ctx)

    return get


class ForeignFlowClient:
    def __init__(self, bearer_token: str, http_get: HttpGet | None = None) -> None:
        if not bearer_token:
            raise ValueError("bearer_token is required")
        self._token = bearer_token
        self._http_get = http_get or _default_http_get()

    def __repr__(self) -> str:  # never expose the token
        return "ForeignFlowClient(token=***)"

    def fetch(self, symbol: str, date_from: str, date_to: str) -> pd.DataFrame:
        """One call. Raises SBHttpError / URLError on transport failure; ValueError on non-JSON."""
        headers = {**_HEADERS, "Authorization": f"Bearer {self._token}"}
        raw = self._http_get(request_url(symbol, date_from, date_to), headers)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(f"non-JSON response for {symbol} {date_from}..{date_to}") from None
        return parse_response(body)
