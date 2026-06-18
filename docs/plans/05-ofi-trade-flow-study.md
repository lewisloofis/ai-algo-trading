# OFI & Trade Flow Study — Order Flow Imbalance, Signed Flow, Kyle's Lambda

> **Status:** ✔️ Done · **Owner:** @lewis · **Updated:** 2026-06-18
> **Related:** [Thesis: order flow & price impact](../thesis/02-order-flow-and-price-impact.md) · [Thesis: microstructure signals](../thesis/01-microstructure-signals.md) · [04-microstructure-study.md](./04-microstructure-study.md) · [Plans index](./README.md)

Plan 04 passed the OBI/micro-price gate on the June 17 BUMI segments (0 trades,
book-only data). This plan runs the **next layer of the thesis** now that a full
trading day with a live trade tape is available: does **OFI add over OBI**, does
**signed flow predict direction**, what is **Kyle's lambda on BUMI**, and does
the OBI finding **generalise across symbols**?

> ⚠️ **Disclaimer.** See [01-market-data-capture.md §1](./01-market-data-capture.md)
> for the disclaimer on undocumented Stockbit endpoints and ToS.

---

## 1. Context — what changed

The segment `segments/seg-013634.binpb.zst` (downloaded from S3 after the June
18 trading session) provides:

| Property | June 17 (plan 04) | June 18 (this plan) |
|----------|------------------|---------------------|
| Span | Two partial sessions, BUMI only | Full day 08:36–16:25 WIB, 6 symbols |
| Trade tape | **0 trades** | 138,000+ trades with `side=B/S` |
| Symbols | BUMI | ANTM, BUMI, DEWA, INDY, ITMG, MDKA |
| OFI testable? | No | **Yes** |
| Signed flow | No | **Yes (continuous session only)** |

Trade counts by symbol (all with aggressor-side tags during continuous trading):

| Symbol | Trades |
|--------|--------|
| BUMI   | 49,277 |
| DEWA   | 41,874 |
| ANTM   | 23,955 |
| MDKA   | 17,588 |
| INDY   |  2,679 |
| ITMG   |  2,567 |

---

## 2. Locked decisions

1. **Same hybrid Go + Python stack as plan 04.** Extend `features.go` for OFI
   columns; add a new `sbcap trades` subcommand for the trade CSV; Python does
   all statistics and plots.
2. **Four independent studies, one segment.** OFI/OBI comparison (§3), signed
   flow + Kyle's lambda (§4), multi-symbol OBI (§5), pre-open IEP (§6). They
   share the same data source and replay infrastructure; their Python scripts are
   separate.
3. **Continuous trading only for signed-flow analysis.** The pre-opening batch
   (printed 08:58 WIB) has `side=` empty — no aggressor tags. Filter to
   09:00–12:00 and 13:30–16:15 WIB for any test involving `side`.
4. **Same significance standard as plan 04.** HAC (Newey-West) standard errors,
   block-bootstrap null for directional tests, chronological OOS split (no random
   splits). Multiple-testing (signals × horizons) noted with a Bonferroni caveat.
5. **Forward target is mid, not last-price.** Consistent with plan 04's reasoning
   (§2 locked decision 3).

---

## 3. Part A — OFI vs. OBI comparison (BUMI)

### 3.1 Go: extend `features.go`

The features CSV currently emits static book snapshots. To compute OFI we need
the *change* in best-bid/ask volume between consecutive rows. The cleanest
approach is to emit the raw delta columns from Go and compute OFI in Python
(avoids stateful logic in the emitter and keeps the CSV self-contained).

- **Edit `collector/cmd/sbcap/features.go`** — add two new columns immediately
  after `iep`:
  - `delta_bid_vol` — `bid_vol_0(t) − bid_vol_0(t−1)`, empty for the first row
    and after any reconnect/reauth control event (stale state).
  - `delta_ask_vol` — `ask_vol_0(t) − ask_vol_0(t−1)`, same nullability.
  - Track `prevBidVol`, `prevAskVol`, and a `dirty` bool that is set to true on
    `EventControl` (RECONNECT/REAUTH) and clears after the first orderbook
    update; when dirty, emit empty strings for the delta columns.
- **Edit `buildFeaturesHeader`** — append `"delta_bid_vol"`, `"delta_ask_vol"`
  after `"iep"`.
- **Edit `assembleRow`** (or add a new variant) — accept `deltaBidVol`,
  `deltaAskVol` as optional `*float64`; format as empty string when nil.
- **Edit `features_test.go`** — add table cases for: delta columns are empty on
  first row; delta columns are empty after a RECONNECT control event; delta
  columns are correct on third and subsequent rows.

Python derives OFI from these columns:

```python
df["ofi"] = (
    df["delta_bid_vol"].clip(lower=0)       # ΔBid⁺
  - df["delta_bid_vol"].clip(upper=0).abs() # ΔBid⁻
  - df["delta_ask_vol"].clip(lower=0)       # ΔAsk⁺
  + df["delta_ask_vol"].clip(upper=0).abs() # ΔAsk⁻
)
```

### 3.2 Python: `study/ofi_obi_compare.py`

Reads the features CSV for BUMI (continuous session only), runs:

1. **OFI in a rolling window** — aggregate OFI over 1s/5s/10s windows (sum over
   all book updates in the window) to compare with the forward-return horizons
   from plan 04.
2. **OLS: `fwd_mid_ret ~ OFI`** (HAC SE) vs. `fwd_mid_ret ~ OBI1` — is OFI's R²
   higher?
3. **OLS: `fwd_mid_ret ~ OFI + OBI1`** — does OFI add incremental R² once OBI1
   is controlled for, and vice versa?
4. **Directional hit-rate of OFI** — same conditional test as plan 04
   (`P(sign(fwd_mid_ret) == sign(OFI))` conditional on `|fwd_mid_ret| > 0`).
5. **Block-bootstrap null and chronological OOS** — same methodology as plan 04.
6. **Outputs:** `out/ofi_obi_report.md` and comparison scatter/decile PNGs.

**Verdict:** OFI PASS if OFI's incremental R² over OBI1 is ≥5pp on held-out
data, beating the bootstrap null. If OFI adds nothing — interpret as "static
snapshot is the sufficient statistic on a 1-tick book" — a legitimate and
interesting finding.

### 3.3 Tasks

- [x] `features.go`: add `prevBidVol`, `prevAskVol`, `dirty` state; emit
  `delta_bid_vol`, `delta_ask_vol`
- [x] `buildFeaturesHeader`, `assembleRow`: update header and row assembly
- [x] `features_test.go`: delta-column cases (first row, post-reconnect, steady-state)
- [x] `go test -race ./cmd/sbcap/...` green
- [x] `study/ofi_obi_compare.py`: OFI aggregation, OLS/HAC, hit-rate, bootstrap,
  OOS, report + PNGs

---

## 4. Part B — Signed trade flow + Kyle's lambda (BUMI)

### 4.1 Go: `sbcap trades` subcommand

A new subcommand `sbcap trades` emits a per-trade CSV from `EventRunningTrade`
events. Mirrors the structure of `sbcap features`:

- **New `collector/cmd/sbcap/trades.go`** — `runTrades(args)`:
  - Flags: `--symbol S` (required), `--from/--to HH:MM` (optional, same WIB
    parsing as other subcommands).
  - Replay loop: on `EventRunningTrade`, if `evt.Symbol == sym` and the event is
    within the time window, emit one CSV row.
  - Columns: `recv_ns, iso_wib, price, vol, side, trade_num`
    where `side` is `"B"`, `"S"`, or `""` (empty for pre-open batch), and
    `trade_num` is the running trade sequence number from the feed.
  - **Skip** events where `side==""` (pre-open auction batch) — flag this at the
    top of the file header with a comment.
- **Edit `collector/cmd/sbcap/main.go`** — add `case "trades":` dispatch and a
  usage line.
- **New `collector/cmd/sbcap/trades_test.go`** — table-driven: side parsing
  (`B`, `S`, empty), time-window filter, multi-symbol filter.

### 4.2 Python: `study/signed_flow.py`

Reads both the features CSV (for mid price) and the trades CSV (for signed
flow), joins them, and runs:

1. **Signed flow aggregation** — sum `ε·vol` over rolling windows (1s, 5s, 10s,
   30s); `ε = +1` for `side=B`, `−1` for `side=S`.
2. **Kyle's lambda estimation** — OLS `Δmid_t ~ Q_[t−Δt,t]` (HAC SE) for each
   window width. Report `λ` in IDR per 100 lots with 95% CI.
3. **OBI → trade sign prediction** — for each trade, look up OBI₁ at the
   nearest prior book update. Fit logistic regression:
   `P(side=B | OBI₁) = σ(α + β·OBI₁)`. Report hit-rate conditional on
   `OBI₁ > 0.3` and `OBI₁ < −0.3`.
4. **Incremental R² of signed flow over OBI**: OLS
   `fwd_mid_ret ~ OBI1 + Q_[t,t+5s]` — does knowing the flow that arrived *after*
   the snapshot add to what the snapshot already told us?
5. **Outputs:** `out/signed_flow_report.md` and PNGs (λ scatter, OBI→trade-sign
   binned bar chart).

### 4.3 Tasks

- [x] `trades.go`: `runTrades`, flag parsing, CSV emit
- [x] `main.go`: dispatch + usage for `trades`
- [x] `trades_test.go`: table-driven
- [x] `go test -race ./cmd/sbcap/...` green
- [x] `study/signed_flow.py`: signed-flow aggregation, λ OLS, OBI→sign logistic,
  incremental R², report + PNGs

---

## 5. Part C — Multi-symbol OBI generalization

### 5.1 Approach

No new Go code. Run the existing `sbcap features` pipeline for each of the six
symbols on the June 18 segment, then run a Python script that replicates the
plan 04 study for each.

```bash
for SYM in ANTM BUMI DEWA INDY ITMG MDKA; do
  /tmp/sbcap features ../segments/seg-013634.binpb.zst \
    --symbol $SYM --depth 5 \
    --from 09:00 --to 16:15 \
    > study/out/${SYM}_features.csv
done
```

### 5.2 Python: `study/multisym_obi.py`

Loops over the six feature CSVs and for each symbol runs the plan 04 OBI study
(decile bins, Pearson/Spearman, HAC OLS, conditional hit-rate, block-bootstrap
null, chronological OOS). Produces:

- Per-symbol table: `(symbol, horizon, OBI1_hit_rate_oos, p_vs_null, passes_gate)`
- Summary: hit-rate boxplot across symbols; flag any name with reversed effect.
- `out/multisym_obi_report.md`.

**Verdict criterion:** all six symbols at ≥0.55 OOS hit-rate (conditional on a
move) at the 5s horizon → strong generalisation. Any reversal (<0.45) → flag
for investigation before including in a multi-name backtest.

### 5.3 Tasks

- [x] Emit six feature CSVs (bash loop above)
- [x] `study/multisym_obi.py`: per-symbol OBI study loop, cross-symbol table,
  boxplot, report

---

## 6. Part D — Pre-opening IEP study (bonus)

### 6.1 Approach

Filter the BUMI (and optionally all-symbol) features CSV to the pre-opening
window `08:45–09:00 WIB` where IEP is populated. One IEP observation per
IEPIEV event, carried forward in the features CSV as is.

### 6.2 Python: `study/preopen_iep.py`

For each symbol:

1. Extract IEP time series from the features CSV (`08:45–08:58` WIB window).
2. Compute `iep_direction = sign(IEP_final − IEP_first)` over the call auction.
3. Compute `open_direction = sign(mid_at_09:01 − mid_at_08:58)` (first
   mid after the auction clears vs. last IEP mid).
4. Test: `P(iep_direction == open_direction)` across the 6 symbols. Report as a
   simple frequency table (n=6, purely illustrative).
5. Also report `OBI₁` at the last book update before 09:00 and whether its sign
   matched the opening direction.
6. `out/preopen_iep_report.md`.

This is a **descriptive study only** — with 6 data points (one per symbol, one
day) no statistical inference is valid. It tells us whether the IEP/OBI signal
is even directionally consistent before we invest in collecting more pre-open
data.

### 6.3 Tasks

- [x] `study/preopen_iep.py`: IEP trajectory, opening-direction match, OBI at
  last pre-open book update, report

---

## 7. Files

| File | Change |
|------|--------|
| `collector/cmd/sbcap/features.go` | edit — add `delta_bid_vol`, `delta_ask_vol` columns + dirty-state tracking |
| `collector/cmd/sbcap/features_test.go` | edit — delta-column table cases |
| `collector/cmd/sbcap/trades.go` | **new** — `runTrades`, per-trade CSV emitter |
| `collector/cmd/sbcap/trades_test.go` | **new** — table-driven trades tests |
| `collector/cmd/sbcap/main.go` | edit — dispatch + usage for `trades` |
| `collector/study/ofi_obi_compare.py` | **new** — OFI vs OBI comparison study |
| `collector/study/signed_flow.py` | **new** — signed flow, Kyle's lambda, OBI→trade-sign |
| `collector/study/multisym_obi.py` | **new** — multi-symbol OBI generalization |
| `collector/study/preopen_iep.py` | **new** — pre-opening IEP study |
| `docs/thesis/01-microstructure-signals.md` | edit — update OFI status from ⛔ blocked to ⏳ pending |
| `docs/thesis/02-order-flow-and-price-impact.md` | **new** — thesis for OFI, signed flow, Kyle's lambda, pre-open |
| `docs/plans/README.md` | edit — register plan 05 |
| `docs/thesis/README.md` | edit — register thesis doc 02 |

Reused as-is: `internal/replay/replay.go`, `internal/feed/codec.go`,
`cmd/sbcap/book.go`, `collector/study/obi_forward_return.py` (plan 04 script).

---

## 8. Verification

```bash
cd collector

# Build
go build -o /tmp/sbcap ./cmd/sbcap
go test -race ./cmd/sbcap/...

# Emit BUMI features with delta columns
/tmp/sbcap features ../segments/seg-013634.binpb.zst \
  --symbol BUMI --depth 5 --from 09:00 --to 16:15 \
  > study/out/bumi_features_jun18.csv

# Check delta columns present, non-empty rows after first
head -3 study/out/bumi_features_jun18.csv

# Emit BUMI trades (continuous session, side-tagged)
/tmp/sbcap trades ../segments/seg-013634.binpb.zst \
  --symbol BUMI --from 09:00 --to 16:15 \
  > study/out/bumi_trades_jun18.csv

wc -l study/out/bumi_trades_jun18.csv  # should be ~49k rows

# Run studies
python study/ofi_obi_compare.py --csv study/out/bumi_features_jun18.csv
python study/signed_flow.py \
  --features study/out/bumi_features_jun18.csv \
  --trades   study/out/bumi_trades_jun18.csv
python study/multisym_obi.py --dir study/out/
python study/preopen_iep.py  --dir study/out/
```

Manual checks: OFI report states whether incremental R² passes the 5pp
threshold; lambda estimate is in `signed_flow_report.md` with CI; multi-symbol
table lists all 6 hit-rates; pre-open report is labelled "descriptive only."

---

Back to [Plans index](./README.md)
