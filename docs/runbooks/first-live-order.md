# Runbook: First Live Order — Account Attribution and the Sleeve's Trading Limit

> **Status: cannot be fully executed yet.** Order placement itself is
> **NOT YET WIRED** into any `strategy/cmd` binary as of 2026-09-24 — see §1.
> This runbook documents everything that CAN be run today (preconditions, gate
> probe, pre/post position snapshots, the arm sequence) in full, and documents
> the order-placement steps in exact detail so they are ready to execute the
> moment a Layer-2 mechanism (§1) exists. Do not improvise flags or commands
> beyond what is listed here; where nothing exists yet, this file says so.

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
| Double-arm gate (`ALGO_LIVE` / `ALGO_CONFIRM`) | **Implemented**, in `strategy/live/armed.go` (`ArmingFromEnv`), but nothing in `strategy/cmd` reads it yet — see next row. |
| Placing a real order | **NOT YET WIRED.** `strategy/live` (`Client`, `Sink`, the poller, `Baseline`/`ReconcileTerminal`) is code-complete but is not imported by any file under `strategy/cmd/` (`grep -rl "strategy/live" strategy/cmd` returns nothing). `strategy/cmd/strategy-run`'s `--sink` flag accepts only `"paper"` (`strategy-run/main.go:newSink` errors on anything else) — `--sink live` does not exist. This is plan 18 §5 B4, and every box under B4 is unchecked. |

Because order placement is not wired, this runbook is written in two layers:

- **Layer 1 — executable today.** §2 (preconditions), §3 (pre-snapshot), §4
  (arm). All read-only or local configuration; zero order risk.
- **Layer 2 — blocked on wiring.** §5–§8 (place, confirm, cancel, diff) and §9
  (the trading-limit probe). Documented in full so whoever builds the missing
  piece can execute immediately, without re-deriving the request shape.
  Two ways Layer 2 could be unblocked — **neither is built by this task**:
  1. `strategy-run --sink live`, wired to `strategy/live.NewSink` behind
     `engine.Run` — the real B4 deliverable, which also gets `strategy/risk`'s
     gates (B2) in front of every order for free.
  2. A small standalone diagnostic command that constructs `strategy/live.Client`
     directly and calls `Place` / `Detail` / `Cancel` once, with no
     `engine.Run` or `RiskSink` in the loop. This is smaller and faster to
     build, but **bypasses every B2 gate** — appropriate only as a one-shot,
     hand-supervised diagnostic, never left running, and never a substitute
     for actually building B4.

  Whichever is built, re-read this runbook against it before using it — do not
  assume flag names not written here.

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
      manual here: whichever Layer-2 mechanism is used (§1), know in advance
      how to cancel an order by hand (`live.Client.Cancel`) within seconds of
      submission. If the diagnostic runs through the real `engine.Run` +
      `RiskSink` path (Layer 2, option 1), the external kill-switch file
      (`strategy/risk/killswitch.go`) is also available, but it stops *new*
      submissions, not an order already resting — cancellation is still manual.

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
   must report `true`. Whatever Layer-2 mechanism you are using will otherwise
   refuse to construct (`live.NewClient` returns `ErrNotArmed` — checked
   *first*, before `accNo` or tokens are even inspected).
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

Request shape (`strategy/live.PlaceRequest`, `strategy/live/orders.go`):

```go
live.PlaceRequest{
    UIRef:     <minted per strategy/live/uiref.go:NewUIRef>,
    Symbol:    "BBCA",
    Side:      "buy",
    PriceIDR:  <far-from-market, tick-valid>,
    Shares:    100, // 1 lot * 100 shares/lot — Place does not do this conversion itself
    BoardType: "RG",
}
```

Submit through whichever Layer-2 mechanism exists (§1). Record, in WIB:
submission time, and the broker order id `Place` returns. That id is
Carina's own numeric id, **not** the client id (`UIRef`/`OrderID`) the rest of
this codebase keys on — see `strategy/live/doc.go`'s section on why the two
must never be swapped.

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
- Does **not** by itself tick any B4 box in §5, and does not tick
  `[ ] B3/B4 replay-vs-live parity` — B4 (idempotency keys, order state
  machine, retained double-arm inside the real pipeline) remains unbuilt as a
  wired sink regardless of this runbook's outcome; a Layer-2-option-2
  standalone diagnostic (§1) explicitly does not constitute B4 being built,
  since it bypasses `strategy/risk`'s gates entirely.
- Directly resolves plan §3.4's two open items and `strategy/live/doc.go`'s
  open question. Neither is its own checkbox in §10 today, but both are named
  preconditions before B4 can be trusted with anything beyond the smallest
  possible size, per that file's own doc comment.
