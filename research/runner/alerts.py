"""Alerts: always a local log line, plus Telegram, falling back to a generic webhook.

Every message is redacted before it is written or sent; a send failure is logged (redacted) and
never raises into the runner."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Mapping

from runner.config import TELEGRAM_CHAT_ENV, TELEGRAM_TOKEN_ENV, WEBHOOK_ENV, WIB
from runner.redact import redact

MAX_LEN = 3500
INFO, WARN, ALERT, SUMMARY, LIVE = "INFO", "WARN", "ALERT", "SUMMARY", "LIVE"
_PUSH_LEVELS = (WARN, ALERT, SUMMARY, LIVE)


def http_post_json(url: str, payload: dict, timeout: float = 10.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URL from operator env
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


class Alerter:
    def __init__(self, log_path: Path, env: Mapping[str, str],
                 post: Callable[[str, dict], None] = http_post_json,
                 now: Callable[[], datetime] = lambda: datetime.now(WIB)):
        self._log, self._env, self._post, self._now = Path(log_path), env, post, now

    def _write(self, level: str, text: str) -> None:
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with self._log.open("a", encoding="utf-8") as f:
                f.write(f"{self._now().isoformat(timespec='seconds')} [{level}] {text}\n")
        except OSError:
            pass

    def log(self, text: str, level: str = INFO) -> None:
        """Local log only (no push)."""
        self._write(level, redact(text, self._env).replace("\n", " | "))

    def send(self, level: str, title: str, body: str = "") -> List[str]:
        """Log always; push WARN / ALERT / SUMMARY out (INFO stays local).

        Returns the channels that took it: 'log', 'telegram', 'webhook'."""
        text = redact(f"{title}\n{body}".strip(), self._env)[:MAX_LEN]
        channels = ["log"]
        self._write(level, text.replace("\n", " | "))
        if level not in _PUSH_LEVELS:
            return channels
        prefix = {ALERT: "[ALERT]", WARN: "[WARN]", LIVE: "[LIVE]"}.get(level, "[daily]")
        message = f"{prefix} sb-algo runner\n{text}"
        token, chat = self._env.get(TELEGRAM_TOKEN_ENV), self._env.get(TELEGRAM_CHAT_ENV)
        if token and chat:
            try:
                self._post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": message})
                channels.append("telegram")
                return channels
            except Exception as exc:  # noqa: BLE001
                self._write(WARN, redact(f"telegram delivery failed ({type(exc).__name__})", self._env))
        hook = self._env.get(WEBHOOK_ENV)
        if hook:
            try:
                self._post(hook, {"text": message})
                channels.append("webhook")
            except Exception as exc:  # noqa: BLE001
                self._write(WARN, redact(f"webhook delivery failed ({type(exc).__name__})", self._env))
        elif not (token and chat):
            self._write(INFO, "no push channel configured (TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID or RUNNER_ALERT_WEBHOOK_URL)")
        return channels
