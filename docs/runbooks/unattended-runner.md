# Unattended daily runner (paper only)

`research/runner/` is a scheduler and supervisor around commands that already exist
(`strategy_fit.paper_day`, `make refresh-prices | recon | paper-scorecard | dashboard`). It
replaces typing them each trading day. **It only runs paper. It cannot arm live trading and
has no path that submits a real order.**

Why Python: the trading calendar (`lib.market_calendar`), the spec registry
(`emitter_specs`), `paper_day`, the daily store and the dashboard are all Python, so the runner
reuses them in-process. The Go side is reached only through `paper_day` (which builds and runs
`live-session --sink paper`).

## What happens each day (WIB, Mon-Fri; holidays are detected, not scheduled around)

| time | job | what |
|---|---|---|
| 08:45 | `runner morning` | **preflight**, then **start**, then **watch** |
| 08:45 | preflight | trading day (repo calendar); previous session's daily bar present, not a zero-volume placeholder, for every paper spec's symbols; `SB_ACCESS_TOKEN` present and not expiring within 1.5h; `SB_BEARER_TOKEN` present (warn only); `runs/KILL` absent; strategy binaries present or `go` available. Any failure: alert, skip the day |
| right after | start | `make build-strategy`, then `python -m strategy_fit.paper_day --strategy <id>` for each spec with status `paper` (emit target + `live-session --sink paper --source live`), so it runs before 09:29 |
| to 09:45 | watch | every 15s: status file stale > 2 min alerts; dead process is restarted only if safe (below); at 09:45 anything still running gets a graceful SIGINT |
| 16:20 | `runner post-close` | `refresh-prices`, `recon` (only if `SB_ACCOUNT_NO` and `SB_TRADING_ACCESS_TOKEN` are set), `paper-scorecard` per paper strategy, dashboard rebuild (`HEALTHCHECK=skip`), then a summary alert |

A failed preflight skips trading for the day but **post-close still runs**: refreshing prices
is what repairs the next morning's stale-bar failure.

Subcommands: `python -m runner preflight | start | watch | post-close | morning | all`
(`all` = morning + wait until 16:20 + post-close, for running by hand). Options: `--date
YYYY-MM-DD`, `--dry-run`, `--force` (post-close rerun), `--no-env-file`.

Restart rule (paper only): the process must have died without an exit code (killed, OOM,
reboot) or by signal; **no** order in today's paper store may be missing its fill; fewer than 2
restarts; and still before `REBALANCE_AT` minus 60s (paper-day refuses later anyway). A real
exit code (1 problem, 2 refused, 3 no token, 5 not paper, 6 too late) is reported, never
retried. No order is ever resubmitted and `--retry` is refused by the guard.

## Install / uninstall

The Mac must be in the Asia/Jakarta time zone (launchd uses local time; install refuses
otherwise) and have `research/.venv` (or pass `DASH_PY=`).

```sh
make runner-dry-run RUNNER_DATE=2026-10-16   # read the plan first
make runner-install
make runner-status
make runner-uninstall
```

The jobs live in `~/Library/LaunchAgents/com.sbalgo.runner.{morning,postclose}.plist`
(templates in `ops/launchd/`). They hold no secrets: the runner loads `<repo>/.env.local`
itself at start-up (the shell environment wins over the file).

### The Mac must be awake
launchd does not wake a sleeping Mac for a user agent. Schedule a wake and keep it plugged in:

```sh
sudo pmset repeat wakeorpoweron MTWRF 08:30:00
sudo pmset -a sleep 0 displaysleep 10     # or keep it awake through 16:30 another way
```

A missed 08:45 (asleep) means no paper session that day; the 16:20 job still runs when the Mac
is awake. Check `runner-status` after a lid-closed day. An EC2 variant (always on, token from
SSM per plan 03) is future work.

## Environment (presence is checked; values are never printed, logged or hashed)

`SB_ACCESS_TOKEN` (market data, paper), `SB_BEARER_TOKEN` (refresh-prices), optional
`SB_ACCOUNT_NO` + `SB_TRADING_ACCESS_TOKEN` (recon, read-only), `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID`, or `RUNNER_ALERT_WEBHOOK_URL` as fallback. Token expiry is read from the JWT
`exp` claim in-process and only ever shown as "expires in 3.2h". A token that is not a JWT
gives a warning, not a failure. `ALGO_LIVE`, `ALGO_CONFIRM` are stripped from every child;
`SB_TRADING_ACCESS_TOKEN` reaches only `recon`.

## Kill switch

`touch runs/KILL` (the same file live-session uses). Preflight and start refuse while it
exists, the watchdog alerts when it appears, and live-session stops a running session itself.
`rm runs/KILL` to resume; rerun `python -m runner morning` by hand if before 09:29.

## Logs

- `runs/runner/runner.log`: every decision and alert, redacted, one line each.
- `runs/runner/<date>-<strategy>.log`: paper-day / live-session output; `<date>-post-close.log`; `<date>-build.log`.
- `runs/runner/state-<date>.json`: phase state (preflight result, per-strategy status, pid, restarts, post-close done).
- `runs/runner/launchd-*.err`: only if the runner itself failed to start.
- Heartbeats stay where they were: `runs/<date>-<id>-paper-aN.status.json`; results via the dashboard.

## Alerts (Telegram, else webhook, always the log; never a token)

| alert | meaning / action |
|---|---|
| `preflight FAILED, skipping the day` | the listed check failed (missing bar, token missing or short-lived, kill file). Fix and rerun `python -m runner morning` before 09:29 |
| `preflight warnings` | e.g. token not a JWT (expiry unknown), bearer token missing (refresh-prices will fail at 16:20) |
| `build-strategy failed` | no session started; read `<date>-build.log` |
| `status file stale` | process alive but heartbeat older than 2 min; inspect the session log |
| `no status file ... after start` | paper-day produced no heartbeat (emit/refusal); read the session log |
| `session died ...; restarted` | warning only, paper restart happened |
| `session died ...; NOT restarted` | exit code, open order, budget or time said no. Check the store and log; do not simply rerun |
| `still running at the deadline` | graceful stop sent at 09:45 |
| `kill switch file appeared` | live-session is stopping itself |
| `post-close: <step> failed` | the chain continues; read `<date>-post-close.log` |
| `daily summary` | per strategy status, fills today, paper position, step results |

## Idempotency

Rerunning is safe. `start` holds a pid lock and records each strategy in the state file; a strategy
already `started` or `done` today is not started again. `post-close` runs once per day unless `--force`.
Runner restarts are the only way a second store appears for a day.

## Failure modes

- **Token lifetime**: the access token is a copy from the web and expires; the runner cannot
  refresh it. If it dies overnight, preflight fails and you get an alert. Unattended operation lasts only
  as long as `.env.local` holds a fresh token (plan 03: SSM-backed rotation is EC2-only today).
- Mac asleep / logged out / time zone not WIB: no run. Agents need a logged-in user session.
- Daily bar stale because the previous 16:20 failed: preflight fails; run `make refresh-prices`.
- `.env.local` unreadable or malformed: the runner reports the line number only.
- Telegram/webhook down: the alert is still in `runner.log`.

## Future: live mode (not implemented)

`runner/modes.py:decide_mode` is the single place that decides each strategy's mode:
today only `paper` or `skip`. A future live mode would require **both** spec status `candidate`
and an arm file `runs/arm/ARM-<YYYY-MM-DD>.json`, valid only for its own date:

```json
{"date": "2026-10-16", "strategies": {"<strategy_id>": {"max_idr": 300000}}}
```

This version parses and validates the file for the log and then ignores it: the log says
"live not supported in this version", and a candidate is skipped. Live trading remains the
hand-run `first-live-order.md` / `live-session.md` flow.
