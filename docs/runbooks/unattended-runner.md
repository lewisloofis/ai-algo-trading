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
| 08:45 | `runner morning` | **token rotation** (below), **preflight**, then the **summary push** (`ARMED: <id>, max <n> IDR` or `not armed`, plus what each strategy will do), then **start**, then **watch** |
| 08:45 | token rotation | on a trading day, if `.env.local` exists and `--no-env-file` is not given: `token_rotation.ensure_tokens(.env.local)` refreshes sessions with < 12h left (network, rewrites `.env.local`), and the new values are applied to the runner's in-memory env so every later phase and child uses them. **INFO** (log only) names the tokens rotated, never values; **ALERT** `token rotation: ACTION REQUIRED` for each manual step; **WARN** for warnings. A crash in the rotation is an ALERT and never stops the run: preflight then judges whatever tokens exist and still fails closed. A dry run shows the step and does not call it |
| 08:45 | preflight | trading day (repo calendar); previous session's daily bar present, not a zero-volume placeholder, for every paper spec's symbols; `SB_ACCESS_TOKEN` present and not expiring within 1.5h; `runs/KILL` absent; strategy binaries present or `go` available. Any failure: alert, skip the day |
| right after | start | `make build-strategy`, then `python -m strategy_fit.paper_day --strategy <id>` for each spec with status `paper` (emit target + `live-session --sink paper --source live`), so it runs before 09:29 |
| to 09:45 | watch | every 15s: status file stale > 2 min alerts; dead process is restarted only if safe (below); at 09:45 anything still running gets a graceful SIGINT |
| 16:20 | `runner post-close` | **token rotation retry** first: the same `ensure_tokens` call, a no-op unless a session is stale or under 12h. It covers a morning network miss: on 2026-10-16 the 08:34 trading refresh was skipped and recon later ran on an expired token. Alerts and the state key are labelled `(post-close)`, and it is skipped when post-close is skipped. Then `refresh-prices`, `recon` (only if `SB_ACCOUNT_NO` and `SB_TRADING_ACCESS_TOKEN` are set), `paper-scorecard` per paper strategy, dashboard rebuild (`HEALTHCHECK=skip`), then ONE **review message** (rules over local artefacts + a short summary; see Daily ops review) that carries the day's summary and, if live was armed or ran, the LIVE summary |

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

`SB_ACCESS_TOKEN` (market data, chartbit, emitten: paper-day and refresh-prices), optional
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
`SB_ACCESS_TOKEN`; optionally `SB_TRADING_REFRESH_TOKEN` (recon) and
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
- `runs/runner/review-<date>.json` / `.txt`: the post-close review (redacted facts as sent to the summary writer, and the summary).
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
| `daily summary` | the single post-close message (the review, below): flags first (ALERT, WARN), then sessions, fills, paper position, step results, recon and scorecard verdicts, and the LIVE summary lines when live ran. Prefixed `[ALERT]` when any rule raised an ALERT |
| `token rotation: ACTION REQUIRED` / `token rotation warnings` | a manual re-login step is needed / a refresh token is close to death (value-free text from `token_rotation`) |
| `<date> morning plan: ARMED: <id>, max <n> IDR` / `... not armed` | the 08:45 push. Below it: `LIVE <id>` (will go live), `NOT live <id>: <why>` (armed but blocked, or not armed), `paper <id>`, and `WARNING` lines (below) |
| `WARNING <id>: holds ... LIVE and is NOT going live today` | a candidate holds a live position in its stores but is not armed today, so nothing will sell it. Arm it for today (exit legs need an arm too) or sell by hand |
| `LIVE session started: <id>, buys at most <n> IDR` | the one live-session was spawned |
| `LIVE ORDER submitted / partial / filled / rejected / cancelled: ...` | one push per order event: side, lots, symbol, price, notional, avg fill, fees, the broker's reject reason. `cancelled` means the order ended with no (or a partial) fill when the session stopped: live-session cancels what is resting at stop; confirm in the app |
| `LIVE not started, too late` | the 08:45 start missed `REBALANCE_AT` minus 60s; nothing ran |
| `LIVE refused at the guard` / `LIVE not started` | the guard re-verified and said no (arm file gone or changed, kill file, spec no longer `candidate`); nothing ran |
| `LIVE session died; NOT restarted` | exit code, the orders in the store that are neither filled nor rejected, and the result of one read-only `make recon`. **Check the Stockbit app for resting orders.** Nothing is retried |
| `DISARMED while live; stopping the session` | you removed the arm file while it ran: graceful stop (resting orders cancelled; fills stay) |
| `LIVE summary` (inside the daily message) | orders by outcome, bought vs the cap, fees, position now, exit code, recon note after a failure; also reports `armed but NO live session ran today` |

## Daily ops review (post-close)

At the end of post-close the runner builds **one** message (`<date> daily summary (...)`) from local
artefacts only; it replaces the separate summary and LIVE-summary pushes. It is informational: the LLM
step can never change runner behaviour, and any failure in it falls back to plain text.

1. **Rules** (`runner/review_rules.py`, deterministic): facts and severity-tagged flags from the day's
   runner log, state file, live and paper stores, `runs/recon/*.json`, paper scorecards,
   `research/out_cost_report/cost_report.json` (if present) and token expiry (durations only, decoded
   in-process). ALERT: preflight failed or missing, fill >= 300 bps from the decision price, position
   open more than its planned hold, recon mismatch or a trade the bot recorded that the broker lacks,
   live cap exceeded, armed but no live session, failed or errored session, expired token, a manual
   token step. WARN: fill >= 100 bps from the decision price, position on its exit day still open,
   live cap >= 90%, unresolved or rejected order, missing/stale/unchecked recon, missing or stale
   status files, a session that did not start or never finished, token < 24h left, scorecard verdict
   STOP, failed post-close step, unreadable source. Planned hold = the spec's `hold` sessions after the
   entry day. The paper scorecard has no backtest expectation field, so "drift" is its verdict only.
2. **Redaction** (`runner/review_redact.py`): `runner/redact.py` (secret env values, JWTs, bearer
   tokens, webhook URLs, long blobs) plus account numbers, order IDs (UUIDs, long digit runs, values
   under `order_id`/`account`/`broker_id`-style keys) and broker IDs. Applied to every string and every
   sensitive key before anything leaves the machine, and again to the model's reply.
3. **Summary** (`runner/review_summary.py`): the redacted facts JSON goes on stdin to
   `claude -p --model haiku --tools "" --system-prompt <fixed> --no-session-persistence
   --disable-slash-commands --strict-mcp-config --permission-prompts none`, in an empty temp directory,
   60s timeout, with only PATH/HOME/locale and `CLAUDE_*`/`ANTHROPIC_*` in its environment (never the
   runner's `SB_*` or Telegram values). No tools means it cannot read files or run commands. The reply
   is used only if every number in it appears in the facts and, when there is an ALERT, it mentions
   ALERT; otherwise the deterministic text below is sent. Fallback also when `claude` is missing, times
   out, exits non-zero or returns nothing, or `RUNNER_REVIEWER=off`.
4. **Output**: the Telegram/webhook message, plus `runs/runner/review-<date>.json` (the redacted facts
   and which writer produced the text) and `runs/runner/review-<date>.txt`.

Config (env or `.env.local`): `RUNNER_REVIEWER=claude|off` (default `claude`), `RUNNER_REVIEWER_MODEL`
(default `haiku`).

**What leaves the machine, and where:** exactly the contents of `review-<date>.json` (redacted): strategy
ids, symbols, lots, prices, fill-vs-decision deviations in bps, rupiah amounts (bought vs cap, fees),
session/step statuses, recon differences (symbol and lots), scorecard verdicts and bps, cost-report
aggregates, token names with hours left, flag texts and the ALERT/WARN lines of the runner log. It goes
to Anthropic through the Claude Code login of the user the runner runs as. No token, account number,
order or broker ID, `.env.local` content or chat id is sent. Set `RUNNER_REVIEWER=off` to send nothing.
Residual risk: redaction is pattern-based, so a new identifier format would not be caught; read
`review-<date>.json` after the first live days.

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

## When launchd is blocked (managed Mac)

If `make runner-install` fails with `Operation not permitted` / `Bootstrap failed: 5`, the
device policy is blocking new LaunchAgents. Do not work around the policy. Instead, run the
whole day in one foreground command each trading morning, before ~09:15 WIB:

    make runner-day

It runs token rotation, preflight, the paper sessions and watch, then waits until 16:20 for
post-close and the review message. `caffeinate -i` keeps the Mac from idle-sleeping while it
runs (no sudo). Keep the Mac on power, the lid open and the terminal window open; closing it
stops the run. Re-running the same day is safe (lock + per-day state).
