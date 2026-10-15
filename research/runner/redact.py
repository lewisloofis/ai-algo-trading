"""Alert/log redaction. Nothing that looks like a credential may leave this process."""

from __future__ import annotations

import re
from typing import Mapping, Optional

SECRET_ENV_NAMES = (
    "SB_ACCESS_TOKEN", "SB_REFRESH_TOKEN", "SB_BEARER_TOKEN", "SB_TRADING_ACCESS_TOKEN",
    "SB_PASSWORD", "SB_USERNAME", "SB_ACCOUNT_NO", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "RUNNER_ALERT_WEBHOOK_URL", "ALGO_ALERT_WEBHOOK_URL", "ALGO_CONFIRM",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
)
MASK = "[REDACTED]"
_MIN_SECRET_LEN = 4
_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"),   # JWT
    re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}"),                                  # Telegram bot token
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"(?i)https?://[^\s]*(?:bot\d+:|token=|hooks\.|webhook)[^\s]*"),   # webhook-ish URLs
    re.compile(r"\b[A-Za-z0-9_\-+=]{40,}\b"),                                     # long opaque blobs
)


def redact(text: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Replace every known secret value (exact match) and every secret-shaped string."""
    out = str(text)
    if env:
        values = sorted({env[n] for n in SECRET_ENV_NAMES if env.get(n) and len(env[n]) >= _MIN_SECRET_LEN},
                        key=len, reverse=True)
        for v in values:
            out = out.replace(v, MASK)
    for pat in _PATTERNS:
        out = pat.sub(MASK, out)
    return out
