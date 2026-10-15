"""Operator-facing text for live mode: the 08:45 arm status, order-event alerts, the day summary.

Everything here is built from counts, symbols, lots, prices, strategy ids and the armed cap. No
token, no account number: and the Alerter redacts the result again before anything leaves."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import List, Optional, Sequence, Tuple

from runner import liveorders
from runner.alerts import LIVE, Alerter
from runner.arm import read_arm_file
from runner.config import Config
from runner.heartbeat import latest_status, read_rc, rc_path
from runner.modes import LIVE as MODE_LIVE
from runner.modes import PAPER


def arm_status(cfg: Config, day: date) -> str:
    """'ARMED: id, max N IDR[; ...]' or 'not armed' (with why, when a file exists but is unusable)."""
    arm = read_arm_file(cfg.runs_dir, day)
    if not arm.present:
        return "not armed"
    if not arm.valid:
        return "not armed (arm file INVALID: " + "; ".join(arm.problems) + ")"
    return "; ".join(f"ARMED: {sid}, max {n:,} IDR" for sid, n in sorted(arm.max_idr.items()))


def _lots(n: int) -> str:
    return f"{n} lot" + ("" if abs(n) == 1 else "s")


def unmanaged_positions(cfg: Config, decisions: Sequence[Tuple[object, object]]) -> List[str]:
    """Candidates that hold a LIVE position (from the stores) but are not going live today: nothing
    will sell it. One line each, so it is never a silent surprise."""
    lines = []
    for spec, mode in decisions:
        if not spec.is_candidate or mode.mode == MODE_LIVE:
            continue
        try:
            held = liveorders.live_net_lots(cfg.runs_dir, spec.strategy_id)
        except sqlite3.Error:
            lines.append(f"{spec.strategy_id}: could not read the live stores to check for an open position")
            continue
        if held:
            what = ", ".join(f"{sym} {_lots(n)}" for sym, n in sorted(held.items()))
            lines.append(f"{spec.strategy_id}: holds {what} LIVE and is NOT going live today ({mode.reason}); "
                         "nothing will sell it. Arm it (make runner-arm) or sell by hand")
    return lines


def morning_summary(cfg: Config, day: date, decisions: Sequence[Tuple[object, object]]) -> Tuple[str, str]:
    """(title, body) of the 08:45 summary. The title's tail is the arm status, verbatim."""
    title = f"{day} morning plan: {arm_status(cfg, day)}"
    body: List[str] = []
    for spec, mode in decisions:
        sid = spec.strategy_id
        if mode.mode == MODE_LIVE:
            body.append(f"LIVE {sid}: buys at most {mode.token.max_idr:,} IDR today, rebalance at {cfg.rebalance_at} WIB; "
                        "never restarted; revoke with make runner-disarm")
        elif mode.mode == PAPER:
            body.append(f"paper {sid}")
        elif spec.is_candidate:
            body.append(f"NOT live {sid}: {mode.reason}")
        else:
            body.append(f"skip {sid}: {mode.reason}")
    body += [f"WARNING {line}" for line in unmanaged_positions(cfg, decisions)]
    return title, "\n".join(body)


# ---- order events (watch) ---------------------------------------------------------------------

def flush_events(cfg: Config, day: date, sid: str, entry: dict, alerter: Alerter, final: bool) -> int:
    """Alert every order event not yet reported for this strategy's live stores; record them in the
    state entry so a rerun never repeats one. Returns how many were sent. Never raises."""
    try:
        orders = liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid, day))
    except sqlite3.Error as exc:
        alerter.log(f"{sid}: live store not readable yet ({type(exc).__name__})")
        return 0
    lines, seen = liveorders.new_events(orders, entry.get("live_seen", {}), final)
    entry["live_seen"] = seen
    for line in lines:
        alerter.send(LIVE, line)
    return len(lines)


# ---- end-of-day summary -----------------------------------------------------------------------

def _order_counts(orders: Sequence[liveorders.OrderState]) -> str:
    live = [o for o in orders if not o.rejected]
    filled = sum(1 for o in live if o.fully_filled)
    partial = sum(1 for o in live if 0 < o.filled_lots < o.lots)
    unfilled = sum(1 for o in live if o.filled_lots == 0)
    rejected = sum(1 for o in orders if o.rejected)
    return (f"{len(live)} submitted: {filled} filled, {partial} partial, {unfilled} unfilled/cancelled; "
            f"{rejected} rejected")


def day_summary(cfg: Config, day: date, state: dict) -> Optional[Tuple[str, str]]:
    """(title, body) of the LIVE summary for the day, or None when live had nothing to do today
    (not armed, and no live session started)."""
    entries = {sid: e for sid, e in state.get("strategies", {}).items() if e.get("live")}
    arm = read_arm_file(cfg.runs_dir, day)
    armed_ids = sorted(arm.max_idr) if arm.valid else []
    if not entries and not armed_ids:
        return None
    body: List[str] = []
    for sid, e in sorted(entries.items()):
        rc = read_rc(rc_path(cfg, day, sid))
        cap = e.get("max_idr") or 0
        head = f"{sid}: LIVE session {e.get('status')}, exit code {'n/a' if rc is None else rc}"
        try:
            orders = liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid, day))
            held = liveorders.live_net_lots(cfg.runs_dir, sid)
        except sqlite3.Error:
            body += [head, "  stores not readable: check runs/ by hand"]
            continue
        bought = liveorders.bought_idr(orders)
        fees = sum(o.fee_idr for o in orders)
        status = latest_status(cfg.runs_dir, day, sid, sink="live") or {}
        risk = status.get("risk") or {}
        body += [head,
                 f"  orders: {_order_counts(orders) if orders else 'none placed'}",
                 f"  bought {bought:,} of the {cap:,} IDR cap (the cap is enforced inside strategy-run)",
                 f"  fees {fees:,.0f} IDR; position now: "
                 + (", ".join(f"{s} {_lots(n)}" for s, n in sorted(held.items())) or "flat")]
        if risk.get("kill_switch_tripped"):
            body.append("  the in-process kill switch tripped during the run")
        if e.get("recon"):
            body.append(f"  broker recon after the failure: {e['recon']}")
    for sid in armed_ids:
        if sid not in entries:
            body.append(f"{sid}: armed (max {arm.max_idr[sid]:,} IDR) but NO live session ran today; see the 08:45 plan")
    title = f"{day} LIVE summary ({arm_status(cfg, day)})"
    return title, "\n".join(body)
