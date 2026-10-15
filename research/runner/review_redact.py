"""Redaction of the reviewer's facts before anything leaves the machine.

Builds on ``runner.redact`` (secret env values, JWTs, bearer tokens, webhook URLs, long opaque blobs)
and additionally strips account numbers, order IDs and broker IDs. Applied to every STRING in the facts
(numbers stay numbers, so a rupiah amount is never mistaken for an id) and to every value under a
sensitive KEY. The facts builder never puts these in on purpose; this is the second line."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from runner.redact import MASK, redact

SENSITIVE_KEY = re.compile(
    r"(?i)(^|_)(acc|acn|account|acct|order_?id|ord_?id|ord_?no|order_?no|broker_?id|broker_?order|client_?id|"
    r"player_?id|nik|npwp|phone|sid|sre|token|secret|password|authorization)(_?(no|number|id))?($|_)")
_CONTEXT = re.compile(
    r"(?i)\b(acc(?:ount)?(?:[ _-]?(?:no|number|id))?|acn|acct|order[ _-]?id|ord(?:er)?[ _-]?no|"
    r"broker[ _-]?(?:id|order[ _-]?id)|client[ _-]?id|player[ _-]?id)\b(\s*[:=#]?\s*)([^\s,;|)\]]+)")
_PATTERNS = (
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),   # UUID order ids
    re.compile(r"\b[A-Za-z]{2,}-?\d{2,}[A-Za-z]{0,3}\b"),    # TEST001L style account ids
    re.compile(r"\b[A-Za-z]{2,}-[A-Za-z]{2,}\d+\b"),                         # SYNTH-ACC1
    re.compile(r"\b\d{10,}\b"),                                              # long digit runs: order ids, NIK, phone
    re.compile(r"\+62[\d\s-]{7,}"),
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),
)
# Symbols are 4 letters and never match the account patterns; ISO dates (2026-10-15) are exempt from the
# letters+digits pattern by construction (they start with digits).


def redact_text(text: str, env: Optional[Mapping[str, str]] = None) -> str:
    out = redact(str(text), env)
    out = _CONTEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    for pat in _PATTERNS:
        out = pat.sub(MASK, out)
    return out


def sanitize(obj: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """A deep copy of ``obj`` (dicts, lists, strings, numbers) with every string redacted and every
    value under a sensitive key masked. Never mutates its input."""
    if isinstance(obj, Mapping):
        return {str(k): (MASK if SENSITIVE_KEY.search(str(k)) and obj[k] not in (None, "", [], {}) else sanitize(obj[k], env))
                for k in obj}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, env) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, env)
    return obj
