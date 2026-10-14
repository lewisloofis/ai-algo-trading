"""
screener/emitten.py — zero-dependency client for the Stockbit funding endpoints.

Extracted from :mod:`screener.screen_universe` so the eligibility snapshot can
run on the collector box, a 512 MB t4g.nano that has no business installing
pandas.  **Standard library only** — do not add third-party imports here.

The funding gate is the INTERSECTION of two emitten subsectors, day-trade
eligible and trading-limit eligible ("all funding options unlocked").  Both are
verified live (HTTP 200).
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request

from screener.sb_http import SB_USER_AGENT, urlopen_bytes

logger = logging.getLogger(__name__)

DEFAULT_API_HOST = os.environ.get("SB_API_HOST", "exodus.stockbit.com")

EMITTEN_BASE_URL = f"https://{DEFAULT_API_HOST}/emitten/v3/sector/88/subsector"
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
    SSL context for outbound API requests.

    ``SB_INSECURE_TLS=1`` disables verification (only behind a trusted MITM
    proxy); ``SB_CA_BUNDLE=<path>`` supplies custom roots.  Verification stays
    ON in every branch but the first.
    """
    if os.environ.get("SB_INSECURE_TLS", "").lower() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("SB_INSECURE_TLS set — TLS verification DISABLED")
        return ctx
    ca = os.environ.get("SB_CA_BUNDLE")
    if ca:
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


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

    def fetch_subsector(self, label: str, subsector_id: str) -> set[str]:
        """
        Uppercased symbol set for one subsector.

        **Raises** on failure rather than degrading to an empty set.  The
        screening path in ``screen_universe`` deliberately swallows errors so a
        single dead endpoint cannot corrupt an intersection; a *recording* path
        must not, because an empty result would be written down as fact and the
        day cannot be re-recorded.
        """
        url = f"{EMITTEN_BASE_URL}/{subsector_id}/company"
        body = self._get(url)
        rows = body.get("data") or []
        symbols = {r["symbol"].strip().upper() for r in rows if r.get("symbol")}
        logger.info("emitten %s subsector (%s): %d symbols", label, subsector_id, len(symbols))
        return symbols

    def fetch_day_trade(self) -> set[str]:
        return self.fetch_subsector("day-trade", SUBSECTOR_DAY_TRADE)

    def fetch_trading_limit(self) -> set[str]:
        return self.fetch_subsector("trading-limit", SUBSECTOR_TRADING_LIMIT)
