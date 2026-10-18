"""
dashboard/summary.py — the small ``summary.json`` read by the portfolio overview page.

Schema: overview/schema/summary.schema.json (``source: "algo"``). Written next to ``index.html`` by
``site.build_site``. Pure functions over the already-gathered ``SiteData``; no network.

What is never copied: account numbers, tokens, or any broker-report field other than ``status``,
``as_of`` and, for a mismatch, each position's ``symbol`` and lot counts. Free text from the report
(``explanation``, ``errors``) is not copied either; alert wording is built here.

Ticker ``state`` rule (the bot is long-only):
  1. Look only at strategies whose stage is ``live`` or ``live_candidate`` (real money); a paper
     strategy's chain state is not a real holding and never drives ``state``.
  2. Each such strategy's shortlist chain state maps: "IN POSITION (exit in N session(s))" ->
     ``exiting`` when N <= EXITING_WITHIN_SESSIONS, else ``in_position``; "ENTER..." -> ``enter``;
     "FLAT..." -> ``flat``; anything else -> unknown.
  3. With several strategies on one ticker the most active wins: exiting > in_position > enter > flat;
     on equal state the smallest ``exit_in_sessions`` is kept.
  4. A real holding in the ledger forces ``in_position`` when step 3 gave ``flat`` or unknown
     (the broker position is the truth; we do not know its exit time).
  5. No live strategy and no holding -> ``state`` is omitted.

``pnl.day/mtd/ytd`` are REALISED profit only, summed from ledger closings by sell date (WIB);
``realised_total`` is the ledger's lifetime figure. ``nav``/``cash`` are not known to the dashboard
(the sleeve size is not recorded), so they are null.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from dashboard import config, health, recon, strategy_status
from dashboard.manual_trades import SHARES_PER_LOT

SCHEMA_VERSION = 1
SUMMARY_NAME = "summary.json"
STALE_PRICE_DAYS = 5
"""A last close older than this many calendar days gets an info alert."""
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{1,9}$")  # same as the schema's ticker pattern
EXITING_WITHIN_SESSIONS = 1
"""A live position this many sessions (or fewer) from its time exit counts as 'exiting'."""

_ACTIVITY = ("flat", "enter", "in_position", "exiting")
_REAL_MONEY_STAGES = ("live", "live_candidate")
_EXIT_IN_RE = re.compile(r"exit in (\d+)", re.IGNORECASE)
WIB = timezone(timedelta(hours=config.WIB_OFFSET_HOURS))


def parse_state(raw: str) -> tuple[str | None, int | None]:
    """(state, exit_in_sessions) from a shortlist chain-state string; (None, None) if unrecognised."""
    low = (raw or "").strip().lower()
    if low.startswith("in position"):
        m = _EXIT_IN_RE.search(low)
        n = int(m.group(1)) if m else None
        return ("exiting" if n is not None and n <= EXITING_WITHIN_SESSIONS else "in_position"), n
    if low.startswith("enter"):
        return "enter", None
    if low.startswith("flat"):
        return "flat", None
    return None, None


def _slug(symbol: str, family: str, hold: int) -> str:
    return f"{symbol}-{family}-h{hold}".lower().replace("_", "-")


def _live_symbols_state(strategies: list[dict]) -> tuple[str | None, int | None]:
    best, exit_in = None, None
    for s in strategies:
        st = s.get("_state")
        if not st:
            continue
        n = s.get("_exit_in")
        if best is None or _ACTIVITY.index(st) > _ACTIVITY.index(best):
            best, exit_in = st, n
        elif st == best and n is not None and (exit_in is None or n < exit_in):
            exit_in = n
    return best, exit_in


def _strategies(d) -> dict[str, list[dict]]:
    """symbol -> strategy entries (with private _state/_exit_in for the state rule)."""
    si = d.strategy_inputs
    specs = getattr(si, "specs", None) if si else None
    if not specs:
        return {}
    shortlist = getattr(si, "shortlist", None)
    stages = {r.name: r.stage for r in strategy_status.stage_rows(specs, d.paper_tests, d.online, shortlist)}
    links = {r.slug: r.detail_page for r in d.rows if r.detail_page}
    out: dict[str, list[dict]] = {}
    for spec in specs.values():
        stage = stages.get(spec.strategy_id)
        if stage is None:
            continue
        for sym in spec.symbols:
            entry = {"id": spec.strategy_id, "family": spec.family, "hold_days": int(spec.hold), "stage": stage}
            link = links.get(_slug(sym, spec.family, int(spec.hold)))
            if link:
                entry["link"] = link
            row = strategy_status._shortlist_row(shortlist, spec)
            if stage in _REAL_MONEY_STAGES and row:
                entry["_state"], entry["_exit_in"] = parse_state(str(row.get("state", "")))
            out.setdefault(sym, []).append(entry)
    return out


def _last_verdicts(d) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in d.rows:
        if r.stats is not None:
            out.setdefault(r.cell.symbol, r.verdict.headline)
    return out


def _positions(d) -> list[dict]:
    out = []
    for p in d.ledger.positions:
        closes = d.closes.get(p.symbol) or []
        last = closes[-1][1] if closes else None
        mv = last * p.lots * SHARES_PER_LOT if last is not None else None
        out.append({
            "ticker": p.symbol, "lots": p.lots, "shares": p.lots * SHARES_PER_LOT,
            "avg_price": round(p.avg_cost, 2), "last_price": last,
            "market_value": round(mv) if mv is not None else None,
            "cost_basis": round(p.cost_idr),
            "unrealized_pnl": round(mv - p.cost_idr) if mv is not None else None,
            "weight": None,
        })
    return out


def _tickers(d, held: set[str]) -> dict[str, dict]:
    strategies, verdicts = _strategies(d), _last_verdicts(d)
    out = {}
    for sym in sorted(set(strategies) | held):
        entries = strategies.get(sym, [])
        state, exit_in = _live_symbols_state(entries)
        if sym in held and state in (None, "flat"):
            state, exit_in = "in_position", None
        t: dict = {"strategies": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]}
        if state:
            t["state"] = state
        if exit_in is not None and state in ("in_position", "exiting"):
            t["exit_in_sessions"] = exit_in
        if sym in verdicts:
            t["last_verdict"] = verdicts[sym]
        out[sym] = t
    return out


def _now(d) -> datetime:
    """Build time as an aware datetime (a naive one is taken as UTC)."""
    return d.now if d.now.tzinfo else d.now.replace(tzinfo=timezone.utc)


def _pnl(d) -> dict:
    today = _now(d).astimezone(WIB).date().isoformat()
    rows = [(c.sell.day, c.pnl_idr) for c in d.ledger.closings]
    total = lambda pred: round(sum(p for day, p in rows if pred(day)))  # noqa: E731
    return {"day": total(lambda x: x == today), "mtd": total(lambda x: x[:7] == today[:7]),
            "ytd": total(lambda x: x[:4] == today[:4]), "realised_total": round(d.ledger.realised_pnl_idr)}


RECON_FORMAT_ALERT = {"severity": "warning", "message": "Broker reconciliation report has an unexpected format."}


def _recon(d) -> tuple[dict, list[dict]]:
    """({status, as_of}, alerts). Reads only status, as_of, and per-position symbol + lot counts.

    Anything else in the report (account label, explanations, errors) is never copied. A malformed
    report gives status "error" and a fixed alert; an invalid as_of or symbol is not copied.
    """
    path = recon.newest_report_path(d.options.runs_dir)
    if path is None:
        return {"status": "not_checked"}, []
    try:
        doc = recon.load_report(path)
    except recon.ReconReportError:
        return {"status": "error"}, [{"severity": "critical", "message": "Broker reconciliation report is unreadable."}]
    as_of = None
    try:
        datetime.fromisoformat(doc["as_of"])
        as_of = doc["as_of"]
    except (KeyError, TypeError, ValueError):
        pass
    try:
        alerts = []
        for p in doc["positions"]:
            if p.get("status") == "match":
                continue
            sym = p["symbol"]
            msg = ("Positions do not match the broker: expected "
                   f"{recon._lots(p['expected_lots'])}, broker {recon._lots(p['broker_lots'])}.")
            alert = {"severity": "critical", "message": msg}
            if isinstance(sym, str) and TICKER_RE.match(sym):
                alert["ticker"] = sym
            alerts.append(alert)
        if doc.get("errors"):
            alerts.append({"severity": "warning", "message": "Broker reconciliation reported errors."})
    except (KeyError, TypeError, ValueError, AttributeError):
        out = {"status": "error"}
        if as_of:
            out["as_of"] = as_of
        return out, [dict(RECON_FORMAT_ALERT)]
    out = {"status": doc["status"]}
    if as_of:
        out["as_of"] = as_of
    return out, alerts


def _stale_price_alerts(d) -> list[dict]:
    today = _now(d).astimezone(WIB).date()
    out = []
    for p in d.ledger.positions:
        closes = d.closes.get(p.symbol) or []
        if not closes:
            continue
        try:
            age = (today - date.fromisoformat(closes[-1][0][:10])).days
        except ValueError:
            continue
        if age > STALE_PRICE_DAYS:
            out.append({"severity": "info", "ticker": p.symbol,
                        "message": f"Last price for {p.symbol} is {age} days old."})
    return out


def _health_alerts(d) -> list[dict]:
    sev = {health.RED: "critical", health.AMBER: "warning"}
    return [{"severity": sev[r.state], "message": f"{r.name}: {r.state}"} for r in d.health
            if r.state in sev and r.name != recon.ROW_NAME]


def _kill_switch_alerts(d) -> list[dict]:
    runs = d.online.runs if d.online else ()
    return [{"severity": "critical", "message": f"Kill switch is on for {r.strategy_id}."}
            for r in runs if r.kill_switch and r.state == "online"]


def build_summary(d) -> dict:
    now = _now(d)
    positions = _positions(d)
    recon_doc, recon_alerts = _recon(d)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(WIB).isoformat(timespec="seconds"),
        "source": "algo",
        "currency": "IDR",
        "nav": None,
        "cash": None,
        "positions": positions,
        "tickers": _tickers(d, {p["ticker"] for p in positions}),
        "pnl": _pnl(d),
        "recon": recon_doc,
        "alerts": recon_alerts + _health_alerts(d) + _kill_switch_alerts(d) + _stale_price_alerts(d),
        "links": {"root": "index.html"},
    }


def write_summary(d, out_dir: str) -> str:
    path = os.path.join(out_dir, SUMMARY_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(build_summary(d), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written file
    return path
