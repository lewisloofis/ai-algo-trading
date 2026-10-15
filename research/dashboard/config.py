"""
dashboard/config.py — the few things a person may need to edit, in one place.

Everything else is auto-discovered (see inputs.py). Keep this file short.
"""

from __future__ import annotations

import os

RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RESEARCH_DIR)

DEFAULT_RUNS_DIR = os.path.join(REPO_ROOT, "runs")
DEFAULT_DAILY_DIR = os.path.join(RESEARCH_DIR, "data", "daily")
DEFAULT_OUT_DIR = os.path.join(RESEARCH_DIR, "out_live", "dashboard")
HEALTHCHECK_SCRIPT = os.path.join(REPO_ROOT, "scripts", "collector_healthcheck.py")

# Post-trade stores that must NOT be read, by file name.
#
# runs/2026-10-14-limitprobe.db is mislabeled (per the account owner, 2026-10-14): its name does
# not match what it holds (five run headers, four of them errors). Reading it would put the
# wrong orders on the Trades page. The BBRI limit-probe record is
# runs/2026-10-14-bbri-limitprobe.db.
# Add a name here, with a comment saying why, rather than deleting the file.
EXCLUDED_STORES: frozenset[str] = frozenset({"2026-10-14-limitprobe.db"})

# Strategy-fit output directories (relative to research/) that were produced AFTER the D1
# entry-timing fix (daily strategies enter at the NEXT session's close). Only these are ever
# shown as current results. Older out_* directories (e.g. out_live) used the same-close entry
# that cannot be traded and must not be added here. When a newer corrected run is produced,
# add its directory; the newest "Generated:" time wins.
CORRECTED_RUN_DIRS: tuple[str, ...] = ("out_d1/next_close",)

# How long to let the collector health check run (it builds a binary and reads S3).
HEALTHCHECK_TIMEOUT_S = 900

# What the owner should run when a check could not be done here.
HEALTHCHECK_RUN_HINT = "make dashboard   (from a shell with your AWS credentials loaded)"

# The broker reconciliation (strategy/cmd/account-recon --stores, wired to `make recon`). It writes
# runs/recon/<date>.json, which the Overview reads. The sleeve account number comes from
# SB_ACCOUNT_NO in .env.local, never from this file.
RECON_DIRNAME = "recon"
RECON_RUN_HINT = f"set -a && source .env.local && set +a && make -C {REPO_ROOT} recon"
RECON_STALE_DAYS = 2
"""A reconciliation older than this many calendar days is shown amber, not green."""

WIB_OFFSET_HOURS = 7
"""Jakarta time (Western Indonesia Time), UTC+7, no daylight saving."""

# Freshness limits, in calendar days. A weekend is 3 days; a long holiday can be more.
FRESH_DAYS = 4
STALE_DAYS = 8
ENDED_DAYS = 60
"""A stock whose last daily bar is this many calendar days behind the store's newest has stopped
trading (delisted, or a long suspension). Those are kept on file for survivorship tests and are
reported separately, not as lagging. Matches screener.daily_store.ENDED_AFTER_DAYS."""

UNIVERSE_STATUS_FILENAME = "universe_status.json"
UNIVERSE_STATUS_SCHEMA_VERSION = 2
"""Written next to the daily parquet files by `make refresh-prices` (screener.universe_status): per
symbol is_tradable true/false/null. Must match screener.universe_status.STATUS_FILENAME / SCHEMA_VERSION."""
