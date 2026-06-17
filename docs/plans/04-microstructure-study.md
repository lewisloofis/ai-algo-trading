# Microstructure Signal Study — OBI / Micro-Price Go/No-Go Gate

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-17
> **Related:** [Thesis: microstructure signals](../thesis/01-microstructure-signals.md) · [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md)

This plan builds the harness that **tests the microstructure thesis** on the BUMI
data we already captured: does **order-book imbalance (OBI)** or the **weighted-mid /
micro-price** predict BUMI's short-horizon forward price move? It is the **go/no-go
gate** the thesis ([thesis §5–§6](../thesis/01-microstructure-signals.md)) calls for —
cheap to run on existing data, and decisive about whether a book-only signal is worth
pursuing on a thin 1-tick IDX name before we invest in a backtest harness.

> ⚠️ **Disclaimer.** See [01-market-data-capture.md §1](./01-market-data-capture.md)
> for the disclaimer on undocumented Stockbit endpoints and ToS.

---

## 1. Context — why this plan exists

The thesis ([thesis/01](../thesis/01-microstructure-signals.md)) argues that on a thin,
1-tick name like BUMI the **printed price is a poor signal** — coarse (sits on integer
ticks), laggy (only updates after a trade prints), and bid-ask-bounce contaminated —
and that direction should instead be read from the **resting order book** via OBI and
the weighted-mid / micro-price. Every cited result (Kyle 1985, Glosten-Milgrom 1985,
Lipton 2013, Stoikov 2018, Cont-Kukanov-Stoikov 2014) was derived on **developed
markets**; the thesis is explicit (§5) that there is **no guarantee it transfers** to a
thin, low-priced, 1-tick IDX name, and frames the first study as a **go/no-go gate**: if
OBI/weighted-mid have no forward-return power on BUMI, book-only strategies won't work
and we wait for the trade tape (OFI) before building a backtester.

**We already have the data to run that gate.** `segments/dt=2026-06-17/default/` holds
two usable BUMI segments:

| Segment | WIB span | Data records | Orderbook | Liveprice | Trades |
|---------|----------|--------------|-----------|-----------|--------|
| `seg-044221.binpb.zst` | 11:42–12:03 (Session 1 tail) | 4,975 | 3,981 | 866 | **0** |
| `seg-062241.binpb.zst` | 13:22–16:15 (Session 2) | 42,695 | 33,731 | 7,934 | **0** |

The **trade tape is empty** (0 running trades across all segments — BUMI is thin and the
windows captured no prints), so **OFI ([thesis §6 step 4](../thesis/01-microstructure-signals.md))
is out of scope**: only OBI and the weighted-mid are testable on this data.

**The infrastructure already exists** — the only missing pieces are a feature extractor
and the statistics:
- `internal/replay/replay.go` — `Replay(segments, ModeFast, handler)` emits a
  time-ordered `MarketEvent` stream (`replay.go:75`).
- `internal/feed/codec.go` — `ParseOrderbookBody` → `BookSide{Side, Levels[]Level{Price,Volume}}`;
  `Decoded.LastPrice` for liveprice.
- `cmd/sbcap/book.go` — the exact BID/OFFER side-caching pattern we reuse (`book.go:54-92`).

**Outcome:** a reproducible harness emitting a PASS / NO-GO verdict — does book imbalance
predict BUMI's forward **mid** move at 1s/5s/30s, beyond a null baseline, out-of-sample —
with the honesty controls thin, autocorrelated, single-name data demands.

## 2. Locked decisions

1. **Hybrid Go + Python.** A Go `sbcap features` subcommand reuses the existing
   replay/decoder to emit a consolidated book+price feature table (CSV); a Python script
   does all statistics + plots. Matches the `collector/viz/` Python precedent; avoids
   reimplementing segment/zstd/protobuf decode in Python (DRY).
2. **Scope = OBI/micro-price gate on existing BUMI data.** OFI and a fresh liquid-name
   capture are **not** in this plan (no trade tape).
3. **Target = forward MID change**, not last-price return. Last price is the contaminated
   signal the thesis rejects; using it as the *target* would bake that noise in. Last-price
   return is computed only as a secondary cross-check.
4. **Significance from HAC + a block-bootstrap null**, never naive p-values — millions of
   book updates are heavily autocorrelated and not independent observations.
5. **Out-of-sample is chronological only.** Random splits leak (adjacent ticks correlate).

## 3. Part A — Go: `sbcap features` subcommand

A fifth subcommand alongside `stat/dump/export/book`, reusing the side-caching pattern in
`cmd/sbcap/book.go` and `feed.ParseOrderbookBody`.

- **New `collector/cmd/sbcap/features.go`** — `runFeatures(args)`:
  - Flags: `--symbol S` (required), `--depth N` (default 5), `--from/--to HH:MM`
    (optional, reuse the WIB parsing in `book.go`/`dump.go`).
  - Roll state over `replay.Replay(segs, replay.ModeFast, …)`: latest BID `feed.BookSide`,
    latest OFFER `feed.BookSide`, latest `last_price` (`EventLivePrice` → `Decoded.LastPrice`),
    latest `iep` (`EventIEPIEV`, nullable).
  - Sort BID descending / OFFER ascending (as `book.go` does) so index 0 is top-of-book.
  - **Emit one CSV row per orderbook update once both sides are known**, carrying the most
    recent last_price/iep forward. Columns:
    `recv_ns, iso_wib, bid_seq, offer_seq, last_price, iep,` then `i` in `0..N-1`:
    `bid_px_i, bid_vol_i, ask_px_i, ask_vol_i` (empty cells for missing depth).
  - Accept multiple segment paths so the two BUMI segments replay as one ordered stream.
- **Edit `collector/cmd/sbcap/main.go`** — add `case "features":` dispatch + a usage line
  (mirror `book` wiring at `main.go:52-56`, `usage()` at `:64`).
- **New `collector/cmd/sbcap/features_test.go`** — table-driven (Go rules): top-of-book
  after sort, last_price carry-forward, no rows before both sides seen, depth
  truncation/padding. Build a tiny in-memory segment via `segment.Writer` (cf.
  `replay_test.go`) or unit-test the row-assembly helper with synthetic `feed.BookSide`.

### Tasks
- [ ] `features.go`: `runFeatures` + flag parsing + state struct + CSV emit
- [ ] `main.go`: dispatch + usage
- [ ] `features_test.go`: table-driven snapshot-assembly tests
- [ ] `go test -race ./cmd/sbcap/...` green

## 4. Part B — Python: the statistical test

**New dir `collector/study/`** (sibling to `collector/viz/`):
- `requirements.txt` — `pandas numpy scipy statsmodels matplotlib`
- `obi_forward_return.py` — reads features CSV (`--csv`), writes `out/report.md` + PNGs
- `README.md` — regenerate steps, go/no-go criteria, caveats
- `.gitignore` — ignore `out/`

**Signals** (tick size = 1 for BUMI):
- `mid = (best_bid + best_ask)/2`, `spread`, `wmid = (bb·ask_vol0 + ba·bid_vol0)/(bid_vol0+ask_vol0)`
- `OBI1 = (bid_vol0 − ask_vol0)/(bid_vol0 + ask_vol0)`
- `OBI_N` depth-weighted, equal weight and `w_i = 1/(i+1)` ([thesis §2.2](../thesis/01-microstructure-signals.md))

**Forward returns:** per horizon `h ∈ {1,5,10,30,60}s`, as-of (`searchsorted`) join of
`mid` at `t+h`; `fwd_mid_ret = mid(t+h) − mid(t)`. Drop joins crossing the lunch gap
(12:00–13:30 WIB) or running past end-of-data. `fwd_last_ret` computed as a secondary target.

**Tests per (signal × horizon):**
1. **Decile-bin curve** — mean `fwd_mid_ret` per OBI decile (Lipton-style); monotone ⇒ signal.
2. **Pearson + Spearman** correlation vs `fwd_mid_ret`.
3. **OLS** `fwd_mid_ret ~ signal` with **Newey-West HAC** SE → slope (Kyle-λ analog), t-stat, R².
4. **Directional hit-rate** conditional on `|fwd_mid_ret| > 0`: `P(sign match)` vs base rate
   (1-tick mid is frozen most of the time — the conditional test is the real one).
5. **Block-bootstrap null** — circular block resample to break OBI→return while preserving
   autocorrelation; real metric must clear the null 95th percentile.
6. **Chronological OOS** — fit sign/threshold on first 60% of Session 2, score on last 40%
   (and on the Session 1 segment as a second holdout). Random splits forbidden.

**Multiple-testing:** many signals × horizons → report a Bonferroni note; treat lone
"significant" cells skeptically.

**Verdict (top of `report.md`):** PASS if, on held-out data, ≥1 (signal, horizon) shows a
**monotone decile curve** AND a correlation/hit-rate **beating the bootstrap null 95th pct**
AND an **economically meaningful** magnitude. Else NO-GO → book-only signal does not
transfer to BUMI; defer to a trade-tape (OFI) capture before building a backtester.

### Tasks
- [ ] `requirements.txt`, `README.md`, `.gitignore`
- [ ] `obi_forward_return.py`: load + signals + forward-return as-of join (lunch-gap aware)
- [ ] decile bins, Pearson/Spearman, HAC OLS, conditional hit-rate
- [ ] block-bootstrap null + chronological OOS split
- [ ] markdown report + decile / hit-rate / scatter PNGs
- [ ] run on the two BUMI segments → record the verdict

## 5. Standing caveats (carried into report.md and study/README.md)

- One name, one day, two sessions → **feasibility gate, not generalizable proof**.
- No trade tape → **OFI (the strongest signal) untestable now**.
- 1-tick stock → forward mid mostly frozen; the conditional-on-move analysis is the real test.
- Stoikov micro-price `g(I,S)` fitting ([thesis §6 step 3](../thesis/01-microstructure-signals.md))
  is **deferred** — only if weighted-mid/OBI clear the gate; needs Markov transition-matrix estimation.

## 6. Files

| File | Change |
|------|--------|
| `collector/cmd/sbcap/features.go` | **new** — `runFeatures`, consolidated book+price CSV emitter |
| `collector/cmd/sbcap/main.go` | edit — dispatch + usage for `features` (mirror `book`) |
| `collector/cmd/sbcap/features_test.go` | **new** — table-driven snapshot-assembly tests |
| `collector/study/obi_forward_return.py` | **new** — regression / bins / hit-rate / null / OOS + plots |
| `collector/study/requirements.txt` | **new** — pandas, numpy, scipy, statsmodels, matplotlib |
| `collector/study/README.md` | **new** — regenerate steps, criteria, caveats |
| `collector/study/.gitignore` | **new** — ignore `out/` |
| `docs/thesis/01-microstructure-signals.md` | edit — cross-link §6 to `collector/study/`; flip status note once a verdict exists |
| `docs/plans/README.md` | edit — register this plan in the index |

Reused as-is: `internal/replay/replay.go`, `internal/feed/codec.go`
(`ParseOrderbookBody`, `BookSide`, `Level`, `Decoded`), `cmd/sbcap/book.go`.

## 7. Verification (end-to-end)

```bash
cd collector
go build -o /tmp/sbcap ./cmd/sbcap
go test -race ./cmd/sbcap/...        # features_test.go green

# Emit features for both BUMI segments (ordered) → CSV
/tmp/sbcap features \
  segments/dt=2026-06-17/default/seg-044221.binpb.zst \
  segments/dt=2026-06-17/default/seg-062241.binpb.zst \
  --symbol BUMI --depth 5 > study/out/bumi_features.csv

wc -l study/out/bumi_features.csv    # ≈ #orderbook updates after both sides seen

python -m pip install -r study/requirements.txt
python study/obi_forward_return.py --csv study/out/bumi_features.csv
# Inspect study/out/report.md (PASS/NO-GO + tables) and the decile/hit-rate PNGs
```

Manual checks: decile-curve direction matches the correlation sign; the bootstrap-null
band is plotted; OOS hit-rate is reported separately from in-sample. The verdict in
`report.md` is the deliverable.

---

Back to [Plans index](./README.md)
