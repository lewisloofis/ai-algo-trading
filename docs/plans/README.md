# Market-Data Capture & Replay — Plans

This directory contains the design and implementation plans for the cheapest
correct way to **record Stockbit market-data to disk, archive it on AWS, and
replay it for backtesting** — without re-architecting the prototype.

## Goal in one paragraph

Stream live price, running-trade, and orderbook data from the Stockbit
market-data WebSocket for a configurable watchlist of ~10–50 IDX symbols.
Append every raw Protobuf frame to a local binary log as it arrives (zero decode
on the hot path). Roll and zstd-compress log segments daily. Ship compressed
segments to an S3 bucket in `ap-southeast-3` (Jakarta). Run only during IDX
market hours via EventBridge scheduling so compute cost is effectively zero for
most of the day. For backtesting, a replayer reads the same raw bytes through
`collector/datafeed.go`'s existing `decodeWrap` function, feeding a strategy the
same event stream it would see live — no code changes required between live and
replay mode.

## Plans

| Doc | Status | What it covers |
|-----|--------|----------------|
| [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) | 📝 Draft | **The overarching map** — what separates "one rigorous research loop" (where plans 04–06 leave us) from a complete, profit-generating trading system. Five pillars: the **alpha factory** (universe screening, multi-day data, signal pipeline, multiple-testing discipline, signal combination), **execution**, **portfolio & risk**, **infra & ops**, and **capital/governance**. Names every gap, places plans 01–06 within it, and fixes the sequencing: the **40 bps round-trip cost gate governs the whole strategy class**, so "find an edge that clears it" comes before any execution/risk machinery. Immediate next: a data-chosen universe screen and unattended multi-day capture. |
| [01-market-data-capture.md](./01-market-data-capture.md) | 🔨 In Progress | End-to-end capture architecture: locked decisions, component design, AWS deployment (compute options + cost table), OpenTofu provisioning, failure modes, and phased implementation steps. |
| [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) | ✅ Approved | Precise on-disk and S3 specification: binary record framing (byte-level layout), segment naming, JSON manifest schema, zstd rationale, S3 layout + lifecycle policy, replayer design with pseudocode, and optional Parquet analytics schema. |
| [03-token-rotation.md](./03-token-rotation.md) | 📝 Draft | Seamless, self-healing Stockbit token rotation: SSM as authoritative source, version-based hot-reload so a laptop-seeded token is adopted mid-session without restart or clobber, hardened env precedence, and **headless re-login** on refresh-token death via a trusted client-chosen `player_id` (device trust carries no server-issued id; `signature` optional). Includes a gating trust+IP validation spike, the operator swap runbook, and a trading-token extension path. |
| [04-microstructure-study.md](./04-microstructure-study.md) | ✔️ Done · **INCONCLUSIVE** (post-audit) | The **go/no-go gate** for the [microstructure thesis](../thesis/01-microstructure-signals.md): a `sbcap features` Go subcommand emits a consolidated book+price CSV from the captured BUMI segments, and a Python harness tests whether **OBI / weighted-mid** predict forward **mid** returns at 1s/5s/30s — decile curves, HAC regression, conditional hit-rate, block-bootstrap null, and chronological out-of-sample. **Result: originally PASS (0.75–0.80 raw OOS hit-rate), downgraded to INCONCLUSIVE** after an audit found the hit-rates inflated by overlapping forward windows — de-overlapped, the best cell holds ~0.56 on only 18 independent moves (< MIN_EFF_N=30). No look-ahead bug. Signal is directional but a single day is too thin. OFI out of scope (empty trade tape). |
| [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) | ✔️ Done · **NO-GO** | The **tradeability go/no-go gate**: a Python fill simulator + backtester (`collector/study/backtest.py`) over the existing `sbcap features`/`trades` CSVs that nets the OBI signal against the real cost stack ([reference 07](../reference/07-fees-and-costs.md): 40 bps round-trip). Taker fills (primary) + passive/queue-aware fills (sensitivity), long-only retail base case, fixed-horizon exit mirroring the hit-rate study, three-state verdict with the same effective-N / bootstrap hygiene as 04/05. **Result: NO-GO** — every (symbol, model, threshold, horizon) cell loses −51 to −99 bps/trade at 0 % win-rate; `net − gross = −40 bps` everywhere (the fee), and the gross leg is already negative (spread + adverse tick). Even the passive best-case model loses. The tick-scale OBI1 edge does not clear retail IDX frictions; the Go event-driven engine stays deferred. |
| [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) | ✔️ Done | **OFI vs. OBI comparison, signed trade flow, Kyle's lambda, and multi-symbol OBI generalisation** — unlocked by the June 18 segment (49k+ BUMI trades with aggressor-side tags, 6 LQ45 mining names, full trading day). Extends `features.go` with book-delta columns, adds a `sbcap trades` subcommand, and runs four Python studies. **Results:** OFI adds nothing over OBI1 (`OFI_NO_GAIN`); signed flow adds nothing (Kyle's λ ≈ 0); multi-symbol OBI **`INSUFFICIENT_DATA` post-audit** — 5/6 names hold a de-overlapped OOS hit-rate of 0.69–0.78 on dozens of independent moves, but ANTM's raw 0.99 collapsed to 4 independent obs (the audit's headline cautionary case). See [thesis 02](../thesis/02-order-flow-and-price-impact.md). |

**Status legend:** 📝 Draft · ✅ Approved · 🔨 In Progress · ⛔ Blocked · ✔️ Done · 🗑️ Superseded. Plans are tracked like tickets — each carries a status header and tickable task checklists per [ADR-0001 — Treat planning docs as tickets](../adr/0001-treat-plans-as-tickets.md).

## Cheapest setup at a glance

- **Compute:** EC2 `t4g.nano` (Graviton/ARM) in `ap-southeast-3`, started and
  stopped by EventBridge around IDX session hours (08:40–16:30 WIB) — roughly
  170 active hours/month → under $1/month in compute.
- **Storage:** S3 Standard for the current week's compressed segments, automatic
  transition to Glacier Deep Archive after 30 days. Full-microstructure data for
  ~30 symbols compressed with zstd runs to tens–low-hundreds of MB/day; monthly
  S3 cost is cents.
- **Code reuse:** the capture loop is the existing `collector/websocket.go` WS
  client plus a thin append layer; the replay path reuses `datafeed.go`'s
  `decodeWrap` directly — no new decode logic.

---

Back to [docs/README.md](../README.md)
