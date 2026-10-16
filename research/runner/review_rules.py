"""Daily ops review, rules half: facts and severity-tagged flags from LOCAL artefacts only.

Deterministic, no network, no secrets: runner log, the day's state file, live and paper stores, recon
output, paper scorecards, the cost report, token expiry (durations only, decoded from the in-memory env).
The LLM (runner.review_summary) only ever restates what this module produced, after runner.review_redact.

Every source is read defensively: an unreadable source becomes a WARN flag, never an exception."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from lib.market_calendar import CALENDAR
from runner import liveorders, token_expiry
from runner.config import ACCESS_TOKEN_ENV, TRADING_TOKEN_ENV, Config
from runner.heartbeat import assess_heartbeat, latest_status
from strategy_fit import paper_stores as ps

ALERT, WARN, INFO = "ALERT", "WARN", "INFO"
SEVERITY_ORDER = {ALERT: 0, WARN: 1, INFO: 2}

FILL_DEVIATION_WARN_BPS = 100.0      # |avg fill - order (decision) price| / price
FILL_DEVIATION_ALERT_BPS = 300.0
TOKEN_WARN_HOURS = 24.0
CAP_WARN_FRACTION = 0.9              # live bought / armed cap
COST_REPORT_STALE_DAYS = 7
MAX_LOG_LINES = 10
MAX_LINE = 200
# A position opened on day E under hold=H sessions is planned out on E+H sessions: still open after the
# close of that day is WARN, one more session is ALERT.
OK_SESSION_STATUS = ("done",)
BAD_SESSION_STATUS = ("refused", "failed", "died", "error")


def flag(severity: str, code: str, text: str) -> Dict[str, str]:
    return {"severity": severity, "code": code, "text": text}


def _safe(flags: List[dict], label: str, fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a broken source is a flag, not a crash
        flags.append(flag(WARN, "source_unreadable", f"could not read {label} ({type(exc).__name__})"))
        return default


def _registry(cfg: Config):
    if cfg.registry is not None:
        return cfg.registry
    from strategy_fit.emitter_specs import SPECS
    return SPECS


# ---- sources -----------------------------------------------------------------------------------

def read_log(cfg: Config, day: date) -> Dict[str, Any]:
    """ALERT / WARN lines of the day from runner.log (already redacted when written)."""
    out: Dict[str, Any] = {"alerts": [], "warns": [], "n_alert": 0, "n_warn": 0}
    try:
        lines = cfg.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        if not ln.startswith(str(day)):
            continue
        for tag, key in (("[ALERT]", "alerts"), ("[WARN]", "warns")):
            if tag in ln[:40]:
                out["n_" + key[:-1]] += 1
                if len(out[key]) < MAX_LOG_LINES:
                    out[key].append(ln.split(tag, 1)[1].strip()[:MAX_LINE])
    return out


def token_facts(cfg: Config, now_epoch: float) -> List[dict]:
    rows = []
    for name in (ACCESS_TOKEN_ENV, TRADING_TOKEN_ENV):
        val = cfg.env.get(name)
        if not val:
            if name != TRADING_TOKEN_ENV:
                rows.append({"name": name, "state": "unset", "hours_left": None})
            continue
        left = token_expiry.seconds_left(val, now_epoch)
        if left is None:
            rows.append({"name": name, "state": "unknown", "hours_left": None})
        elif left <= 0:
            rows.append({"name": name, "state": "expired", "hours_left": round(left / 3600, 1)})
        else:
            rows.append({"name": name, "state": "fresh", "hours_left": round(left / 3600, 1)})
    return rows


def _entry_days(events: Sequence[tuple]) -> Dict[str, dict]:
    """events: (day, symbol, signed lots) oldest first -> {symbol: {lots, entry_day}} for open positions."""
    net: Dict[str, int] = {}
    entry: Dict[str, str] = {}
    for d, sym, lots in events:
        before = net.get(sym, 0)
        after = before + lots
        net[sym] = after
        if before == 0 and after != 0:
            entry[sym] = d
        if after == 0:
            entry.pop(sym, None)
    return {s: {"lots": n, "entry_day": entry.get(s)} for s, n in net.items() if n != 0}


def open_positions(cfg: Config, sid: str) -> Dict[str, List[dict]]:
    live = liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid))
    live_ev = [(o.key[:10], o.symbol, o.filled_lots if o.side == "buy" else -o.filled_lots) for o in live if o.filled_lots]
    paper = ps.read_fills(ps.list_stores(cfg.runs_dir, sid))
    paper_ev = [(f.store[:10], f.symbol, f.lots if f.side == "buy" else -f.lots) for f in paper]
    return {sink: [{"symbol": s, **v} for s, v in sorted(_entry_days(ev).items())]
            for sink, ev in (("live", live_ev), ("paper", paper_ev))}


def fill_rows(cfg: Config, sid: str, day: date) -> List[dict]:
    rows = []
    for o in liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid, day)):
        if o.rejected or not o.filled_lots or not o.price_idr:
            continue
        dev = (o.avg_fill_idr - o.price_idr) / o.price_idr * 1e4
        rows.append({"strategy": sid, "symbol": o.symbol, "side": o.side, "lots": o.filled_lots,
                     "decision_price": o.price_idr, "avg_fill": round(o.avg_fill_idr, 2), "dev_bps": round(dev, 1)})
    return rows


def recon_facts(cfg: Config) -> Optional[dict]:
    from dashboard import recon
    path = recon.newest_report_path(str(cfg.runs_dir))
    if path is None:
        return None
    doc = recon.load_report(path)
    diffs = [{"symbol": p.get("symbol"), "expected_lots": p.get("expected_lots"), "broker_lots": p.get("broker_lots"),
              "status": p.get("status")} for p in doc["positions"] if p.get("status") != "match"]
    tc = doc.get("trade_check") or {}
    return {"file": Path(path).name, "status": doc["status"], "as_of": doc["as_of"], "differences": diffs,
            "errors": [str(e)[:MAX_LINE] for e in doc.get("errors") or []],
            "trade_check": {k: tc.get(k) for k in ("checked", "matched", "broker_only", "store_only",
                                                    "pending_history", "fee_gaps_flagged")}}


def scorecard_facts(cfg: Config, specs: Sequence) -> List[dict]:
    from strategy_fit.paper_scorecard import DEFAULT_DATA_DIR, build_scorecard
    data_dir = cfg.daily_dir if cfg.daily_dir.exists() else DEFAULT_DATA_DIR
    rows = []
    for spec in specs:
        sc = build_scorecard(spec, cfg.runs_dir, data_dir)
        rows.append({"strategy": spec.strategy_id, "n_trips": sc.n_trips, "verdict": sc.verdict,
                     "mean_net_bps": None if sc.mean_net_bps is None else round(sc.mean_net_bps, 1),
                     "edge_over_null_bps": None if sc.edge_over_null_bps is None else round(sc.edge_over_null_bps, 1)})
    return rows


def cost_report_facts(cfg: Config) -> Optional[dict]:
    path = cfg.research_dir / "out_cost_report" / "cost_report.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    agg = doc.get("aggregates") or {}
    fills = {src: (a.get("overall", {}).get("slippage_decision_bps") or {}) for src, a in agg.items()}
    return {"generated": str(doc.get("generated", ""))[:19],
            "n_fills": {s: m.get("n") for s, m in fills.items()},
            "mean_slippage_decision_bps": {s: (None if m.get("mean") is None else round(m["mean"], 1))
                                           for s, m in fills.items()},
            "report_flags": len(doc.get("flags") or [])}


# ---- facts + flags -----------------------------------------------------------------------------

def build_facts(cfg: Config, day: date, state: dict, now_epoch: float, *, steps: Sequence[tuple] = (),
                live_summary: Optional[Sequence[str]] = None, paper_summary: Optional[Sequence[str]] = None) -> dict:
    flags: List[dict] = []
    reg = _registry(cfg)
    specs = sorted(reg.values(), key=lambda s: s.strategy_id)
    strategies = state.get("strategies", {})

    # preflight / token rotation / steps
    pre = state.get("preflight")
    if pre is None:
        flags.append(flag(ALERT, "no_preflight", "no preflight recorded today: the morning run did not happen"))
    elif not pre.get("ok"):
        flags.append(flag(ALERT, "preflight_failed", "preflight failed; the day was skipped"))
    rot = state.get("token_rotation") or {}
    rot_pc = state.get("token_rotation_post_close") or {}
    for label, r in (("token rotation", rot), ("token rotation (post-close)", rot_pc)):
        for action in r.get("actions") or []:
            flags.append(flag(ALERT, "token_action", f"{label} needs a manual step: {action}"[:MAX_LINE]))
    for name, result in steps:
        if str(result).startswith("FAILED"):
            flags.append(flag(WARN, "step_failed", f"post-close step {name} {result}"))

    # tokens
    tokens = token_facts(cfg, now_epoch)
    for t in tokens:
        if t["state"] == "expired":
            flags.append(flag(ALERT, "token_expired", f"{t['name']} is expired"))
        elif t["state"] == "fresh" and t["hours_left"] < TOKEN_WARN_HOURS:
            flags.append(flag(WARN, "token_expiring", f"{t['name']} expires in {t['hours_left']}h (< {TOKEN_WARN_HOURS:g}h)"))
        elif t["state"] == "unset" and t["name"] == ACCESS_TOKEN_ENV:
            flags.append(flag(WARN, "token_unset", f"{t['name']} is not set"))

    # sessions
    sessions = []
    trading = CALENDAR.is_trading_day(day)
    for sid, e in sorted(strategies.items()):
        sink = "live" if e.get("live") else "paper"
        hb, age = assess_heartbeat(latest_status(cfg.runs_dir, day, sid, sink=sink), now_epoch, cfg.stale_s)
        status = e.get("status")
        sessions.append({"strategy": sid, "mode": sink, "status": status, "restarts": e.get("restarts", 0),
                         "heartbeat": hb})
        if status in BAD_SESSION_STATUS:
            flags.append(flag(ALERT, "session_bad", f"{sink} session {sid} ended {status}"))
        elif status not in OK_SESSION_STATUS:
            flags.append(flag(WARN, "session_unfinished", f"{sink} session {sid} is still '{status}' at post-close"))
        if hb == "none":
            flags.append(flag(WARN, "status_missing", f"{sid}: no status file for today"))
        elif hb == "error":
            flags.append(flag(ALERT, "status_error", f"{sid}: status file reports an error"))
        elif hb == "stale":
            flags.append(flag(WARN, "status_stale", f"{sid}: status file is stale (still 'running' after the session)"))
        if e.get("restarts"):
            flags.append(flag(INFO, "restarts", f"{sid}: restarted {e['restarts']} time(s)"))
    if trading and pre and pre.get("ok"):
        for s in specs:
            if s.is_paper and s.strategy_id not in strategies:
                flags.append(flag(WARN, "session_not_started", f"paper session {s.strategy_id} did not start today"))

    # live: orders, fills, cap, arm
    fills: List[dict] = []
    live_cap: List[dict] = []
    for sid, e in sorted(strategies.items()):
        if not e.get("live"):
            continue
        rows = _safe(flags, f"live stores of {sid}", lambda sid=sid: fill_rows(cfg, sid, day), [])
        fills += rows
        for r in rows:
            a = abs(r["dev_bps"])
            if a >= FILL_DEVIATION_WARN_BPS:
                sev = ALERT if a >= FILL_DEVIATION_ALERT_BPS else WARN
                flags.append(flag(sev, "fill_far_from_decision",
                                  f"{sid} {r['side']} {r['symbol']}: avg fill {r['avg_fill']:,.0f} vs decision price "
                                  f"{r['decision_price']:,} ({r['dev_bps']:+.0f} bps)"))
        cap = int(e.get("max_idr") or 0)
        orders = _safe(flags, f"live orders of {sid}",
                       lambda sid=sid: liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid, day)), [])
        bought = liveorders.bought_idr(orders)
        live_cap.append({"strategy": sid, "bought_idr": bought, "cap_idr": cap,
                         "pct": round(100.0 * bought / cap, 1) if cap else None})
        if cap and bought > cap:
            flags.append(flag(ALERT, "live_cap_exceeded", f"{sid}: bought {bought:,} IDR against a cap of {cap:,}"))
        elif cap and bought >= CAP_WARN_FRACTION * cap:
            flags.append(flag(WARN, "live_cap_near", f"{sid}: bought {bought:,} of {cap:,} IDR cap"))
        for o in liveorders.unresolved(orders):
            flags.append(flag(WARN, "order_unresolved",
                              f"{sid}: {o.side} {o.symbol} {o.filled_lots}/{o.lots} lots ended unfilled or partial; confirm in the app"))
        for o in orders:
            if o.rejected:
                flags.append(flag(WARN, "order_rejected", f"{sid}: {o.side} {o.symbol} {o.lots} lots rejected"))
    for sid in _armed_not_run(cfg, day, strategies):
        flags.append(flag(ALERT, "armed_not_run", f"{sid} was armed but no live session ran"))

    # positions open past their planned hold
    positions: List[dict] = []
    for s in specs:
        if not (s.is_paper or s.is_candidate):
            continue
        pos = _safe(flags, f"stores of {s.strategy_id}", lambda s=s: open_positions(cfg, s.strategy_id),
                    {"live": [], "paper": []})
        for sink, rows in pos.items():
            for r in rows:
                held = CALENDAR.trading_days_between(date.fromisoformat(r["entry_day"]), day) if r["entry_day"] else None
                row = {"strategy": s.strategy_id, "sink": sink, "symbol": r["symbol"], "lots": r["lots"],
                       "entry_day": r["entry_day"], "sessions_held": held, "planned_hold": s.hold}
                positions.append(row)
                if held is None or held < s.hold:
                    continue
                sev = ALERT if held > s.hold else WARN
                flags.append(flag(sev, "position_past_hold",
                                  f"{s.strategy_id} {sink}: {r['symbol']} {r['lots']} lots open {held} sessions, "
                                  f"planned hold {s.hold}"))

    # recon
    recon = _safe(flags, "recon output", lambda: recon_facts(cfg), None)
    if recon is None:
        flags.append(flag(WARN, "recon_missing", "no recon output (runs/recon) exists"))
    else:
        if recon["status"] != "match":
            syms = ", ".join(f"{d['symbol']} expected {d['expected_lots']} broker {d['broker_lots']}"
                             for d in recon["differences"]) or "see report"
            flags.append(flag(ALERT, "recon_mismatch", f"recon positions do NOT match the broker: {syms}"))
        for err in recon["errors"]:
            flags.append(flag(WARN, "recon_error", f"recon: {err}"))
        tc = recon["trade_check"]
        if tc.get("store_only"):
            flags.append(flag(ALERT, "recon_phantom", f"recon: {tc['store_only']} trade(s) recorded by the bot but not at the broker"))
        if tc.get("checked") is False:
            flags.append(flag(WARN, "recon_unchecked", "recon: trade-by-trade check was not done"))
        if recon["as_of"][:10] != str(day):
            flags.append(flag(WARN, "recon_stale", f"newest recon is from {recon['as_of'][:10]}, not today"))

    # scorecards (paper) and cost report
    scorecards = _safe(flags, "paper scorecards", lambda: scorecard_facts(cfg, [s for s in specs if s.is_paper]), [])
    for sc in scorecards:
        if sc["verdict"].startswith("STOP"):
            flags.append(flag(WARN, "scorecard_stop", f"{sc['strategy']}: scorecard says STOP ({sc['n_trips']} trips, "
                                                       f"mean net {sc['mean_net_bps']} bps)"))
    cost = _safe(flags, "cost report", lambda: cost_report_facts(cfg), None)
    if cost and cost["generated"]:
        try:
            age = (day - date.fromisoformat(cost["generated"][:10])).days
            if age > COST_REPORT_STALE_DAYS:
                flags.append(flag(INFO, "cost_report_stale", f"cost report is {age} days old"))
        except ValueError:
            pass

    log = read_log(cfg, day)
    if log["n_alert"]:
        flags.append(flag(INFO, "log_alerts", f"{log['n_alert']} ALERT line(s) in today's runner log"))

    flags.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
    counts = {sev: sum(1 for f in flags if f["severity"] == sev) for sev in (ALERT, WARN, INFO)}
    return {"schema": 1, "date": str(day), "counts": counts, "flags": flags, "preflight": pre,
            "token_rotation": rot or None, "token_rotation_post_close": rot_pc or None,
            "tokens": tokens, "sessions": sessions,
            "steps": [{"name": n, "result": r} for n, r in steps], "fills": fills, "live_cap": live_cap,
            "positions": positions, "recon": recon, "scorecards": scorecards, "cost_report": cost,
            "log": log, "live_summary": list(live_summary or []),
            "paper_summary": list(paper_summary or [])}


def _armed_not_run(cfg: Config, day: date, strategies: dict) -> List[str]:
    from runner.arm import read_arm_file
    arm = read_arm_file(cfg.runs_dir, day)
    if not arm.valid:
        return []
    return [sid for sid in sorted(arm.max_idr) if not (strategies.get(sid) or {}).get("live")]
