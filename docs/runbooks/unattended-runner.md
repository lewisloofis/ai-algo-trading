# Unattended daily runner (paper by default, guarded live mode)

`research/runner/` is a scheduler and supervisor around commands that already exist
(`strategy_fit.paper_day`, `live-session`, `make refresh-prices | recon | paper-scorecard | dashboard`).
It replaces typing them each trading day. **It is paper unless you arm a strategy for a single
day** (see [Live mode](#live-mode-guarded-per-day-arming)): a `candidate` spec, a valid arm file
for today with a rupiah cap, a passing preflight including the trading token, and no `runs/KILL`.
Without all of those, no process that can place an order is ever started.

Why Python: the trading calendar (`lib.market_calendar`), the spec registry
(`emitter_specs`), `paper_day`, the daily store and the dashboard are all Python, so the runner
reuses them in-process. The Go side is reached only through `paper_day` (which builds and runs
`live-session --sink paper`).

## What happens each day (WIB, Mon-Fri; holidays are detected, not scheduled around)

| time | job | what |
|---|---|---|
| 08:45 | `runner morning` | **preflight**, then the **summary push** (`ARMED: <id>, max <n> IDR` or `not armed`, plus what each strategy will do), then **start**, then **watch** |
| 08:45 | preflight | trading day (repo calendar); previous session's daily bar present, not a zero-volume placeholder, for every paper spec's symbols; `SB_ACCESS_TOKEN` present and not expiring within 1.5h; `SB_BEARER_TOKEN` present (warn only); `runs/KILL` absent; strategy binaries present or `go` available. Any failure: alert, skip the day |
| right after | start | `make build-strategy`, then `python -m strategy_fit.paper_day --strategy <id>` for each spec with status `paper` (emit target + `live-session --sink paper --source live`), so it runs before 09:29 |
| to 09:45 | watch | every 15s: status file stale > 2 min alerts; dead process is restarted only if safe (below); at 09:45 anything still running gets a graceful SIGINT |
| 16:20 | `runner post-close` | `refresh-prices`, `recon` (only if `SB_ACCOUNT_NO` and `SB_TRADING_ACCESS_TOKEN` are set), `paper-scorecard` per paper strategy, dashboard rebuild (`HEALTHCHECK=skip`), a summary alert, and (if live was armed or ran) a **LIVE summary** alert |

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

**A live session is never restarted, by anything.** The restart rule above is paper-only; the live
watcher has no spawn call at all (a test pins that).

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
`SB_ACCOUNT_NO` + `SB_TRADING_ACCESS_TOKEN` (recon, read-only; and **required for live**),
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, or `RUNNER_ALERT_WEBHOOK_URL` as fallback. Token expiry
is read from the JWT `exp` claim in-process and only ever shown as "expires in 3.2h". A token that
is not a JWT gives a warning, not a failure (for paper; live requires a readable `exp`).
`ALGO_LIVE`, `ALGO_CONFIRM` are stripped from every child except the one live-session process
(below); `SB_TRADING_ACCESS_TOKEN` reaches only `recon` and the live supervise wrapper/live-session.
Do not put `ALGO_LIVE`/`ALGO_CONFIRM` in `.env.local`: they are ignored (stripped), the runner sets them itself.

## Token rotation

`research/token_rotation/` keeps `.env.local` fresh so the 08:45 preflight does not fail on an
expired web token (plan 03, phase 5). It rewrites only the token keys, atomically, mode 0600.

```sh
make token-status      # per token: expires in Xh / expired / unknown / not set (never a value)
make token-refresh     # rotate now (first real run, or after a manual re-login)
make token-ensure      # rotate only if < TOKEN_MIN_HOURS (12) left; what the runner calls
```

One-time seed: `.env.local` must contain `SB_REFRESH_TOKEN` (main session) alongside the existing
`SB_ACCESS_TOKEN`/`SB_BEARER_TOKEN`; optionally `SB_TRADING_REFRESH_TOKEN` (recon) and
`SB_USERNAME`/`SB_PASSWORD`/`SB_PLAYER_ID` (headless re-login if the refresh token dies; skip these
to keep the password off disk, at the cost of a manual re-login at refresh-token death).

Exit codes: 0 ok · 1 main session needs a manual re-login · 2 transient network error (retry) ·
3 ok but a warning/action is pending (e.g. trading PIN login needed).

**Still manual:** trading session after its refresh token dies (needs the PIN, never stored);
revoked player_id trust (approve once on the phone); the one-time `SB_REFRESH_TOKEN` seed. The
tool reports these as `ACTION REQUIRED ...` and warns when a refresh token has < 72h left and no
re-login credentials are configured. Do not run it from two places at once (rotation invalidates the
old refresh token; a file lock guards concurrent local runs only).

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
| `<date> morning plan: ARMED: <id>, max <n> IDR` / `... not armed` | the 08:45 push. Below it: `LIVE <id>` (will go live), `NOT live <id>: <why>` (armed but blocked, or not armed), `paper <id>`, and `WARNING` lines (below) |
| `WARNING <id>: holds ... LIVE and is NOT going live today` | a candidate holds a live position in its stores but is not armed today, so nothing will sell it. Arm it for today (exit legs need an arm too) or sell by hand |
| `LIVE session started: <id>, buys at most <n> IDR` | the one live-session was spawned |
| `LIVE ORDER submitted / partial / filled / rejected / cancelled: ...` | one push per order event: side, lots, symbol, price, notional, avg fill, fees, the broker's reject reason. `cancelled` means the order ended with no (or a partial) fill when the session stopped: live-session cancels what is resting at stop; confirm in the app |
| `LIVE not started, too late` | the 08:45 start missed `REBALANCE_AT` minus 60s; nothing ran |
| `LIVE refused at the guard` / `LIVE not started` | the guard re-verified and said no (arm file gone or changed, kill file, spec no longer `candidate`); nothing ran |
| `LIVE session died; NOT restarted` | exit code, the orders in the store that are neither filled nor rejected, and the result of one read-only `make recon`. **Check the Stockbit app for resting orders.** Nothing is retried |
| `DISARMED while live; stopping the session` | you removed the arm file while it ran: graceful stop (resting orders cancelled; fills stay) |
| `<date> LIVE summary` | after post-close: orders by outcome, bought vs the cap, fees, position now, exit code, recon note after a failure; also reports `armed but NO live session ran today` |

## Idempotency

Rerunning is safe. `start` holds a pid lock and records each strategy in the state file; a **paper** strategy
already `started` or `done` today is not started again. `post-close` runs once per day unless `--force`.
Paper restarts are the only way a second paper store appears for a day.

A **live** strategy is stricter: ANY state-file entry for it today (`starting`, `started`, `done`, `failed`,
`stopped`, `refused`), or any live store dated today, means `start` will not start it again, whatever its status.
The entry is written before the spawn, so a crash in between also blocks a second start. If a live start was
refused before anything was placed (check `runs/` for a live store of that strategy dated today, and the
Stockbit app), deliberately retrying is: delete that strategy's entry from `runs/runner/state-<date>.json`
and rerun `python -m runner morning` before 09:29. That is manual surgery on purpose.

## Failure modes

- **Token lifetime**: without rotation the access token is a web copy that expires and preflight fails.
  See "Token rotation" above: once wired in (`ensure_tokens` before preflight) the main session
  self-heals; only the manual cases listed there still need you.
- Mac asleep / logged out / time zone not WIB: no run. Agents need a logged-in user session.
- Daily bar stale because the previous 16:20 failed: preflight fails; run `make refresh-prices`.
- `.env.local` unreadable or malformed: the runner reports the line number only.
- Telegram/webhook down: the alert is still in `runner.log`.

## Live mode (guarded, per-day arming)

`runner/modes.py:decide_mode` is the single place that decides each strategy's mode (`paper`,
`skip` or `live`). A strategy goes **live only if ALL hold**:

1. its spec status in `research/strategy_fit/emitter_specs.py` is `candidate` (a `paper` spec can never go live, even if named);
2. a valid arm file for **today** names it: `runs/arm/ARM-<YYYY-MM-DD>.json`, valid only for its own date;
3. the day's preflight passed, including a valid trading token (`SB_TRADING_ACCESS_TOKEN`: a JWT with at least 1.5h left, its account claim equal to `SB_ACCOUNT_NO`) and `SB_ACCESS_TOKEN`;
4. `runs/KILL` is absent;
5. no live session for it has started today (state entry or live store).

Otherwise it runs paper (paper specs) or is skipped, and the log and the 08:45 push say why.

### Arming procedure

```sh
make runner-dry-run RUNNER_DATE=2026-10-16                       # read what WOULD run live, and why
make runner-arm STRATEGY=<id> MAX_IDR=<n> DATE=2026-10-16        # DATE defaults to today
make runner-disarm DATE=2026-10-16                               # revoke
```

- `MAX_IDR` is **whole rupiah, no default**, and the most the strategy may **buy** that day. It is refused above the hard ceiling `runner/arm.py:HARD_MAX_IDR` (15,000,000 IDR, the sleeve's own cash: Carina rejects more with `C-006`). Size it for the order: the emitter's one lot is sized at about **1.3x its price**, and the runner refuses a target that would buy more than the cap (exit 7, nothing submitted).
- The arm file is refused for a spec that is not `candidate`, a past date, a non-trading day, or a bad amount. Arming a second strategy the same day adds to the file. It is valid for its own date only: arm again for every day you want the bot to act, **including the exit day of a multi-day hold** (an exit leg needs an arm too; if you forget, the 08:45 push carries a `WARNING ... holds ... LIVE ... NOT going live today` line).
- `make runner-disarm` removes the file. If a live session is already running, the watcher notices within 15s and sends it a graceful stop (resting orders cancelled; fills stay).
- Order of events on an armed day: 08:45 preflight (armed candidates need their previous daily bar; live-only problems are warnings and keep it off live) -> summary push -> `make build-strategy` -> **one** supervise wrapper -> it re-authorises from the files as they are then, emits the target (`strategy_fit.target_emitter emit`), refuses a target larger than the cap, then starts `live-session` with `ALGO_LIVE`/`ALGO_CONFIRM` in that process's environment only -> live-session waits for 09:30.

### What enforces the rupiah cap, and where

| layer | what it does |
|---|---|
| `arm.py` | refuses an amount outside 1..15,000,000 at write time, and the same at read time (a hand-edited file over the ceiling is invalid) |
| `supervise.py` | refuses (exit 7) to start live-session when the emitted target would buy more than the cap (`base_notional_idr` x sum of weights) |
| `live-session --max-buy-notional-idr N` | new flag, forwarded to strategy-run; the runner always passes the armed cap. `--equity-idr` is also the cap, and the risk fractions are of it (1.0 per order and per name, 2.0 per day for an exit leg, loss limit 5% of the cap) |
| `strategy-run --max-buy-notional-idr N` | enforced in the risk layer (`risk.WithMaxBoughtNotionalIDR`): an order that would push the cumulative **bought** notional over N is refused before it reaches the sink. Seeded from buys already in today's stores, so a restart cannot reset it. Sells never count and never free budget; a cancelled buy stays consumed; a buy is refunded (cap, exposure and day notional alike) only when the error PROVES nothing reached the market (typed broker refusal, 4xx other than 408, sink shutting down); a timeout, 5xx or unknown error may mean Carina accepted it, so it stays consumed |

**The cap exists only when `--max-buy-notional-idr` is passed.** The runner always passes it; a manual `live-session` / `strategy-run` run without that flag is uncapped by this mechanism (only the fraction-of-equity risk limits apply).

### What the guard enforces

`guard.py` has two doors. `validate_argv` (paper) still refuses `live-session`, `strategy-run`, `--retry`, `--sink live`, `ALGO_*` and every non-paper spec, and is what `Executor.run`/`spawn` and the paper supervise path use. The live door builds a command only from a sealed `LiveToken` that `decide_mode` issues; the resulting `LiveCommand` is re-verified (arm file, kill file, spec status, exact argv: `--sink live`, `--max-restarts 0`, no `--retry`, cap flags equal to the armed cap, nothing unexpected after `--`) when spawned and again inside the supervise process. `ALGO_LIVE`/`ALGO_CONFIRM` are set in exactly one function, for that one child.

### No restart, and what happens if it dies

`--max-restarts 0` goes to live-session, the runner has no spawn path for a live process, and a rerun of `start` never starts it again (see Idempotency). If the live process dies (any exit code, or killed): `ALERT ... LIVE session died; NOT restarted` with the orders in the store that are neither filled nor rejected, plus one read-only `make recon` (the broker holdings versus the stores). **Then it stops.** Look at the Stockbit app for resting orders; resolve by hand per `first-live-order.md` / `live-session.md`. `--retry` stays forbidden to the runner.

### Alerts and summaries

Every order event is pushed once (see the alerts table), sourced from live-session's own store (`order_intent`, `fill`, `order_reject`; a store has no `cancelled` row, so an order that ends without a full fill is reported `cancelled` once the session has ended). Everything is redacted by `runner/redact.py` and built from symbols, lots, prices, counts and the cap: no token and no account number (the account is masked as `[ACCOUNT]` even in the dry-run and the log). The end-of-day LIVE summary follows the normal post-close summary.

### Dry run

`make runner-dry-run RUNNER_DATE=<date>` simulates that morning (that date, 08:45 clock) and prints, per strategy, `LIVE <id>: <reason>` with the emit command, the cap, the exact live-session command (account masked), the environment rules and "no restart", or `NOT live <id>: <reason>`. It executes nothing and does not run the day's preflight (it says so; the live-only checks are evaluated).

### Residual limits

- A store records no cancel, so "cancelled" is inferred when the session ends (and at the 09:45 deadline stop, at post-close).
- The runner cannot see the broker's side during the run: a fill recorded late by Carina shows up in recon, not in the push.
- The cap counts buy **intents** (not refused or rejected ones), a conservative over-count by design.
- Token and account checks are in-process reads of the JWT claims (signature not verified): live-session's own `data.acn` guard and the position gate remain the authoritative checks.
- Nothing here validates a strategy. Promoting a spec to `candidate` is a deliberate edit of `emitter_specs.py`; no spec is a candidate today, so `make runner-arm` refuses everything until you promote one.
