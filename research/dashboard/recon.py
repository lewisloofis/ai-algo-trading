"""
dashboard/recon.py — the Overview's "Positions match the broker" row.

The Go tool ``account-recon --stores`` compares what the bot's stores, the owner's manual trades and
the broker's trade history say the account holds with what the broker reports, and writes
``runs/recon/<date>.json``. The broker is the source of truth. This module reads the newest such file
and turns it into one health row: green when the positions match, red (with every per-symbol
difference) when they do not, and "Not checked. Run: <command>" when no report exists.

The report holds no token and no account number, only a short label.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta, timezone

from dashboard import config
from dashboard.health import AMBER, GREEN, RED, HealthRow, not_checked_row

ROW_NAME = "Positions match the broker"
STATUSES = ("match", "mismatch")


class ReconReportError(ValueError):
    """The newest reconciliation file cannot be trusted. The row goes red; it is never read as a match."""


def newest_report_path(runs_dir: str) -> str | None:
    """The newest ``runs/recon/<date>.json`` (file names are ISO dates, so name order is date order)."""
    found = sorted(glob.glob(os.path.join(runs_dir, config.RECON_DIRNAME, "*.json")))
    return found[-1] if found else None


def load_report(path: str) -> dict:
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise ReconReportError(f"{name} is not readable JSON ({exc})") from exc
    if not isinstance(doc, dict) or doc.get("status") not in STATUSES:
        raise ReconReportError(f"{name} has no valid status (want 'match' or 'mismatch')")
    if not isinstance(doc.get("positions"), list) or not isinstance(doc.get("as_of"), str):
        raise ReconReportError(f"{name} is missing its positions or as_of")
    return doc


def _lots(n) -> str:
    n = int(n) if float(n).is_integer() else n
    return f"{n} lot" + ("" if n == 1 else "s")


def _differences(doc: dict) -> list[str]:
    out = []
    for p in doc["positions"]:
        if p.get("status") == "match":
            continue
        out.append(f"{p['symbol']}: expected {_lots(p['expected_lots'])}, broker {_lots(p['broker_lots'])}"
                   + (f" ({p['explanation']})" if p.get("explanation") else ""))
    return out


def _holdings(doc: dict) -> str:
    held = [f"{p['symbol']} {_lots(p['broker_lots'])}" for p in doc["positions"] if p.get("broker_lots")]
    return ", ".join(held) if held else "nothing held"


def _trade_check(doc: dict) -> str:
    tc = doc.get("trade_check") or {}
    if not tc.get("checked"):
        why = tc.get("error") or "no reason recorded"
        return f" The trade-by-trade check was not done ({why})."
    bits = [f"{tc.get('matched', 0)} matched", f"{tc.get('broker_only', 0)} placed by hand in the app",
            f"{tc.get('store_only', 0)} recorded by the bot but not at the broker"]
    if tc.get("pending_history"):
        bits.append(f"{tc['pending_history']} awaiting broker history")
    extra = f"; {tc['fee_gaps_flagged']} fee gap(s) over Rp 0.50" if tc.get("fee_gaps_flagged") else ""
    return f" Trades checked against the broker's history: {', '.join(bits)}{extra}."


def _age_days(doc: dict, now: datetime) -> int | None:
    try:
        as_of = datetime.fromisoformat(doc["as_of"])
    except ValueError:
        return None
    today = (now.astimezone(timezone.utc) + timedelta(hours=config.WIB_OFFSET_HOURS)).date()
    return (today - as_of.date()).days


def recon_row(runs_dir: str, now: datetime) -> HealthRow:
    path = newest_report_path(runs_dir)
    if path is None:
        return not_checked_row(ROW_NAME, "no broker reconciliation has been run yet", config.RECON_RUN_HINT)
    try:
        doc = load_report(path)
    except ReconReportError as exc:
        return HealthRow(ROW_NAME, RED, f"The reconciliation report could not be read: {exc}. "
                         f"Re-run it: {config.RECON_RUN_HINT}", run_hint=config.RECON_RUN_HINT)

    when = doc["as_of"][:16].replace("T", " ") + " WIB"
    errors = [str(e) for e in doc.get("errors") or []]
    diffs = _differences(doc)
    if doc["status"] != "match" or errors:
        parts = []
        if diffs:
            parts.append("Positions do NOT match the broker. " + "; ".join(diffs) + ".")
        parts += [e + "." if not e.endswith(".") else e for e in errors]
        return HealthRow(ROW_NAME, RED, f"{' '.join(parts)} Checked {when}.{_trade_check(doc)}")

    age = _age_days(doc, now)
    summary = (f"Yes: the bot's records agree with the broker. The broker holds {_holdings(doc)}. "
               f"Checked {when}.{_trade_check(doc)}")
    if (doc.get("trade_check") or {}).get("pending_history"):
        return HealthRow(ROW_NAME, AMBER, f"{summary} Today's trade(s) are awaiting the broker's history, which lists "
                         f"them from the next trading day. Re-run then: {config.RECON_RUN_HINT}",
                         run_hint=config.RECON_RUN_HINT)
    if age is not None and age > config.RECON_STALE_DAYS:
        return HealthRow(ROW_NAME, AMBER, f"Matched when last checked, {when}, but that was {age} days ago. "
                         f"Check again: {config.RECON_RUN_HINT}", run_hint=config.RECON_RUN_HINT)
    return HealthRow(ROW_NAME, GREEN, summary)


__all__ = ["ROW_NAME", "ReconReportError", "load_report", "newest_report_path", "recon_row"]
