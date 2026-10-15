"""Runner configuration, the WIB clock and the daily schedule constants."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

WIB = timezone(timedelta(hours=7))
REPO_ROOT = Path(__file__).resolve().parents[2]

ACCESS_TOKEN_ENV = "SB_ACCESS_TOKEN"          # market data, chartbit, emitten: paper-day, refresh-prices
TRADING_TOKEN_ENV = "SB_TRADING_ACCESS_TOKEN"  # recon only (read-only towards the broker)
ACCOUNT_ENV = "SB_ACCOUNT_NO"                 # recon only
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"
WEBHOOK_ENV = "RUNNER_ALERT_WEBHOOK_URL"
REVIEWER_ENV = "RUNNER_REVIEWER"                # claude (default) | off
REVIEWER_MODEL_ENV = "RUNNER_REVIEWER_MODEL"    # default haiku
DEFAULT_REVIEWER_MODEL = "haiku"


def at(day: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=WIB)


class Clock:
    """Wall clock in WIB. Tests substitute a fake with the same two methods."""

    def now(self) -> datetime:
        return datetime.now(WIB)

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


@dataclass(frozen=True)
class Config:
    repo_root: Path = REPO_ROOT
    env: Mapping[str, str] = field(default_factory=dict)
    python: str = ""
    rebalance_at: str = "09:30"        # paper-day's default; the window is 09:00-11:30
    preflight_at: str = "08:45"
    watch_deadline: str = "09:45"      # live-session stops itself 3m after rebalance_at
    post_close_at: str = "16:20"
    max_restarts: int = 2
    stale_s: int = 120                 # same bar as dashboard.health.HEARTBEAT_STALE_S
    no_heartbeat_s: int = 180
    poll_s: float = 15.0
    min_access_token_hours: float = 1.5
    registry: Optional[Any] = None
    rotate_tokens: bool = True         # call token_rotation.ensure_tokens before preflight (needs .env.local)

    @property
    def runs_dir(self) -> Path:
        return self.repo_root / "runs"

    @property
    def runner_dir(self) -> Path:
        return self.runs_dir / "runner"

    @property
    def research_dir(self) -> Path:
        return self.repo_root / "research"

    @property
    def daily_dir(self) -> Path:
        return self.research_dir / "data" / "daily"

    @property
    def kill_file(self) -> Path:
        return self.runs_dir / "KILL"      # live-session's default kill switch, honoured here too

    @property
    def log_file(self) -> Path:
        return self.runner_dir / "runner.log"

    def state_path(self, day: date) -> Path:
        return self.runner_dir / f"state-{day}.json"

    def child_log(self, day: date, name: str) -> Path:
        return self.runner_dir / f"{day}-{name}.log"
