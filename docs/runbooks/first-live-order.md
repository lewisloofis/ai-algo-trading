# Runbook: First Live Order — Account Attribution and the Sleeve's Trading Limit

> **Status, 2026-09-24: WIRED.** `strategy-run --sink live` now exists
> (`strategy/cmd/strategy-run/main.go`) — see §1 for the exact command line.
> Order placement can be executed following this runbook end to end. The
> "Layer 1 / Layer 2" split below is kept as written because it still
> describes what is read-only vs. what places a real order; only the
> "NOT YET WIRED" framing is stale and is corrected in §1.
>
> **Executed 2026-10-13: §2–§9 PASSED; §10 (trading-limit probe) NOT YET
> RUN.** The order landed in the sleeve `008488L`, and the core book
> `0001958` did not move. See §13 for the full log, the numbers, and the five
> bugs it surfaced, all fixed the same day.

## 0. What this settles, and why it needs a real order

Two facts are unproven, both load-bearing for [plan 18 §3.4](../plans/18-alpha-and-infrastructure-split.md) and [ADR-0017](../adr/0017-sleeve-is-a-funded-sub-account.md):

1. **Which account absorbs an order.** `strategy/live/doc.go`'s "open question"
   section states it plainly: none of Carina's order-mutation or order-query
   endpoints accept an account-scoping parameter, unlike `GET /portfolio/v2/list`
   and `GET /balance/withdrawable`, which do. One trading token addresses both
   the sleeve (`008488L`) and the pre-existing core book (`0001958`). Whether an
   order lands in one or the other is currently **assumed**, not measured.
2. **Whether the sleeve has a trading limit of its own.** ADR-0017 measured that
   `GET /balance/cash/info`'s `trade_limit` reports the core book's figure
   regardless of `acc_no`, and moving cash into the sleeve reduced that figure
   by exactly the amount moved — evidence the sleeve did *not* acquire an
   independent limit, but not proof, since that endpoint cannot address the
   sleeve at all. ADR-0017 explicitly defers this to "an order attempt... gated
   behind B4."

Neither is answerable by reading an endpoint. Both need one real order, placed
deliberately, at the smallest possible size, with a before/after diff on both
accounts. That is this runbook.

## 1. Current tooling state — read this before doing anything else

| Capability | State |
|---|---|
| `market-probe` (pre-trade gate check) | **Wired, safe to run today.** GET-only. `strategy/cmd/market-probe` |
| Position snapshot, one account at a time | **Wired.** `strategy/cmd/account-recon` (`PortfolioList` + `OpenOrders`, reconciled against a post-trade store) |
| Broker reconciliation (all stores + manual + broker-import vs broker holdings, trade-by-trade vs broker history) | **Wired, 2026-10-14.** `strategy/cmd/account-recon --stores` (`make recon`); see §3a. Read-only. |
| Pre-trade position gate | **Wired, 2026-10-14.** `strategy-run --sink live` refuses to arm when the broker's holdings differ from the position it would load; see §3a. |
| Cash / withdrawable balance snapshot | **NOT YET WIRED as a CLI.** No binary in `strategy/cmd/` calls `account.Client.Withdrawable` or `PortfolioSummary`. See §3 for how to obtain it anyway (the package itself is read-only and safe). |
| Double-arm gate (`ALGO_LIVE` / `ALGO_CONFIRM`) | **Implemented and wired.** `strategy/live/armed.go` (`ArmingFromEnv`) is read by `strategy-run`'s `--sink live` path (`newInnerSink` → `live.NewClient`); an unarmed process refuses to construct the sink at all. |
| Placing a real order | **WIRED, 2026-09-24.** `strategy-run --sink live` constructs `strategy/live.Sink` via `live.NewClient` and always wraps it in `strategy/risk.RiskSink` (limits, kill switch, the real endpoint-backed market gate) before it reaches `engine.Run` — see the exact command line below. `Baseline()` runs at startup and `ReconcileTerminal()` at shutdown, with any divergence logged loudly (not silently dropped). |

Because order placement now goes through one real binary, this runbook is
still written in two layers, but Layer 2 is now executable:

- **Layer 1 — read-only or local configuration.** §2 (preconditions), §3
  (pre-snapshot), §4 (arm). Zero order risk.
- **Layer 2 — places real orders.** §5–§8 (place, confirm, cancel, diff) and
  §9 (the trading-limit probe). Executed via `strategy-run --sink live`, one
  invocation per attempt (this binary places whatever its strategy decides to
  place for one rebalance window — see below for how to point it at a single,
  minimal order).

  **The exact command line** (fill in the bracketed values; do not improvise
  flags beyond what is listed here — `strategy-run -h` is the source of
  truth if this drifts):

  ```
  ALGO_LIVE=true ALGO_CONFIRM=<verbatim live.LiveConfirmPhrase, copied from strategy/live/armed.go — never retyped from memory> \
  SB_ACCESS_TOKEN=<fresh Exodus token> \
  SB_TRADING_ACCESS_TOKEN=<fresh Carina trading token> \
  strategy-run \
    --source live --symbols <SYMBOL> \
    --sink live --account 008488L \
    --equity-idr <sleeve withdrawable balance from §3, in whole IDR> \
    --strategy rebalance --target-portfolio <path to a target_portfolio.json sized for the smallest possible order — see §5> \
    --limit-price-idr <tick-valid price ~12% below market, §5 only — omit for §8's fill variant> \
    --rebalance-at <HH:MM WIB, a minute or two from now, inside a continuous session> \
    --store runs/<YYYY-MM-DD>-<n>.db \
    --prior-stores 'runs/<YYYY-MM-DD>-*.db' \
    --kill-switch-file <path to a file you can create by hand to trip the kill switch mid-run>
  ```

  Notes on flags that are easy to get wrong:

  - `--prior-stores` is **required** with `--sink live`. It must glob every
    earlier store file from the same trading day; on restart after a crash,
    the intents recorded there are not resubmitted. Matching nothing is fine
    on the day's first run. Use a fresh `--store` file per run (one store
    file per run is the store's own convention). It also feeds the position
    gate (§3a): the glob must cover every store that built a position still
    open at the broker, or the run refuses to start.

  - `--source live` is required for `--sink live` — `strategy-run` refuses
    `--sink live` with `--source replay` outright (never place a real order
    against replayed data).
  - `--account` has no default. An empty value is refused at startup rather
    than silently falling back to whichever account the trading token's own
    login addresses — see plan 18 §3.4.
  - `--equity-idr` also has no default and is **not** fetched automatically:
    pass the exact `withdrawable` figure from §3's pre-snapshot. This is
    deliberate — `strategy/risk`'s own doc comment requires the caller to
    source sleeve equity explicitly, never from an endpoint this binary
    trusts on its own.
  - Every `--source live` run — `--sink paper` or `--sink live` — is wrapped
    in `risk.RiskSink` with the real market gate. There is no way to run
    `--source live` ungated; use `--sink paper --source live` first as a
    shadow run if you want to see the gates fire without placing anything.
  - `--strategy rebalance --target-portfolio <path>` is what actually decides
    what gets bought/sold and at what size — see §5 for how to hand-write a
    minimal one-symbol, one-lot target portfolio for the diagnostic steps
    below rather than pointing this at a real strategy's output.
  - `--limit-price-idr <p>` is a diagnostic override (default 0 = off) for
    §5's unfilled step: `rebalance` otherwise always prices at the last
    observed price, which would make that step a marketable buy. With it
    set, the one order is submitted at exactly `p` and sized against `p`.
    Refused at startup unless `--strategy rebalance`, the portfolio has
    exactly one target, and `p` is tick-valid (and > 0). It does not bypass
    anything downstream: the risk gate and the market ARA/ARB band check
    still apply and still deny an out-of-band `p`. A loud `WARNING` is
    logged at startup when it is in force. Do **not** pass it for §8.
  - `--rebalance-at HH:MM` (WIB) opens the rebalance window. Its default,
    15:00, is after continuous trading ends, so a run without it never
    trades — always pass an in-session time (e.g. two minutes from now).
  - **Stopping a run: Ctrl-C (SIGINT) or SIGTERM.** With `--sink live`, the
    first Ctrl-C **cancels every resting order** and refuses new ones. It then
    waits up to 30s, with the feed and order watcher still running, until each
    order is terminal and any last fill is recorded. Then `ReconcileTerminal`
    + `Close`, the final status write and `FinishRun` run. An order still not
    terminal after that is logged as `RECONCILE DIVERGENCE — not_terminal=[...]`;
    the run then ends as **`error`**, and you must cancel that order in the
    Stockbit app. A second Ctrl-C force-exits immediately and skips all of
    this. While the process runs, the live sink also auto-cancels any order
    still OPEN after 5 minutes (`live.DefaultOpenTimeout`).
  - The order id is `rebalance-<SYMBOL>-<date>`: **one rebalance order per
    symbol per trading day.** §5 and §8 on the same day must use different
    symbols (a same-symbol rerun is skipped via `--prior-stores`, or reuses
    the same `ui_ref`).
  - `--kill-switch-file` is optional but strongly recommended for a first
    live order: creating the file (`touch <path>`) mid-run trips the kill
    switch before the next `Submit` call, on top of the manual
    `live.Client.Cancel` §6 already requires you to know.

## 2. Preconditions

- [ ] **Fresh tokens.** Mint `SB_ACCESS_TOKEN` (Exodus, main session) and
      `SB_TRADING_ACCESS_TOKEN` (Carina, trading session) immediately before
      starting — do not reuse a pair from an earlier session. (Plan 18 §5 B2's
      own verification log records a `market-probe` run that hit an expired
      token mid-session and returned three `unknown` fields; the same failure
      here would make an order attempt uninterpretable, not just blind.)
- [ ] **Market session confirmed by the gate, not the wall clock.** Run
      `market-probe --symbols <SYMBOL> --price <near-market price>` and require
      exit 0 with every field `allow`, in particular `market_open=allow` with
      `state_name` in a continuous-auction session — plan 18 §5 B2 found that
      `/market-time`'s `status` field alone reads `STATUS_OPEN` even during the
      lunch break; `market.Gate` (and this check) reads `state_name` instead.
- [ ] **Gate probe on the actual candidate order.** Also run `market-probe`
      with the exact symbol and price band you intend to use in §5/§6/§9 (a
      far-from-market price and a near-market price) and confirm neither is
      denied by `within_band` before proceeding — a band-denied price at
      submission time would confound "was it rejected for size/account" with
      "was it rejected for price."
- [ ] **Sleeve funding re-confirmed.** ADR-0017 recorded 15,000,000 IDR in
      `008488L` as of 2026-09-14; re-read it via §3 immediately before this
      runbook, since it may have drifted (fees, a manual transfer, etc.).
- [ ] **Operator acknowledges there is no dry run.** `strategy/live/doc.go` is
      explicit: this package refuses to construct at all unless armed, and once
      armed there is no simulate-only mode. Every `Place` call from this point
      is a real order with real money.
- [ ] **A stop mechanism is understood before starting**, even though it is
      manual here: know in advance how to cancel an order by hand
      (`live.Client.Cancel`) within seconds of submission.
      `strategy-run --sink live` runs through the real `engine.Run` +
      `RiskSink` path, so the external kill-switch file
      (`strategy/risk/killswitch.go`, `--kill-switch-file`) is also available
      — but it stops *new* submissions, not an order already resting;
      cancellation of a resting order is still manual.

## 3. Pre-snapshot both accounts

Do this for **both** `008488L` (sleeve) and `0001958` (core book) — the whole
point is a before/after diff on both, not just the one expected to move.

**Positions** (wired, read-only):

```
account-recon --store <store-path> --acc-no 008488L  --sink-kind live --format json > pre-008488L.json
account-recon --store <store-path> --acc-no 0001958   --sink-kind live --format json > pre-0001958.json
```

Use the same `--store` path for both invocations. A fresh or mostly-empty
store is fine and expected here — every broker holding will report as
`held_at_broker_only`, which is not a MATCH failure to worry about for this
purpose; you only need the broker-reported numbers (`BrokerSymbols`, each
position's quantity/cost) this run captured, not a clean verdict. Record the
full JSON, not just the verdict line.

**Cash / withdrawable balance** (NOT YET WIRED as a CLI — §1). Obtain it by
calling `strategy/account.Client.Withdrawable` directly. The package is
structurally read-only (`account/readonly_test.go` fails the build if a
non-GET method is ever added), so writing and running a short-lived script or
REPL snippet against it carries no order risk:

```go
client, err := account.New(host, "008488L", tokenSource, httpClient) // accNo required, no default (account/client.go:New)
bal, err := client.Withdrawable(ctx)   // scoped: balance_scope=BALANCE_SCOPE_SELECTED_ACCOUNT&account_number=008488L
```

Repeat with `accNo = "0001958"`. Record `Withdrawable`, `NetWithdrawable`, and
`SettlementSchedule` for both. `host` defaults to `account.DefaultHost` if
empty; `tokenSource` just needs to return `SB_TRADING_ACCESS_TOKEN`, the same
pattern `account-recon/main.go`'s `staticToken` uses.

Do **not** use `GET /balance/cash/info` (`account.Client.CashInfo`) for this —
ADR-0017 measured it as unscoped: it reports the core book's `trade_limit`
regardless of which `accNo` the client was constructed with, and using it here
would make the pre-snapshot wrong on the axis this whole runbook exists to
measure.

Timestamp both snapshots (WIB). Keep the raw JSON — the diff in §8/§9 is
exact-number comparison, not an eyeballed summary.

## 3a. Broker reconciliation and the pre-trade position gate

The broker (Stockbit/Carina) is the source of truth for what the sleeve holds.
The bot's stores are only its own record, and the owner can trade by hand in
the app (2026-10-14: the stores said 2 GOTO lots, the owner had sold 1 two
minutes after the bot bought it; the broker showed 1). A bot that loads a wrong
position places wrong orders, so two checks stand between the stores and an
order.

**1. Reconcile (read-only, run before every live session and after any app
trade).** From the repo root, with the trading token and the sleeve account in
the environment (`.env.local` defines `SB_TRADING_ACCESS_TOKEN` and
`SB_ACCOUNT_NO`):

```
set -a && source .env.local && set +a && make recon
```

It runs `strategy/cmd/account-recon --stores 'runs/*.db' --acc-no "$SB_ACCOUNT_NO" --sink-kind live` and:

- derives the expected position per symbol from **every** live store for the
  account (legacy stores with no recorded account are included and counted as
  `unattributed_fills`), plus `runs/manual_trades.json` (the dashboard's
  hand-entered trades), plus the broker-import ledger `runs/broker_trades.json`;
- compares it with the broker's holdings (`/portfolio/v2/list`, scoped to the
  account) and writes `runs/recon/<date>.json`: per symbol expected lots vs
  broker lots, `match`/`mismatch`, an explanation, and `as_of`. The file holds a
  short label (`--account-label`, default `sleeve`), never the account number
  or a token;
- fetches the broker's trade history (`GET /history`, paginated by
  `meta.max_page`, 3 months) and checks it trade by trade against the stores'
  fills, matching on symbol, side, Jakarta date, price and lots with the time
  within 5 minutes:
  - **matched**: price and lots agree; the broker's fee is truth, and a gap
    over Rp 0.50 is flagged (the first live fill's store fee 0 vs broker 4.05 is
    the known case; store 4 vs broker 4.2 is within tolerance and only reported);
  - **broker-only**: a trade placed by hand in the app. It is imported into
    `runs/broker_trades.json` (source `broker-import`, keyed by the broker's id,
    so re-running never duplicates it) and counts toward the expected position;
  - **store-only**: a fill the broker never executed — a bot bug, shown as an
    error (the exit code is 1).
- `/history` takes no account parameter, so it answers for the trading token's
  own account. It is used only when the token's account (the JWT `data.acn`
  claim) equals `--acc-no`; otherwise the check is skipped and the report says
  so. Cash moves are counted, not used; unknown commands are counted and logged,
  never dropped silently and never fatal. Account numbers and names in
  `move_stock_cash` are discarded at parse time.

Exit code 0 means every position matches and no phantom fill exists. Flags:
`--no-history` (holdings only), `--no-import` (report without writing the
ledger), `--exclude-stores a.db,b.db`, `--manual-trades`, `--broker-ledger`,
`--out-dir`. `make dashboard` shows the newest report on the Overview as the
"Positions match the broker" row (green, or red with each difference); with no
report it says "Not checked" and prints this command.

**2. The gate (automatic).** `strategy-run --sink live` runs the same
holdings check right after the token-account guard and before the store, run
row or any sink exists: broker holdings vs the position loaded from
`--prior-stores` (this account's fills only) plus manual and imported trades
(those dated before the earliest loaded fill are ignored: they belong to stores
that are not loaded). A mismatch refuses to start, naming each symbol and the
fix. On a pass, the rebalance strategy starts from that broker-consistent
position, not the raw stores. `--sink paper` (shadow runs) skips the gate.

- **`--prior-stores` must cover the whole life of every open position**, not
  just today: a position carried over from yesterday needs yesterday's store in
  the glob, or the gate (correctly) sees a broker holding the stores do not
  explain. Stores with unattributed (pre-`run.account`) fills cannot be loaded
  by `--prior-stores` (`loadPriorState` refuses them); reconcile with
  `account-recon`, which includes them.
- After an app trade: run the reconcile above (it imports the trade), then
  start `strategy-run` again.
- `--allow-position-mismatch` starts the run anyway. It is logged as a loud
  `WARNING`, and written to the run row (`run.position_override`). Use it only
  when you know why the records are wrong. An unreadable broker (401/403,
  network) is never overridable.
- `--manual-trades` / `--broker-ledger` override the default file locations
  (`runs/manual_trades.json`, `runs/broker_trades.json`).

The reconciliation code is structurally unable to place orders:
`strategy/recon/readonly_test.go` fails the build if the package or
`account-recon` imports `strategy/live` or gains an HTTP write path, and
`strategy/account/readonly_test.go` keeps the client GET-only.

## 4. Arm

1. `ALGO_LIVE=true` (parsed with `strconv.ParseBool` — `"true"`, `"1"`, `"TRUE"`
   all work; anything else, including unset, does not — `strategy/live/armed.go`).
2. `ALGO_CONFIRM=<the exact value of LiveConfirmPhrase>`, copied at the moment
   you set the variable **directly from `strategy/live/armed.go`**, not
   retyped from memory or from this document (which deliberately does not
   reproduce the literal string). It is compared byte-for-byte, no trimming,
   no case-folding, and its only purpose is to force a deliberate per-session
   opt-in — copy-pasting it into a log, ticket, or chat defeats that purpose
   even though it is not a credential in the sense of granting access by
   itself.
3. Confirm arming succeeded before relying on it: `live.ArmingFromEnv().Armed()`
   must report `true`. `strategy-run --sink live` will otherwise refuse to
   construct the live sink at all (`live.NewClient` returns `ErrNotArmed` —
   checked *first*, before `accNo` or tokens are even inspected), and exits
   with that error before touching the network.
4. From here on: no dry run. Every step below places or attempts to place a
   real order.

## 5. Smallest possible order — unfilled variant

Goal: prove acceptance/attribution with the smallest possible blast radius —
an order that cannot fill.

| Field | Value | Why |
|---|---|---|
| Symbol | a liquid name already gate-ALLOW (§2) | e.g. BBCA |
| Side | `buy` | |
| Lots | `1` | smallest a regular order can express — 100 shares, `sharesPerLot` in both `strategy/risk/local_checks.go` and `strategy/engine/sink.go` |
| Price | tick-valid, 10-15% below the current bid | far enough not to trade during the diagnostic; not so far it falls outside ARA/ARB — check with `market-probe` first (§2) |
| Board | `RG` | the only board this system ever routes to (`strategy/risk/local_checks.go`, `strategy/live/orders.go`) |
| Order type | `PLATFORM_ORDER_TYPE_LIMIT_DAY` | the only type `strategy/live` ever sends (`strategy/live/orders.go`) — never FAK/market |
| GTC | `false` | day order |

Request shape (`strategy/live.PlaceRequest`, `strategy/live/orders.go`) —
this is what `strategy-run --sink live` constructs internally
(`live.Sink.Submit`), shown here so the operator knows what to expect on the
wire, not something to build by hand:

```go
live.PlaceRequest{
    UIRef:     <minted per strategy/live/uiref.go:DeterministicUIRef>,
    Symbol:    "BBCA",
    Side:      "buy",
    PriceIDR:  <far-from-market, tick-valid>,
    Shares:    100, // 1 lot * 100 shares/lot — Place does not do this conversion itself
    BoardType: "RG",
}
```

**Getting `strategy-run` to submit exactly this one order.** The `rebalance`
strategy sizes an order from `target_notional / (price * 100 shares)`, not
from a literal lot count, and by default prices at the last observed price
snapped to tick — a marketable order, wrong for this variant. So: hand-write
a minimal one-symbol `target_portfolio.json` (plan 18 §3.1 shape — the
schema is unchanged) whose `base_notional_idr` is approximately one lot's
notional at the **limit** price (`limit * 100 * 1.0` for `weight: 1.0`; it
floors to exactly 1 lot for anything in `[limit*100, limit*200)`), and pass
`--limit-price-idr <limit>` with a tick-valid limit ~12% below the current
bid (e.g. BBCA 5250 when last is ~5950). The strategy then submits at
exactly that price and sizes lots against it (`floor(notional / (limit *
100))`), never against the market price. It still waits for the first
liveprice observation for the symbol before acting — the order needs the
market to be live, and the gate compares against it. Point `--target-portfolio`
at the file and run the command line in §1, with `--symbols <SYMBOL>`
matching the portfolio's one target and a `--store` path fresh for this
attempt. Check the startup log for the `--limit-price-idr ... is in force`
warning before arming.

Record, in WIB: submission time, and the broker order id `Place` returns.
That id is Carina's own numeric id, **not** the client id (`UIRef`/
`OrderID`) the rest of this codebase keys on — see `strategy/live/doc.go`'s
section on why the two must never be swapped.

## 6. Confirm resting, then cancel

- Poll `live.Client.Detail(ctx, brokerOrderID)` until the state is OPEN, not
  still SENDING (`strategy/live/state.go`'s `State`/`Transition`;
  `strategy/live/poller.go` is the reference implementation of exactly this
  poll, on a 3s cadence in the real `Sink`).
- **Cancel it**: `live.Client.Cancel(ctx, brokerOrderID, uiRef)`. Do not leave
  it resting past this diagnostic step.
- Confirm via another `Detail` call: state should read CANCELLED (or
  REJECTED, if the exchange itself rejected it outright — also useful
  evidence; record why).

## 7. Post-snapshot and diff — unfilled order

Re-run §3's exact procedure for **both** accounts.

- **Positions must be unchanged in both.** An unfilled, then-cancelled order
  moves nothing. If either account's position changed, **STOP** (§11) — that
  means the order filled when it should not have, or something is
  fundamentally confused about attribution.
- **Attribution from the order list itself is likely inconclusive, and that is
  worth recording as a finding, not a failure.** `strategy/live/doc.go`
  already flags that `GET /order/v2/list` returns every sub-account's orders
  with no per-row account field. If the resting order appears in a `List`/
  `account-recon` call regardless of which `acc_no` is queried, that null
  result is itself the datum — it means order-list attribution genuinely
  cannot answer this question, matching the doc comment's warning, and
  §8/§9's balance-movement diff is the only channel left.
- **Withdrawable balance may show a transient reservation** while the order
  rests, even though nothing fills. Whichever account's `Withdrawable` dipped
  by the reserved amount while the order was open — even briefly — is
  suggestive evidence toward attribution. Record it even if it does not fully
  settle the question; §8 is the decisive step.

## 8. The fill variant — minimal notional, and the decisive diff

Only proceed once §5–§7 completed cleanly: no unexpected position change, no
open questions about the cancel.

1. Same board/lots/order-type as §5, but price **at or inside the
   current best offer** for a buy, so it fills promptly. On the same day as
   §5, use a **different symbol**: the order id is
   `rebalance-<SYMBOL>-<date>`, so each symbol gets one rebalance order per
   trading day (see §1's flag notes). Keep notional small —
   1 lot of a low-priced, already gate-ALLOW, liquid name.
2. Submit, then poll `Detail` until FILLED. (A 1-lot order cannot partially
   fill below the lot boundary — it should go straight to FILLED, or stay
   OPEN if genuinely unmatched. If still OPEN after a reasonable wait, cancel
   it per §6 and treat the fill variant as inconclusive this run, not as a
   failed diagnostic — retry another day/name rather than forcing a fill.)
3. Re-run §3's procedure for **both** accounts.
4. **The decisive check.** Whichever account shows, between pre- and
   post-snapshot:
   - the traded symbol's position increased by exactly 1 lot, **and**
   - `Withdrawable` decreased by approximately
     `fill_price × 100 shares × (1 + 0.0015)` (buy-side all-in fee rate,
     `account.DefaultFeeSchedule` / [reference 07](../reference/07-fees-and-costs.md) §2)

   is the account the order actually landed in. **Record both accounts' exact
   before/after numbers in plan 18 §3.4 — not just the "winning" one.** A
   confirmed *non-movement* in the other account is exactly as load-bearing
   as the movement in the one that did.
5. Reconcile the fill through `account-recon` again, now against a store that
   has a real fill row in it — this is also the first of the three
   consecutive real-fill sessions plan 18 §10's B1 box requires (see §11
   below; one session is necessary, not sufficient, for that box).

## 9. Cleanup

- Sell the 1 lot back out (same process, side=`sell`), unless there is a
  deliberate reason to keep it — record the decision either way.
- Confirm no resting orders remain in either account (`Detail`/`List`).
- Unset `ALGO_LIVE`/`ALGO_CONFIRM` at the end of the session — do not leave
  them set in a persistent shell profile.

## 10. The separate, more cautious step — does the sleeve have its own trading limit? (§3.4)

Run this only **after** §5–§9 have settled attribution cleanly, in a separate
sitting — mixing this with the attribution steps would confuse "the sleeve
rejected an over-limit order" with "the order landed somewhere unexpected."

- The question: ADR-0017 measured that `cash/info`'s `trade_limit`
  (377,013,889.11 IDR at last measurement) reports the **core book**
  regardless of `acc_no`, and is therefore silent about whether `008488L` has
  any limit of its own beyond its own cash.
- The only way to settle it: attempt an order whose notional exceeds
  15,000,000 IDR in `008488L`, and observe whether Carina's order-entry
  validation accepts or rejects it.
- **Design for safety**, same shape as §5: price far enough from market that
  it cannot fill (settlement risk is irrelevant to what this measures — only
  acceptance/rejection at submission matters), notional modestly above the
  boundary (e.g. 16,000,000–18,000,000 IDR — enough to unambiguously exceed
  15M, not an order of magnitude over it), symbol already gate-ALLOW.
- **Two possible outcomes, both are the answer, not a failure either way:**
  - **Rejected at submission** (`live.ErrInvalidParameter` or similar, citing
    insufficient balance/buying power): the sleeve is capped at its own cash.
    This confirms ADR-0017's conservative reading by evidence — update plan
    18 §3.4's open checkbox from "the conservative reading stands" to
    "confirmed: no independent limit," with the exact rejection recorded.
  - **Accepted and rests**: the sleeve inherited the core book's limit after
    all. This **reverses** ADR-0017's reading and needs its own follow-up —
    B2's caps (denominated in `withdrawable`, per ADR-0017 decision 2) would
    then be a deliberate, conservative choice rather than the only option,
    which is a decision to make explicitly, not to silently carry forward.
    ADR-0017 would need amending, not just plan 18 §3.4.
- **Cancel it immediately either way** — if accepted, do not let a >15M order
  rest through a session in an account whose true limit is now uncertain by
  construction; cancel within the same Detail-then-Cancel loop as §6.

## 11. Abort criteria (apply throughout §5–§10)

- Any position change in either account/symbol not expected by the step just
  taken.
- Any order state `strategy/live/state.go`'s `Transition` cannot explain —
  treat an unrecognized transition as "stop and inspect by hand," never
  "retry."
- `live.ErrUserSuspended`, `live.ErrUnauthorized`, or a repeated
  `live.ErrDuplicateOrder` on what should be a fresh `UIRef` — each indicates
  either a credentials problem or a ui_ref-reuse bug; stop, do not push
  through. The exception is the expected duplicate after a same-day retry
  without `--retry N` (see the note below).
- **Same-day retry after a cancelled order.** Rebalance order ids are
  `rebalance-<SYMBOL>-<day>` and the ui_ref is derived from them
  (`live.DeterministicUIRef`), so a second attempt the same day is rejected by
  Carina as `duplicate order (ui_ref already used)`, even after the first order
  was cancelled unfilled. A new `--store` does not help. To resubmit on
  purpose, pass `strategy-run --retry N` (N >= 2, a fresh N and a fresh
  `--store` per attempt): ids get the suffix `-rN`, and only an earlier `-rN`
  attempt blocks the run. Never reuse a `--store` file across runs (`UNIQUE
  constraint failed: decision.seq`).
- `market-probe` reports DENY on the symbol in use, at any point during the
  session.
- Genuine doubt about which account was just charged, at any step — do not
  proceed to §8 (the fill variant) or §10 (the limit probe) until the
  preceding step's result is fully understood.

## 12. What to record, and which plan 18 §10 boxes this does and does not tick

**Record in [plan 18 §3.4](../plans/18-alpha-and-infrastructure-split.md):**

- The account-attribution result from §8 (both accounts' before/after
  numbers), replacing the "assumed, not measured" framing with a confirmed
  answer.
- The trading-limit result from §10, with the exact rejection message (if
  rejected) or the accepted order's detail (if accepted).
- If §10 came back "accepted," flag [ADR-0017](../adr/0017-sleeve-is-a-funded-sub-account.md)
  for amendment — its "Open questions" section already anticipates this.

**Also update, separately** (not this task, and not this file):
`strategy/live/doc.go`'s "An open question this package does not resolve"
section should be updated by whoever owns that package once §8 produces a
result — it currently reads "whoever wires this package for real trading must
independently confirm," which this runbook's execution is that confirmation.

**Plan 18 §10 boxes this does and does not move:**

- Contributes the **first** of the three consecutive real-fill sessions
  `[ ] B1 reconciliation matches the live sleeve account (008488L, §3.4) by
  hand for 3 consecutive sessions carrying real fills` requires — necessary,
  not sufficient, for that box.
- Does **not** by itself tick `[ ] B3/B4 replay-vs-live parity` in plan 18
  §5 — that is a parity claim about behaviour across many runs, not
  something one diagnostic order settles. `strategy-run --sink live` wiring
  itself (Carina order routing behind `engine.OrderSink`, always wrapped in
  `RiskSink`, restart-durable client ids/ui_refs) is done, as is
  `Baseline`/`ReconcileTerminal` being called; B5 (execution algo — slicing,
  participation caps) is separate and still unbuilt.
- Directly resolves plan §3.4's two open items and `strategy/live/doc.go`'s
  open question. Neither is its own checkbox in §10 today, but both are named
  preconditions before B4 can be trusted with anything beyond the smallest
  possible size, per that file's own doc comment.

## 13. Execution log — 2026-10-13 (Tue, Session 2)

The first execution of §2–§9. Run artifacts (stores, status files, snapshots)
are in `runs/2026-10-13-*`, which is untracked. All times are WIB.

| Step | Result |
|---|---|
| §2 `market-probe` | BBCA band ARB 5,075 – ARA 7,125, last ~5,925, tick 25. ALLOW at 5,950 and 5,250; DENY at 8,950 (out of band), as expected. |
| Paper shadow run (`--sink paper --source live`) | `shadow-3`: one intent, buy 1 lot of BBCA at 5,925 at 13:41:39; paper fill at 5,925 with fee 889; no rejects. Two earlier attempts failed on bugs 1 and 2 below. |
| §3 pre-snapshot | `008488L`: MATCH, 0 positions. `0001958`: 9 holdings; DIVERGENT, as expected, because no store tracks the core book. |
| §5–§7 unfilled order | First attempt 13:52: baseline `/order/v2/list` 401, an expired trading token, so no order was sent (bug 4). Retry: buy 1 lot of BBCA at a **fixed 5,250** (`--limit-price-idr`), submitted 13:56:32. OPEN, 0 lots done; the app showed 525,787 reserved (525,000 + 0.15% fee) against the sleeve's 15,000,000 equity. **Cancelled automatically** by the live sink's 5-minute `OpenTimeout` (about 14:01:30), with no manual action, which is the first live proof of the auto-cancel path. Run ended `complete` on Ctrl-C at 14:03:23. Pre/post snapshots of **both** accounts are byte-identical. |
| §8 fill | GOTO, 1 lot at 27 (ask), `--rebalance-at 14:12`. Submitted 14:12:00.08, **fill observed 2.75 s later** (1 lot at 27). `ReconcileTerminal clean`. The WS feed dropped once (EOF at 14:13:30) and reconnected after 750 ms. Post-fill: `008488L` **MATCH** (broker and store both hold GOTO). `0001958`'s broker holdings are identical to the pre-snapshot. |
| §9 cleanup | GOTO sold in the Stockbit app at 26. The post-sell `008488L` snapshot shows 0 broker symbols; the store still holds 100 GOTO shares (`held_in_store_only`), which is expected because the sale was made outside the store. The `ALGO_*` variables were set per command, never exported. |
| §10 trading limit | **Not run.** Needs a separate sitting. |

**§3.4 attribution answer: both orders landed in the sleeve, but we do not
know why.** `--account` is **not sent to Carina**. `live.Client` stores it, but
the order body (`placeBody`) has no account field, and no order endpoint takes
one. Carina routed both orders to `008488L` on its own. Plan 18 §3.4 recorded
on 2026-09-14 that the trading JWT is stamped `acn=0001958`, the core book, so
"the token's own account" does not explain the result either. Until the
mechanism is known, every live session must re-confirm attribution with the
§3/§7 snapshot diff of both accounts, and `--account` should be read as "the
account we *expect* and reconcile against", not as routing. The routing
evidence:
- The sleeve gained exactly the 1 GOTO lot.
- The app's sleeve view showed the §5 reservation against the sleeve's own
  15,000,000.
- The core book's holdings did not move across either order.

Withdrawable was read from the app, not from a CLI (§1: not wired), so the
exact before/after cash figures were not captured programmatically. We did not
capture whether the resting §5 order appeared in `/order/v2/list` for both
accounts while it rested: the snapshots were taken before and after the
order, not during it.

**Cost.** About 111 IDR: the GOTO round trip lost 100 IDR (27 → 26 × 100
shares) and paid about 11 IDR in fees. §5 cost nothing.

**Bugs this session surfaced, all fixed 2026-10-13:**

1. Carina/Exodus REST returns HTTP 403 to Go's default User-Agent before
   auth. Every client now sends `feed.DefaultUserAgent`, and `account`
   returns a separate `ErrForbidden` (`e6077c4`).
2. The rebalance window was hardcoded to 15:00, after continuous trading
   ends, so a live run could never trade. Fixed with `--rebalance-at HH:MM`.
   The same commit makes Ctrl-C/SIGTERM shut down cleanly: reconcile and
   close run on a fresh 30 s context and the run is recorded as `complete`
   (`2f5a4e0`). `--limit-price-idr` was added for §5 (`f73f193`).
3. Live fills recorded `fee_idr = 0`. They now use the paper sink's fee
   schedule, `engine.ScheduleFeeIDR`; this is an estimate, because the
   meaning of Carina's fee field is unconfirmed (`42411a0`).
4. A failed pre-trade baseline left the run row at `running`. It now
   records `error` and closes the sink (`94120fd`).
5. `account-recon` for one account counted another account's fills from
   the same store, so the sleeve's GOTO showed up in the core book. Each run
   now records `run.account`, and positions and order ids are filtered by it
   (`8fce31f`). Stores written before this fix, including all of today's,
   read as account `""`. A sixth, smaller bug: one-sided divergences showed
   `delta_shares: 0` (`12669dc`).

**Still open.**
- §10, the sleeve trading-limit probe.
- Recording the attribution answer in plan 18 §3.4 and
  `strategy/live/doc.go`'s open question, per §12.
- B1's three consecutive real-fill sessions: this was session 1 of 3.
- **Find out what routes an order to `008488L`**, since `--account` does not.
  Candidate explanations: the account selected in the app when the trading
  token was minted, a server-side default for the `Trading` sub-account, or a
  claim in today's token that differs from the 2026-09-14 `acn=0001958`. The
  operator can check the token's claims in their own terminal; the token must
  never be pasted into chat.
- ~~Ctrl-C leaves a resting order resting~~. Fixed the same day: Ctrl-C now
  cancels open orders and waits for them to settle (see §1's flag notes).


## 14. Execution log — 2026-10-14 (Wed): §10 attempted, VOID, order misrouted to the core book

**Result: §10 did not measure the sleeve.** The probe order was placed in the
core book `0001958` (app account "LEWIS"), not `008488L`. Nothing was learned
about the sleeve's trading limit, and ADR-0017's conservative reading stands.

- 11:53 WIB: `market-probe --symbols BBCA --price 5350` returned ALLOW (ARB 5,075,
  ARA 7,125). Pre-snapshots of both accounts were taken
  (`runs/2026-10-14-pre-*.json`).
- `strategy-run` was started at 11:59 with `--account 008488L --equity-idr
  14900000`, the 1.25 risk overrides and `runs/target_limitprobe_bbca.json`
  (base notional 17M). Run id `20261014T045923Z-1e2c7931`.
- 13:32:03 WIB: intent `rebalance-BBCA-2026-10-14`, buy 31 lots @ 5,350 =
  16,585,000 IDR. Carina accepted it: `order_reject` stayed empty. It was
  resting at about 14:09.
- The operator found the order under the main account "LEWIS" and cancelled
  it in the app. There was no fill.

**Cause (operator's account, not yet verified by claim decode).** The trading
token was copied from a web session where the main account was selected.
Yesterday's token presumably came from a session with `008488L` selected. So
routing appears to follow the account the token was minted for. `--account` has
no effect on routing, as `strategy/live/doc.go` already warned.

**Consequences.**
- The run's store labels this order `008488L`, which is wrong. Exclude
  `runs/2026-10-14-limitprobe.db` from any sleeve reconciliation or P&L.
- The risk layer sized its caps on the sleeve's 14.9M, while the order went into
  the 1.1B book. Nothing in `strategy-run` can detect this today.

**Before any further live run.**
- [x] Find the routing claim. The trading JWT's top-level claims are
      `aud, data, exp, iss, jti, ver`; the account is the nested **`data.acn`**,
      not a top-level `acn`. The operator decoded it in their own terminal. A
      fresh token copied under `008488L` reads `data.acn = 008488L`. The token
      that misrouted was copied under the main account, and the 2026-09-14
      token read `0001958`.
- Later runs that afternoon placed no orders. The 14:26 and 14:54 runs were
  refused at baseline because the token was rejected (401/403). The 15:00 run
  reused the same store and died on `UNIQUE constraint failed: decision.seq`:
  every run needs its own `--store` file.
- [x] Add a pre-flight guard to `strategy-run`: refuse to arm unless the
      token's `acn` equals `--account`. Done in `2f607e1`.
- [x] Re-run §10 under that guard (below).

**§10 re-run, 15:13 WIB: ANSWERED, the sleeve has no limit of its own.**
- Fresh token copied under `008488L` (`data.acn = 008488L`), with the
  refuse-to-arm guard in place (`strategy-run` refuses to arm when `data.acn` ≠
  `--account`).
- BBRI was used instead of BBCA. A same-day BBCA retry would reuse the cancelled
  order's id `rebalance-BBCA-2026-10-14`, and so its deterministic ui_ref. A
  rejection would then be ambiguous between a duplicate and the limit.
- Target `runs/target_limitprobe_bbri.json` (17M). Store
  `runs/2026-10-14-bbri-limitprobe.db`, run `20261014T081304Z-8575169b`.
- 15:13:07: intent `rebalance-BBRI-2026-10-14`, buy 64 lots @ 2,630 =
  **16,832,000 IDR**, against 14.9M withdrawable. Submission returned OK, so
  `order_reject` is empty.
- The app then showed **`Order Rejected — [C-006] insufficient trading
  balance`**. There was no fill. The run closed `complete` at 15:14:07 after
  Ctrl-C.
- That a sleeve rejected for insufficient balance is itself attribution
  evidence: the core book has ~377M of limit and would have accepted.

**Answer.** The sleeve's buying power is its own cash. ADR-0017's conservative
reading is confirmed, not reversed, and B2's caps (in `withdrawable`) are the
only correct choice.

**New open item.** A broker rejection that arrives *after* a successful
submission is not written to `order_reject`, which today only captures
`Submit` errors. The store shows this order as attempted with 0 fills and
nothing else. The live poller should record a post-submission REJECTED status,
with its reason, as a reject row.
Fixed in `a641a2a`; this has not yet been observed against a real rejection.

## 15. Execution log — 2026-10-14 (Wed): GOTO round trip, leg 1 (buy) — real-fill session 2

**Result: CLEAN FILL in `008488L`.** This is B1 real-fill session 2 of 3, and
the first fill made with the `data.acn` guard in place.

- **Gate.** `market-probe --symbols GOTO --price 28` returned ALLOW (ARB 23,
  ARA 36). The first attempt was DENY because `market_open` got 401/403:
  `SB_ACCESS_TOKEN` (the main/Exodus session, used for `market-time`) had
  expired, while the trading token was fine. Refreshing it fixed the gate.
- **Pre-snapshot.** Reused `runs/2026-10-14-bbri-pre-008488L.json` (MATCH, 0
  symbols). Nothing in the sleeve had changed since the BBRI probe, which did
  not fill.
- **Run.** `strategy-run --strategy rebalance --target-portfolio
  runs/target_roundtrip_buy_2026-10-14.json` (strategy_id `roundtrip-goto`,
  GOTO weight 1, base notional 4,000), `--limit-price-idr 28 --rebalance-at
  15:38`, store `runs/2026-10-14-roundtrip-buy.db`, run
  `20261014T083758Z-0fce0610`. The guard logged that the token account matches
  `--account`.
  - 15:38:00 WIB: intent `rebalance-GOTO-2026-10-14`, buy 1 lot @ 28.
  - 15:38:01: **fill**, 1 lot @ 28, fee 4 IDR, 0.0 bps slippage. `order_reject`
    is empty.
  - Ctrl-C at 15:38:37, outcome `complete`.
- **Post-reconcile.** `runs/2026-10-14-post-008488L.json` is a **MATCH**: 1
  broker symbol, 1 store symbol, no divergences. The operator confirmed in the
  app that the main account "LEWIS" (`0001958`) did not change.

**Leg 2 is the sell to zero on Thu 2026-10-15, Session 1** (commands in
`runs/2026-10-15-roundtrip-sell-commands.md`).
- Target GOTO weight 0, with `--prior-stores 'runs/2026-10-1[45]-roundtrip-*.db'`
  so the bot starts out holding 1 lot.
- It cannot happen today. Rebalance makes one order per (day, symbol), and
  `rebalance-GOTO-2026-10-14` with its deterministic ui_ref is already used.
- It is real-fill session 3 and the first live sell-to-zero by the bot, the
  last two B4 gates.

## 16. Execution log — 2026-10-15 (Thu, Session 1): GOTO round trip, leg 2 (sell to zero) — real-fill session 3

- **Pre-snapshot.** `runs/2026-10-15-pre-008488L.json`: broker holds GOTO 100
  shares, verdict DIVERGENT (`held_at_broker_only`) against the fresh, empty
  recon store, so account-recon exited 1. Expected: the snapshot store holds no
  positions; the bot's own position comes from `--prior-stores`.
- **Attempt 1.** Store `runs/2026-10-15-roundtrip-sell.db`, run
  `20261015T015929Z-c2ea33a9`. 09:00:00 WIB: intent `rebalance-GOTO-2026-10-15`,
  sell 1 lot @ 27. It rested unfilled and was cancelled on Ctrl-C (outcome
  `complete`, 0 fills).
- **Attempt 2a.** The same `--store` reused at @ 26: the run died at once with
  `UNIQUE constraint failed: decision.seq`. Nothing was sent.
- **Attempt 2b.** A fresh store `runs/2026-10-15-roundtrip-sell-2.db`,
  `--prior-stores` pointing at the buy store only. 09:06:00: intent sell 1 lot
  @ 26 under the same id, rejected by Carina: `duplicate order (ui_ref already
  used)`. This is the first live evidence that the deterministic ui_ref blocks
  a same-day resubmission even after a cancel.
- **Fix.** `strategy-run --retry N` added the same morning (see §11).
- **Attempt 3.** `--retry 2`, store `runs/2026-10-15-roundtrip-sell-r2.db`, run
  `20261015T021713Z-d3c58e19`.
  - 09:17:15: intent `rebalance-GOTO-2026-10-15-r2`, sell 1 lot @ 26.
  - 09:17:16: **fill**, 1 lot @ 26, fee 6.5 IDR, 0.0 bps slippage.
    `order_reject` is empty.
  - Ctrl-C at 09:17:50, outcome `complete`.
- **Post-reconcile.** `runs/2026-10-15-post-008488L.json` is a **MATCH**: 0
  broker symbols, 0 store symbols. The sleeve is flat.
- **Round trip.** Buy @ 28 (fee 4), sell @ 26 (fee 6.5): about -210.5 IDR.
  Real-fill session 3 and the bot's first live sell-to-zero are done, which
  closes B4 step 1.
