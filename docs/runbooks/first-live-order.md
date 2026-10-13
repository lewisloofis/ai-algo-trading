# Runbook: First Live Order — Account Attribution and the Sleeve's Trading Limit

> **Status, 2026-09-24: WIRED.** `strategy-run --sink live` now exists
> (`strategy/cmd/strategy-run/main.go`) — see §1 for the exact command line.
> Order placement can be executed following this runbook end to end. The
> "Layer 1 / Layer 2" split below is kept as written because it still
> describes what is read-only vs. what places a real order; only the
> "NOT YET WIRED" framing is stale and is corrected in §1.

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
    --store runs/<YYYY-MM-DD>-<n>.db \
    --prior-stores 'runs/<YYYY-MM-DD>-*.db' \
    --kill-switch-file <path to a file you can create by hand to trip the kill switch mid-run>
  ```

  Notes on flags that are easy to get wrong:

  - `--prior-stores` is **required** with `--sink live`. It must glob every
    earlier store file from the same trading day; on restart after a crash,
    the intents recorded there are not resubmitted. Matching nothing is fine
    on the day's first run. Use a fresh `--store` file per run (one store
    file per run is the store's own convention).

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

1. Same symbol/board/lots/order-type as §5, but price **at or inside the
   current best offer** for a buy, so it fills promptly. Keep notional small —
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
  through.
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
