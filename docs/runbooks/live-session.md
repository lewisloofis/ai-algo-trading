# Live session runner (`live-session`)

Plan 18 B7, local scope. One command replaces the hand-typed
pre-snapshot / strategy-run / post-reconcile sequence in
`first-live-order.md` §13-§16. It places no orders itself: the only order path is
the `strategy-run` child, which keeps its own `ALGO_LIVE` + `ALGO_CONFIRM`
double-arm, token-account guard and position gate.

## One line

Tokens come from the environment (copied from the web as before); the arming
pair is exactly strategy-run's.

```sh
export SB_ACCESS_TOKEN=... SB_TRADING_ACCESS_TOKEN=...   # never on a command line
export ALGO_LIVE=true ALGO_CONFIRM=I_UNDERSTAND_LIVE_TRADING

live-session --account 008488L --equity-idr 15000000 \
  --target-portfolio runs/target_roundtrip_sell_2026-10-15.json \
  --rebalance-at 13:45 --symbols GOTO \
  --prior-stores 'runs/2026-10-1[45]-roundtrip-*.db'
```

Optional: `ALGO_ALERT_WEBHOOK_URL` (internal http(s) endpoint; the URL is a secret,
never logged). Extra strategy-run flags go after `--`
(`-- --limit-price-idr 28`).

Rehearsal, no trading token and no arming (replay + paper sink):

```sh
live-session --sink paper --target-portfolio ... --rebalance-at 13:45 --segments 'segments/2026-10-14/*.binpb.zst'
```

### Paper on live prices (`--sink paper --source live`)

Shadow run on the real feed with simulated fills. Needs only `SB_ACCESS_TOKEN`
(market data): no `SB_TRADING_ACCESS_TOKEN`, no `ALGO_LIVE`/`ALGO_CONFIRM`, no
`--account`, no broker reconcile. `--equity-idr` is still required (risk caps are
fractions of it). strategy-run's paper sink fills like `PaperSink` in taker mode:
a buy fills at the best offer, a sell at the best bid, immediately and in full (no
queue, no partial fills), fee 0.15% buy / 0.25% sell. With no trading token the
risk wrapper's market gate is session-only (IDX calendar + Exodus session);
tradable / suspension / ARA-ARB are not checked.

```sh
export SB_ACCESS_TOKEN=...
live-session --sink paper --source live --equity-idr 15000000 \
  --target-portfolio runs/target_wifi_momo_h5_2026-10-16.json \
  --rebalance-at 09:30 --symbols WIFI
```

Paper stores are named `runs/<date>-<strategy>-paper-aN.db` and the prior-stores
glob is `runs/*-<strategy>-paper-a[0-9]*.db` (every day), so paper and live never
share a glob. Pre-run and the summary report the paper position netted across all
days' stores (a multi-day hold buys in one store and sells in a later one). Only a
target whose emitter sidecar says `spec_status: candidate` (or that has no
sidecar) may arm a live sink; a `paper` spec is refused. The one-command daily
wrapper is `make paper-day STRATEGY=<id>`; results are scored with
`make paper-scorecard STRATEGY=<id>`.

## What it does

1. **Preflight** (all failures listed at once; nothing is created if any fails):
   token `data.acn` equals `--account`; `ALGO_LIVE`/`ALGO_CONFIRM` armed;
   target parses and `as_of <= --rebalance-at < complete_by`; `--rebalance-at`
   is **required** (no 15:00 default), inside a continuous-trading session, and
   at least `--min-lead` (60s) in the future; today is an IDX trading day;
   kill-switch file (`runs/KILL`) absent (an unreadable path fails closed);
   strategy-run binary readable (its sha256 is checked against the `binary_hash`
   in every run row afterwards, and against `--expect-binary-sha256` if given).
2. **Pre-snapshot** (live sink). "Expected" is exact: for every symbol, the
   broker's balance equals the net lots from this account's `live` fills in the
   prior stores (`--prior-stores` + every `runs/*-<strategy>-aN.db`) + manual
   trades + broker-imported ledger. That is why a broker holding GOTO is fine
   when yesterday's store explains it, and refused when nothing does. Any other
   row, or an unreadable broker, refuses (exit 2).
3. **Run**: `strategy-run` with a fresh store `runs/<date>-<strategy>-aN.db`
   (N = next free; never reused) and `--prior-stores` covering earlier stores, so
   attempted windows are recovered. SIGINT (graceful drain) is sent
   `--stop-after` (3m) after `--rebalance-at`, or on your Ctrl-C.
4. **Restart**: a non-zero exit restarts up to `--max-restarts` (2) times, each
   with a new store and a re-run preflight + pre-snapshot (so fills the crashed
   attempt did place must be in its store or the restart is refused). Never
   after the kill switch or past the stop deadline. `--retry` is never added: on
   exhaustion the exact `--retry N` command is printed in the alert, and
   resubmitting stays your decision.
5. **Post-reconcile**: same check, now including this session's stores, retried
   `--recon-tries` (3) times `--recon-settle` (5s) apart because the broker can
   lag a fill. Then a one-screen summary (orders, fills, fees, rejects, recon,
   binary hash, result).
6. **Alerts** (stderr always; webhook if configured; bodies carry no tokens and
   no account number): `refused`, `crash`, `repeated_crash`, `kill_switch`
   (file appeared, or an in-process trip seen as rejects), `recon_divergence`,
   `order_reject`, `binary_hash`, `zero_record` (warn only: a clean live run that
   attempted no orders; normal when the book already equals the target).

## Exit codes

| code | meaning |
|---|---|
| 0 | clean run, recon MATCH, no rejects |
| 1 | recon divergence (or broker unreadable), any order reject, kill switch tripped |
| 2 | refused to start, crashed past the restart budget, or integrity failure (binary hash mismatch / clean exit with no run row) |

## After a non-zero exit

Do not just re-run. Read the summary, check the app and
`account-recon --stores 'runs/*.db' --acc-no <acct> --sink-kind live` (it also
imports manual trades into the ledger), then re-run `live-session` (fresh store
automatically). Use `--retry N` only for a deliberate same-day resubmission
(`first-live-order.md` §11).

## Not covered yet

Cloud deploy, supervision outside an operator shell, and a freshness
alert on `<store>.status.json` (the heartbeat is written next to each store but
nothing watches it yet).
