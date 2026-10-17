"""Markdown rendering for strategy_fit/families_index_rebalance.py (plan 21 H2). Every number comes from the
results dict that module produces; nothing is typed in by hand."""
from __future__ import annotations

import numpy as np

CELLS = ("MSCI_ST", "MSCI_SC", "FTSE", "IDX", "pooled")
HOLDS = (5, 10, 20)
PRE = ("no_dates", "ticker_unresolved", "no_bars", "suspended_a_to_p", "duplicate_ticker_p")
HARNESS = ("entry_not_traded", "bad_bars_or_missing", "not_eligible", "sequential_overlap")


def f(x, nd: int = 1) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{nd}f}"


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def _cells(out):
    return [(c, k, out["cells"][c][k]) for c in CELLS for k in HOLDS]


def results_rows(out) -> list[list]:
    rows = []
    for c, k, r in _cells(out):
        if r["status"] == "no_data":
            rows.append([c, k, 0, 0, "-", "-", "-", "-", "-", "-", "-", "-", "1.000", "no_data"])
            continue
        m = r["metrics"]
        if not m["n"]:
            rows.append([c, k, 0, 0, "-", "-", "-", "-", "-", "-", "-", "-", f(r["p_holm"], 3), r["status"]])
            continue
        v = r["verdict"] + (" (" + ",".join(r["failed_gates"]) + ")" if r["failed_gates"] else "")
        rows.append([c, k, m["n"], m["reviews"], f(m["gross"]), f(m["null"]), f(m["edge"]), f(m["t"], 2),
                     f(m["cost_cur"]), f(m["net_edge_cur"]), f(m["net_minus_ihsg"]), f(m["drop5_net_edge_cur"]),
                     f(r["p_holm"], 3), v])
    return rows


RESULT_HEADER = ["cell", "k", "events", "reviews", "gross bps", "null bps", "edge bps", "t (review-clust.)",
                 "cost bps", "net = edge-cost", "net-IHSG", "drop-top-5 net", "Holm p", "verdict (failed gates)"]


def exclusion_rows(out) -> list[list]:
    rows = []
    for c in CELLS:
        r5 = out["cells"][c][5]
        if r5["status"] == "no_data":
            rows.append([c, 0, *["-"] * len(PRE), "no_data", *["-"] * 6])
            continue
        pre = r5["pre_exclusions"]
        row = [c, r5["raw_events"], *[pre[x] for x in PRE], r5["candidate_events"]]
        for k in HOLDS:
            ex = out["cells"][c][k]["exclusions"]
            row.append(" / ".join(str(ex[x]) for x in HARNESS))
        row += [out["cells"][c][k]["exclusions"]["trades"] for k in HOLDS]
        rows.append(row)
    return rows


EXCL_HEADER = ["cell", "raw dev events", *PRE, "candidates",
               *[f"k={k}: " + "/".join(x.replace("_", " ") for x in HARNESS) for k in HOLDS],
               *[f"trades k={k}" for k in HOLDS]]


def sens_rows(out) -> list[list]:
    rows = []
    for c, k, r in _cells(out):
        if r["status"] == "no_data" or not r["metrics"]["n"]:
            continue
        m, op, off = r["metrics"], r["sens_open_entry"]["metrics"], r["sens_eligibility_traded_only"]
        om = off["metrics"]
        rows.append([c, k, f(m["net_edge_cur"]), f(m["net_edge_1lot"]), f(m["net_edge_cand_latest"]),
                     f(m["net_edge_cand_pooled"]), f(m["net_edge_10x"]),
                     f"{f(op.get('edge'))} (t {f(op.get('t'), 2)}) / net {f(op.get('net_edge_cur'))}",
                     f"n={om.get('n', 0)}, rev={om.get('reviews', 0)}: edge {f(om.get('edge'))} (t {f(om.get('t'), 2)}) / "
                     f"net {f(om.get('net_edge_cur'))} / n-IHSG {f(om.get('net_minus_ihsg'))}"])
    return rows


SENS_HEADER = ["cell", "k", "net edge @3M (current)", "net edge 1-lot", "net edge cand. latest (gate)",
               "net edge cand. pooled", "net edge 10x notional", "open_{p+1} entry: edge (t) / net",
               "eligibility = traded-that-day only (NON-gating)"]


def g8_rows(out) -> list[list]:
    rows = []
    for c, k, r in _cells(out):
        if r["status"] == "no_data" or not r.get("regime_g8"):
            continue
        g = r["regime_g8"]
        cell = []
        for key in ("below_sma200", "at_or_above_sma200"):
            x = g[key]
            cell.append("-" if not x["n"] else
                        f"n={x['n']}, rev={x['reviews']}: edge {f(x['edge'])} (t {f(x['t'], 2)}), net {f(x['net_edge_cur'])}, "
                        f"net-IHSG {f(x['net_minus_ihsg'])}")
        rows.append([c, k, *cell, g["unknown_regime"]])
    return rows


G8_HEADER = ["cell", "k", "IHSG below SMA200 at a", "IHSG at/above SMA200 at a", "unknown regime (n)"]


def gate_rows(out) -> list[list]:
    rows = []
    for c, k, r in _cells(out):
        if r["status"] != "tested":
            rows.append([c, k, r["status"], *["-"] * 7])
            continue
        g = r["gates"]
        mark = lambda b: "pass" if b else "FAIL"  # noqa: E731
        rows.append([c, k, "met", mark(g["G2"]), mark(g["G3a_current"]), mark(g["G3b_candidate"]), mark(g["G3c_1lot"]),
                     mark(g["G4"]), mark(g["G6"]), mark(g["G7"])])
    return rows


GATE_HEADER = ["cell", "k", "sample floor", "G2", "G3a current", "G3b candidate", "G3c 1-lot", "G4", "G6", "G7"]


def summary_lines(out) -> list[str]:
    cells = _cells(out)
    tested = [(c, k, r) for c, k, r in cells if r["status"] == "tested"]
    nodata = [(c, k) for c, k, r in cells if r["status"] == "no_data"]
    insuff = [(c, k) for c, k, r in cells if r["status"] == "insufficient_n"]
    n_pass = [(c, k) for c, k, r in tested if r["verdict"] == "PASS_DEV"]
    g2 = [(c, k) for c, k, r in tested if r["gates"]["G2"]]
    g3 = [(c, k) for c, k, r in tested if r["gates"]["G3"]]
    pooled_e = [out["cells"]["pooled"][k]["metrics"] for k in HOLDS if out["cells"]["pooled"][k]["metrics"]["n"]]
    l1 = (f"We tested one idea on 2014-2021 data only: buy a stock the day after an index provider's forced selling of "
          f"it is finished (a deletion) and hold it 5, 10 or 20 trading days, to see whether it rebounds by more than "
          f"trading costs.")
    l2 = (f"15 test cells were fixed in advance; {len(tested)} had enough events (30+ events, 12+ reviews), "
          f"{len(insuff)} were too small and {len(nodata)} had no data (all IDX: not transcribed yet).")
    best = max(tested, key=lambda x: x[2]["metrics"]["t"], default=None)
    small_hi = [(c, k) for c, k, r in cells if r["status"] == "insufficient_n" and r["metrics"]["n"]
                and r["metrics"]["t"] >= 2.0]
    if best:
        bm = best[2]["metrics"]
        l3 = (f"Before costs, {len(g2)} of {len(tested)} tested cells beat simply holding the same stock (t >= 2); the "
              f"strongest is {best[0]} k={best[1]} with an average edge of {f(bm['edge'])} bps (t {f(bm['t'], 2)}).")
    else:
        l3 = "No cell had enough events to test."
    if small_hi:
        l3 += (" Some cells below the sample floor look strong (" + ", ".join(f"{c} k={k}" for c, k in small_hi)
               + ") but are not scored: too few events or reviews.")
    fails: dict[str, int] = {}
    for _, _, r in tested:
        for g in r["failed_gates"]:
            fails[g] = fails.get(g, 0) + 1
    common = ", ".join(f"{g} ({n} cells)" for g, n in sorted(fails.items(), key=lambda x: -x[1])[:3]) or "none"
    l4 = (f"After realistic trading costs and the other checks, {len(n_pass)} of {len(tested)} tested cells pass "
          f"(the cost gate alone passes in {len(g3)} of {len(tested)}); most common failing gates: {common}.")
    dec = out["decision"]["outcome"]
    reading = {"candidate": "A single-provider cell passed: it is a forward-test candidate under plan section 5.",
               "investigate": "Only the pooled cell passed: not a candidate, investigate which provider drives it.",
               "no_pass": "Nothing passed, so on this window the deletion-rebound idea is not supported."}[dec]
    l5 = f"{reading} The holdout (2022 onward) has NOT been run or touched."
    return [l1, l2, l3, l4, l5]


def _head(out, title: str) -> list[str]:
    return [f"# {title}", ""]


def render_full(out) -> str:
    P = []
    P.append("# Plan 21 H2 deletion rebound, development window: full results\n")
    P.append("Generated by `research/strategy_fit/families_index_rebalance.py` (frozen plan 21 section 9a). All bps unless "
             "stated. net = edge - cost (edge = gross - same-name always-long null); net-IHSG = gross - cost - IHSG "
             "simple return over the identical close_{p+1} to close_{p+1+k} window.\n")
    P.append("## Results (15 cells)\n")
    P.append(table(RESULT_HEADER, results_rows(out)) + "\n")
    P.append("## Gate matrix\n")
    P.append(table(GATE_HEADER, gate_rows(out)) + "\n")
    P.append("## Exclusions per cell\n")
    P.append("Harness exclusions are `entry not traded / bad bars or missing price / not eligible / sequential overlap`. "
             "`candidates` = unique (ticker, p) events after the k-independent exclusions.\n")
    P.append(table(EXCL_HEADER, exclusion_rows(out)) + "\n")
    P.append("## Sensitivities\n")
    P.append(table(SENS_HEADER, sens_rows(out)) + "\n")
    P.append("## G8 regime split (report only)\n")
    P.append(table(G8_HEADER, g8_rows(out)) + "\n")
    P.append(f"Decision (plan section 5): **{out['decision']['outcome']}** {out['decision']['cells']}\n")
    return "\n".join(P)


def render_doc(out) -> str:
    P = ["# Index rebalance H2 (deletion rebound): development-window result\n"]
    P.append("Plan: [21](../plans/21-index-rebalance-event-study.md), frozen section 9a. Development window only "
             "(reviews announced <= 2021-12-31, non-freeze). Holdout not run. Generated by "
             "`research/strategy_fit/families_index_rebalance.py`; full numbers in "
             "`research/results/index_rebalance/h2_dev.json` and `h2_dev.md`.\n")
    P.append("## Summary\n")
    P += [f"{i}. {s}" for i, s in enumerate(summary_lines(out), 1)]
    P.append("")
    P.append("## Coverage and exclusions\n")
    P.append("Harness exclusions per k are `entry not traded / bad bars or missing price / not eligible / sequential "
             "overlap`. `not eligible` is the harness's default tradeability filter (ADV, price, history, zero-volume "
             "days at s = p), used as-is.\n")
    P.append(table(EXCL_HEADER, exclusion_rows(out)) + "\n")
    P.append("## Results\n")
    P.append("Means in bps. net = edge - cost at the harness Rp 3,000,000 with the current impact coefficients; "
             "net-IHSG = gross - cost - IHSG over the same window; drop-top-5 = mean net without the 5 best events. "
             "t is clustered by review. Holm p is one-sided over all 15 cells (no_data and insufficient_n count as p = 1).\n")
    P.append(table(RESULT_HEADER, results_rows(out)) + "\n")
    P.append("## Gates\n")
    P.append(table(GATE_HEADER, gate_rows(out)) + "\n")
    P.append("## Sensitivities (net edge in bps)\n")
    P.append("The eligibility-off column replaces the harness's default tradeability filter with 'traded that day' and is "
             "not gating; names without ADV history are charged floor cost only there.\n")
    P.append(table(SENS_HEADER, sens_rows(out)) + "\n")
    P.append("## G8: IHSG below its 200-session SMA at the announcement session (not blocking)\n")
    P.append(table(G8_HEADER, g8_rows(out)) + "\n")
    P.append(f"## Decision rule (plan section 5)\n\n**{out['decision']['outcome']}** {out['decision']['cells']}\n")
    return "\n".join(P)
