# OBI / Micro-Price Go/No-Go Study

Statistical harness for the microstructure signal study described in
[`docs/plans/04-microstructure-study.md`](../../docs/plans/04-microstructure-study.md).
Tests whether order-book imbalance (OBI) and the weighted-mid predict BUMI's
short-horizon forward mid-price change.

## Prerequisites

- Go toolchain (for `sbcap features`)
- Python 3.13+ (matches `collector/viz/`)
- The two BUMI segments from `segments/dt=2026-06-17/default/`

## End-to-end regenerate

```bash
# 1. Build sbcap (from the collector/ directory)
cd collector
go build -o /tmp/sbcap ./cmd/sbcap

# 2. Emit the features CSV for both BUMI segments (one ordered stream)
mkdir -p study/out
/tmp/sbcap features \
  segments/dt=2026-06-17/default/seg-044221.binpb.zst \
  segments/dt=2026-06-17/default/seg-062241.binpb.zst \
  --symbol BUMI --depth 5 > study/out/bumi_features.csv

wc -l study/out/bumi_features.csv   # sanity: ~33k+ rows

# 3. Set up Python environment (first run only)
cd study
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Run the analysis
python obi_forward_return.py --csv out/bumi_features.csv

# Outputs:
#   out/report.md       — PASS / NO-GO verdict + full metrics table
#   out/decile_*.png    — decile-bin curves (Lipton-style)
#   out/scatter_*.png   — signal vs forward-return scatter
#   out/bootstrap_*.png — block-bootstrap null distributions
```

## Go/no-go criteria

**PASS** if, on held-out data (last 40 % of the long session), at least one
(signal, horizon) pair simultaneously satisfies **all three**:

1. **Monotone decile curve** — mean `fwd_mid_ret` is directionally consistent
   across the signal deciles (≥ 80 % of adjacent-decile steps agree in direction).
2. **Clears bootstrap null** — the real Pearson |r| or directional hit-rate
   exceeds the 95th percentile of the circular block-bootstrap null distribution.
3. **Economically meaningful** — `|Pearson r| > 0.02` or `hit-rate > 0.52`
   conditional on `|fwd_mid_ret| > 0`.

**NO-GO** otherwise: book-only signals do not transfer to BUMI on this dataset;
defer to a trade-tape (OFI) capture before building a backtest harness.

## Signals computed

| Signal | Description |
|--------|-------------|
| `OBI1` | Top-of-book imbalance: `(bid_vol_0 − ask_vol_0) / (bid_vol_0 + ask_vol_0)` |
| `OBI_eq_N` | Equal-weight depth-N imbalance: sums all N levels with weight 1 |
| `OBI_w_N` | Depth-weighted N-level imbalance: `w_i = 1/(i+1)` (thesis §2.2) |
| `wmid_skew` | Weighted-mid displacement from mid, normalised by half-spread |

Forward return horizons: 1 s, 5 s, 10 s, 30 s, 60 s.

## Standing caveats

- **One name, one day, two sessions** — feasibility gate only, not generalisable
  proof of a trading signal.
- **No trade tape** — OFI (Cont–Kukanov–Stoikov 2014, the strongest signal) is
  untestable; the BUMI segments captured 0 running trades.
- **1-tick stock** — forward mid is frozen most of the time; the
  conditional-on-move analysis (hit-rate with `|fwd_mid_ret| > 0`) is the
  real test, not the overall correlation.
- **Stoikov micro-price g(I,S) fitting** (docs/thesis §3.2) is deferred — only
  if weighted-mid/OBI clear this gate; it requires estimating Markov transition
  matrices from the data.
