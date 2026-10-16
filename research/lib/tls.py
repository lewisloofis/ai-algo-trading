"""TLS context for outbound HTTPS, driven by an explicit env mapping.

The python.org framework build ships an EMPTY system CA store, so the default
SSL context verifies nothing and every HTTPS call fails with a certificate
error (surfacing as URLError). This mirrors the screener's decision order but
reads the passed-in mapping (the runner's in-memory .env.local values), never
os.environ.
"""

from __future__ import annotations

import logging
import ssl
from typing import Mapping

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes")


def ssl_context(env: Mapping[str, str]) -> ssl.SSLContext:
    """Build a verifying SSL context from ``env``.

    - SB_INSECURE_TLS=1/true/yes: verification DISABLED (trusted proxy only).
    - SB_CA_BUNDLE=<path>: use that PEM as the trust store.
    - Otherwise the default context if it has roots, else the certifi bundle.

    Verification stays on in every branch except SB_INSECURE_TLS.
    """
    if env.get("SB_INSECURE_TLS", "").lower() in _TRUTHY:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("SB_INSECURE_TLS set - TLS certificate verification DISABLED "
                       "(use only behind a trusted proxy)")
        return ctx
    ca = env.get("SB_CA_BUNDLE")
    if ca:
        return ssl.create_default_context(cafile=ca)
    ctx = ssl.create_default_context()
    if ctx.get_ca_certs():
        return ctx
    try:
        import certifi
    except ImportError:
        logger.warning("TLS trust store is empty and certifi is not installed - HTTPS "
                       "requests will fail. Install certifi or set SB_CA_BUNDLE.")
        return ctx
    logger.info("TLS trust store empty; falling back to certifi bundle")
    return ssl.create_default_context(cafile=certifi.where())
