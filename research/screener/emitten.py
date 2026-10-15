"""
screener/emitten.py — zero-dependency client for the Stockbit funding endpoints.

Extracted from :mod:`screener.screen_universe` so the eligibility snapshot can
run on the collector box, a 512 MB t4g.nano that has no business installing
pandas.  **Standard library only** — do not add third-party imports here.

The funding gate is the INTERSECTION of two emitten subsectors, day-trade
eligible and trading-limit eligible ("all funding options unlocked").  Both are
verified live (HTTP 200).

Both lists are read from the emitten *classification* endpoint, which also carries
per-company ``tradeable`` / ``company_status`` / ``notations`` / ``uma`` — one source
for eligibility and tradability.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from screener.sb_http import SB_USER_AGENT, urlopen_bytes

logger = logging.getLogger(__name__)

DEFAULT_API_HOST = os.environ.get("SB_API_HOST", "exodus.stockbit.com")

CLASSIFICATION_URL = f"https://{DEFAULT_API_HOST}/emitten/classification/company"
CLASSIFICATION_PARENT_ID = "88"
SUBSECTOR_DAY_TRADE = "1000004658"      # day-trade eligible
SUBSECTOR_TRADING_LIMIT = "1000005139"  # trading-limit eligible

_HEADERS = {
    "User-Agent": SB_USER_AGENT,
    "X-Platform": "android",
    "X-AppVersion": "7.0.0",
    "X-DeviceType": "algo-prototype",
    "Accept-Language": "id",
    "Accept": "application/json",
}


def build_ssl_context() -> ssl.SSLContext:
    """
    SSL context for outbound API requests — the same builder the chartbit path
    uses, so ``SB_INSECURE_TLS`` / ``SB_CA_BUNDLE`` behave identically and an
    empty interpreter trust store (bare macOS venv / python.org build) falls
    back to the certifi bundle instead of failing with CERTIFICATE_VERIFY_FAILED.
    """
    from screener.screen_universe import _build_ssl_context  # local: avoid import cycle

    return _build_ssl_context()


class ClassificationError(RuntimeError):
    """The classification response cannot be trusted as complete (e.g. it is paginated)."""


@dataclass(frozen=True)
class CompanyRecord:
    """One company row from the classification endpoint."""

    symbol: str
    tradeable: bool | None
    """True/False from the 1/0 ``tradeable`` flag; None when absent or not 0/1 (unknown)."""
    company_status: str
    notations: tuple[str, ...]
    uma: bool


def classification_request_url(subsector_id: str) -> str:
    """One subsector per request: comma-separated ids return HTTP 400."""
    query = urllib.parse.urlencode({"ids": subsector_id, "parent_id": CLASSIFICATION_PARENT_ID})
    return f"{CLASSIFICATION_URL}?{query}"


def _tradeable_flag(raw: object) -> bool | None:
    if isinstance(raw, bool) or raw not in (0, 1):
        return None
    return bool(raw)


def _notation(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("notation") or raw.get("code") or json.dumps(raw, sort_keys=True))
    return str(raw)


def _check_complete(group: dict, companies: list) -> None:
    page = group.get("pagination") or {}
    pages = page.get("total_page") or 1
    total = page.get("total_data")
    if pages > 1 or (total is not None and total != len(companies)):
        raise ClassificationError(
            f"classification response is paginated ({pages} pages); pagination not implemented"
        )


def parse_classification(body: dict) -> tuple[CompanyRecord, ...]:
    """
    Flatten every ``data[]`` group's companies. Raises ClassificationError when a group
    says it holds more than was received: silently truncating would shrink the universe.
    """
    records: list[CompanyRecord] = []
    for group in body.get("data") or []:
        companies = group.get("companies") or []
        _check_complete(group, companies)
        for row in companies:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            records.append(CompanyRecord(
                symbol=symbol,
                tradeable=_tradeable_flag(row.get("tradeable")),
                company_status=str(row.get("company_status") or ""),
                notations=tuple(_notation(n) for n in row.get("notations") or []),
                uma=bool(row.get("uma")),
            ))
    return tuple(records)


def merge_tradeable(flags: Iterable[bool | None]) -> bool | None:
    """Combine one symbol's flags across lists: any False wins; True only if all True; else unknown."""
    seen = list(flags)
    if any(f is False for f in seen):
        return False
    return True if seen and all(f is True for f in seen) else None


class EmittenClient:
    """Authenticated reader for the two funding-subsector endpoints."""

    def __init__(self, bearer_token: str) -> None:
        if not bearer_token:
            raise ValueError("bearer_token is required")
        self._token = bearer_token
        self._ssl_ctx = build_ssl_context()

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(
            url, headers={**_HEADERS, "Authorization": f"Bearer {self._token}"}
        )
        raw = urlopen_bytes(req, timeout=15, context=self._ssl_ctx)
        return json.loads(raw.decode())

    def fetch_classification(self, subsector_id: str) -> tuple[CompanyRecord, ...]:
        """
        Per-company records for one subsector.

        **Raises** on failure (HTTP, or an incomplete/paginated response) rather than
        degrading to an empty result.  The screening path in ``screen_universe``
        deliberately swallows errors so a single dead endpoint cannot corrupt an
        intersection; a *recording* path must not, because an empty result would be
        written down as fact and the day cannot be re-recorded.
        """
        return parse_classification(self._get(classification_request_url(subsector_id)))

    def fetch_subsector(self, label: str, subsector_id: str) -> set[str]:
        """Uppercased symbol set for one subsector (raises like fetch_classification)."""
        symbols = {r.symbol for r in self.fetch_classification(subsector_id)}
        logger.info("emitten %s subsector (%s): %d symbols", label, subsector_id, len(symbols))
        return symbols

    def fetch_day_trade(self) -> set[str]:
        return self.fetch_subsector("day-trade", SUBSECTOR_DAY_TRADE)

    def fetch_trading_limit(self) -> set[str]:
        return self.fetch_subsector("trading-limit", SUBSECTOR_TRADING_LIMIT)
