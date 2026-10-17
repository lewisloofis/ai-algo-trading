"""
dashboard/site.py — gather every input, then write every page.

``build_site`` is the one entry point behind ``make dashboard`` /
``python -m dashboard build``. It finds its own inputs (inputs.py), runs the collector health
check itself, and writes static HTML into one directory.

Failures that must stop the build rather than render something misleading:
  * a strategy_id in the stores that strategy_map.py does not know (UnmappedStrategyError)
  * a malformed runs/manual_trades.json (ManualTradesError), a manual sell that would take a
    position negative, or a link to a bot order that does not exist (ledger.LedgerError)
  * a column or link that points at a glossary term that does not exist (KeyError)
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from strategy_fit.trade_export import CellTrades, export_cells

from dashboard import broker_import, config, health, inputs, ledger, manual_trades, recon, results, scorecard, store, trades_data
from dashboard import paper_tests, strategy_status
from dashboard.live import LiveSummary, collect as collect_live
from dashboard.annotations import Annotation, Decision, TimelineEvent, load_annotations, load_decision, load_timeline


@dataclass(frozen=True)
class SiteOptions:
    runs_dir: str = config.DEFAULT_RUNS_DIR
    daily_dir: str = config.DEFAULT_DAILY_DIR
    research_dir: str = config.RESEARCH_DIR
    out_dir: str = config.DEFAULT_OUT_DIR
    annotations_path: str | None = None
    manual_trades_path: str | None = None
    """Default: <runs_dir>/manual_trades.json. A missing file means no manual trades."""
    broker_trades_path: str | None = None
    """Default: <runs_dir>/broker_trades.json (written by account-recon). A missing file means none."""
    research_log_path: str | None = None
    """Default: <runs_dir>/research_log.jsonl (appended by strategy_fit.research_log). A missing file means none."""
    healthcheck: str = "run"
    """'run' (run the script), 'skip', or a path to a saved ``--json`` output."""
    now: datetime | None = None


@dataclass
class SiteData:
    now: datetime
    options: SiteOptions
    health: list[health.HealthRow]
    stores: inputs.Stores
    store_data: store.StoreData
    annotations: dict[str, Annotation]
    decision: Decision | None
    timeline: list[TimelineEvent]
    orders: list[trades_data.OrderLine]
    live: list[trades_data.OrderLine]
    paper: list[trades_data.OrderLine]
    summary: trades_data.LiveSummary
    manual: list[manual_trades.ManualTrade]
    ledger: ledger.Ledger
    run: results.RunResults | None
    rows: list[scorecard.ScoreRow]
    exported: dict[tuple[str, str, int], CellTrades]
    closes: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    daily_last: dict[str, str] = field(default_factory=dict)
    online: LiveSummary | None = None
    """Every heartbeat file, as of build time (live.py); the Overview's "Online now" panel."""
    strategy_inputs: "strategy_status.Inputs | None" = None
    """Shortlist, universe status, stored symbols and the emitter registry; the Strategies page's inputs."""
    run_history: LiveSummary | None = None
    """Heartbeat files over the last RUN_HISTORY_DAYS days, for the Strategies page's "Recent runs"."""
    paper_tests: list | None = None
    """paper_tests.PaperRow per paper/candidate spec; None = strategy_fit unavailable (not checked)."""

    @property
    def now_wib(self) -> datetime:
        return self.now + timedelta(hours=config.WIB_OFFSET_HOURS)


def load_closes(daily_dir: str, symbol: str) -> list[tuple[str, float]]:
    path = os.path.join(daily_dir, f"{symbol}.parquet")
    if not os.path.exists(path):
        return []
    df = pd.read_parquet(path, columns=["date", "close"]).dropna()
    return [(str(d), float(c)) for d, c in zip(df["date"].astype(str), df["close"])]


def _healthcheck(opt: SiteOptions) -> inputs.HealthOutcome:
    if opt.healthcheck == "run":
        return inputs.run_healthcheck()
    if opt.healthcheck == "skip":
        return inputs.run_healthcheck(enabled=False)
    return inputs.load_healthcheck_file(opt.healthcheck)


def gather(opt: SiteOptions, log: Callable[[str], None] = lambda _m: None) -> SiteData:
    now = opt.now or datetime.now(timezone.utc)
    ann_path = opt.annotations_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "annotations.json")

    stores = inputs.discover_stores(opt.runs_dir)
    log(f"stores: {len(stores.kept)} read, {len(stores.excluded)} excluded")
    data = store.read_stores(list(stores.kept))
    if data.errors:
        raise RuntimeError("unreadable post-trade store(s): " + "; ".join(data.errors))
    annotations = load_annotations(ann_path)

    orders = trades_data.build_orders(data, annotations)  # raises UnmappedStrategyError
    live, paper = trades_data.split_by_sink(orders)
    summary = trades_data.live_summary(live)
    manual = manual_trades.load_manual_trades(
        opt.manual_trades_path or os.path.join(opt.runs_dir, "manual_trades.json"))  # raises ManualTradesError
    # Trades the broker's own history shows that no bot fill explains (account-recon imports them);
    # they supersede a hand-entered duplicate and are never counted twice against a bot fill.
    manual = broker_import.fold_into_manual(
        manual,
        broker_import.load_broker_imports(  # raises BrokerImportError
            opt.broker_trades_path or os.path.join(opt.runs_dir, "broker_trades.json")),
        live)
    merged = ledger.build_ledger(live, manual)  # raises LedgerError (negative position, unknown link)

    status_path = inputs.latest_status_json(opt.runs_dir)
    status = None
    if status_path:
        import json

        with open(status_path, encoding="utf-8") as f:
            status = json.load(f)

    log("collector health check..." if opt.healthcheck == "run" else "health check: not run")
    outcome = _healthcheck(opt)
    rows_health = health.health_rows(outcome, opt.daily_dir, data.runs, status, now)
    rows_health.append(recon.recon_row(opt.runs_dir, now))  # "Positions match the broker"

    run = results.latest_report(opt.research_dir, config.CORRECTED_RUN_DIRS)
    exported: dict[tuple[str, str, int], CellTrades] = {}
    rows: list[scorecard.ScoreRow] = []
    if run is not None:
        wanted = [c.key for c in run.cells if scorecard.needs_trades(c) and os.path.exists(
            os.path.join(opt.daily_dir, f"{c.symbol}.parquet"))]
        log(f"re-deriving trades for {len(wanted)} backtest cells...")
        exported = export_cells(wanted, data_dir=opt.daily_dir)
        rows = scorecard.build_rows(run, exported)

    symbols = sorted({x.symbol for x in orders} | {t.symbol for t in manual})
    closes = {s: load_closes(opt.daily_dir, s) for s in symbols}
    return SiteData(
        now=now,
        options=opt,
        health=rows_health,
        stores=stores,
        store_data=data,
        annotations=annotations,
        decision=load_decision(ann_path),
        timeline=load_timeline(ann_path),
        orders=orders,
        live=live,
        paper=paper,
        summary=summary,
        manual=manual,
        ledger=merged,
        run=run,
        rows=rows,
        exported=exported,
        closes=closes,
        daily_last=health.daily_data_newest(opt.daily_dir),
        online=collect_live(opt.runs_dir, now),
        paper_tests=paper_tests.collect(opt.runs_dir, opt.daily_dir),
        strategy_inputs=strategy_status.gather_inputs(
            opt.research_dir, opt.daily_dir,
            opt.research_log_path or os.path.join(opt.runs_dir, "research_log.jsonl")),
        run_history=collect_live(opt.runs_dir, now, recent_days=strategy_status.RUN_HISTORY_DAYS),
    )


def footer_text(d: SiteData) -> str:
    built = d.now_wib.strftime("%Y-%m-%d %H:%M WIB")
    bt = (
        f"Backtest results: run generated {d.run.generated}, corrected next-session entry."
        if d.run
        else "Backtest results: none found."
    )
    skipped = ", ".join(os.path.basename(p) for p in d.stores.excluded) or "none"
    return (
        f"Built {built}. {bt} Order records: {len(d.stores.kept)} store file(s) read "
        f"(deliberately skipped: {skipped}). The only network use is the collector health check."
    )


def build_site(opt: SiteOptions, log: Callable[[str], None] = lambda _m: None) -> list[str]:
    """Write every page into ``opt.out_dir``; returns the file paths written."""
    from dashboard import pages_glossary, pages_overview, pages_research, pages_strategies, pages_trades

    d = gather(opt, log)
    pages: dict[str, str] = {
        "index.html": pages_overview.render(d),
        "trades.html": pages_trades.render(d),
        "strategies.html": pages_strategies.render_list(d),
        "research.html": pages_research.render(d),
        "glossary.html": pages_glossary.render(d),
    }
    pages.update(pages_strategies.render_details(d))

    os.makedirs(opt.out_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(opt.out_dir, "*.html")):
        os.remove(stale)
    written = []
    for name, text in pages.items():
        path = os.path.join(opt.out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(path)
    return written
