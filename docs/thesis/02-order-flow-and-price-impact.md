# Order Flow, Price Impact & the Trade Tape

This note documents the **academic thesis** behind the next layer of signals we
study once a live trade tape is available: **order flow imbalance (OFI)**,
**signed trade flow**, and **Kyle's lambda (price impact)**. It also covers the
microstructure of the **pre-opening call auction** and the **IEP** signal it
produces.

> **Status:** study complete — see [plan 05](../plans/05-ofi-trade-flow-study.md)
> and reports in [`collector/study/out/`](../../collector/study/out/).
> Data: `segments/seg-013634.binpb.zst` (2026-06-18, 6 symbols, full trading day,
> live trade tape with aggressor-side tags). Thesis doc 01 (OBI/micro-price)
> is the prerequisite — [read it first](./01-microstructure-signals.md).
>
> **Key results (2026-06-18, one day, feasibility gate only):**
> - **OFI vs OBI**: verdict `OFI_NO_GAIN` — incremental R² did not reach 5 pp OOS.
>   OBI1 alone: 0.77–0.82 OOS hit-rate on BUMI. Static snapshot is a sufficient
>   statistic on a 1-tick book; OFI adds no reliable gain.
> - **Kyle's lambda**: λ ≈ 0 IDR/100 lots (t ≈ 3.4, statistically significant but
>   economically negligible on the 1-tick scale). Signed flow adds no OOS R² over OBI.
> - **OBI → trade sign**: AUC 0.55; hit-rate 0.58 at |OBI1| > 0.3.
> - **Multi-symbol OBI**: `STRONG` — 6/6 symbols pass (OOS hit-rates 0.64–0.99 at 5s).
> - **Pre-opening IEP**: IEP stable (no direction change) across all 6 symbols
>   on 2026-06-18; OBI1 strongly buy-side at open but open price unchanged from
>   pre-open mid. Descriptive only (n=6, 1 day).

---

## 1. From static snapshots to dynamic flow

[Thesis doc 01](./01-microstructure-signals.md) studies **OBI** — order-book
imbalance measured from the *resting* queue at a point in time:

```
OBI₁ = (V_bid − V_ask) / (V_bid + V_ask)
```

OBI is a **static snapshot**: it tells you where passive orders are sitting
*right now*. What it cannot tell you is *what is changing* — whether the bid
queue is building because buyers are flooding in, or draining because buyers are
being filled and not replaced. Two books can have the same OBI but completely
different dynamics.

**Order Flow Imbalance (OFI)** fixes this by measuring the *rate of change* of
the queue at the touch between consecutive book updates. It captures the
*momentum* of order flow, not just its current state. This is the difference
between a photograph and a video.

The practical consequence: on a 1-tick book like BUMI, OBI and the weighted-mid
are mathematically equivalent (`wmid − mid = (spread/2)·OBI₁`). OFI breaks
that degeneracy — it can distinguish "book is imbalanced and getting more
imbalanced" from "book is imbalanced but normalising."

---

## 2. Order Flow Imbalance (OFI) — Cont, Kukanov & Stoikov (2014)

### 2.1 Thesis

- **Cont, Kukanov & Stoikov (2014), "The price impact of order book events"**
  (*Journal of Financial Econometrics* 12(1), 47–88).
  The central empirical result: over a short window (e.g. 10s), mid-price
  changes are **linearly explained** by OFI with R² of 60–70% in their sample
  — far higher than either OBI or naive volume. The key insight is that **price
  changes are driven by the *flow* of orders into the book, not their resting
  level**. Buyers arriving at the touch push the best ask queue down; once it
  empties the price must tick up. OFI makes that depletion observable
  *continuously*, not just when a trade prints.

The four observable events at the best bid/ask between two consecutive book
snapshots (times `t−1` and `t`):

| Event | What happened | Direction |
|-------|--------------|-----------|
| `ΔBid⁺` | bid queue *grew* | buyers arriving passively |
| `ΔBid⁻` | bid queue *shrank* | buyers filled or cancelled |
| `ΔAsk⁺` | ask queue *grew* | sellers arriving passively |
| `ΔAsk⁻` | ask queue *shrank* | sellers filled or cancelled |

Buying pressure enters through `ΔBid⁺` (passive bids building) and `ΔAsk⁻`
(passive asks being consumed by aggressive buys). Selling pressure enters
through `ΔAsk⁺` and `ΔBid⁻`.

### 2.2 Formula

At the best bid/ask (level 0):

```
ΔBid⁺_t = max(0,  V_bid(t) − V_bid(t−1))
ΔBid⁻_t = max(0,  V_bid(t−1) − V_bid(t))
ΔAsk⁺_t = max(0,  V_ask(t) − V_ask(t−1))
ΔAsk⁻_t = max(0,  V_ask(t−1) − V_ask(t))

OFI_t = ΔBid⁺_t − ΔBid⁻_t − ΔAsk⁺_t + ΔAsk⁻_t
```

Note the signs: bid-side changes enter positively (buying pressure), ask-side
changes enter negatively.

`OFI_t > 0` → net buy-side flow into the book → expect upward mid move.
`OFI_t < 0` → net sell-side flow → expect downward mid move.

### 2.3 How to compute it from our data

Our `sbcap features` CSV already contains consecutive bid/ask volume snapshots
at each level. OFI at the touch requires only the level-0 columns
(`bid_vol_0`, `ask_vol_0`) from consecutive rows:

```python
df["delta_bid"] = df["bid_vol_0"].diff()
df["delta_ask"] = df["ask_vol_0"].diff()
df["ofi"] = (
    df["delta_bid"].clip(lower=0)          # ΔBid⁺
  - df["delta_bid"].clip(upper=0).abs()    # ΔBid⁻
  - df["delta_ask"].clip(lower=0)          # ΔAsk⁺
  + df["delta_ask"].clip(upper=0).abs()    # ΔAsk⁻
)
```

(Simplified — must handle bid-price changes and reconnect resets carefully.)

### 2.4 The open question for BUMI

On a 1-tick book, every change in queue depth at the touch is *already* fully
captured by OBI at the *next snapshot*. The question is whether OFI's
**within-snapshot resolution** — tracking the flow that *led to* the new OBI
level — adds predictive power that the resulting OBI does not. On thicker books
(many ticks, deep queues) OFI clearly dominates. On a 1-tick name the gap may
be small or zero. The study tests this directly.

---

## 3. Signed trade flow

### 3.1 What it is

Every trade has two counterparties — one **passive** (posted a resting limit
order and waited) and one **aggressive** (submitted a market order or a limit
order that immediately crossed the spread). The aggressive side is called the
**aggressor** or **trade initiator**.

**Signed trade flow** attaches a direction to each traded lot based on who was
aggressive:

```
ε_i = +1   if buyer initiated (aggressor hit the ask)   → side = B
ε_i = −1   if seller initiated (aggressor hit the bid)  → side = S

Q_[t, t+Δt] = Σᵢ ε_i · V_i      (sum over all trades in the window)
```

`Q > 0` → net buying urgency; `Q < 0` → net selling urgency.

### 3.2 Why aggressor identity matters

The intuition from Kyle (1985) and Glosten-Milgrom (1985): **the person in a
hurry knows something**. An aggressive buyer is willing to pay the ask (rather
than posting a bid and waiting) because they believe the price is going up and
they cannot afford to wait. Their urgency is informationally significant; the
passive seller's patience is less so.

A purely passive order provides liquidity but reveals little about private
information. An aggressive order consumes liquidity and reveals urgency — which
may reflect private information, a hedging need, or a deadline. Either way,
the market maker who is on the passive side must reprice to protect themselves,
which is why aggressive flow mechanically moves prices.

Practical consequence: even if you strip out all book information and only
observe signed trade volume, you can predict the *direction* of the next mid
move with above-chance accuracy — because the people crossing the spread are
already acting on a view.

### 3.3 Signed flow in our data

The Stockbit feed tags intraday trades with `side=B` or `side=S` during
continuous trading (09:00–12:00 and 13:30–16:15 WIB). The pre-opening auction
batch (printed at 08:58) has `side=` empty because there is no aggressor in a
call auction — both sides posted resting orders and the exchange matched them
simultaneously at the equilibrium price.

This means our signed-flow analysis is restricted to **continuous trading hours
only**.

### 3.4 Relationship between OFI and signed flow

They measure overlapping but distinct phenomena:

| Measure | What it counts |
|---------|---------------|
| OFI | Queue changes at the touch between book snapshots — includes limit order arrivals, cancellations, *and* executions |
| Signed flow | Only executed trades, tagged by aggressor direction |

OFI is broader — it also captures order arrivals and cancellations that never
execute. Signed flow is narrower but cleaner — executions are unambiguous
events, whereas a queue change could be a cancel-and-replace rather than genuine
new interest. On thin names where most flow is executed rather than cancelled,
the two converge.

---

## 4. Kyle's lambda — the price impact coefficient

### 4.1 Thesis

- **Kyle (1985), "Continuous Auctions and Insider Trading"** (*Econometrica*).
  The original result: in a model where an informed trader and noise traders
  submit orders, the market maker sets prices equal to expected value
  *conditional on aggregate order flow*. The equilibrium has a remarkable
  property: **price impact is linear in net signed order flow**, with slope λ
  (lambda):

```
Δprice = λ · Q + ε
```

where `Q` is signed net flow in a window and `ε` is noise. Kyle shows that λ
is pinned by the ratio of the informed trader's information advantage to the
depth of noise trading — the less noise trading there is, the higher λ (each
order carries more information on average).

### 4.2 What λ tells us

λ is the **price impact per unit of signed flow** — how many IDR the mid moves
per lot of net buying or selling pressure. It is the market's conversion rate
between order flow and price.

On a **liquid name** (e.g. BBCA on a high-volume day): λ is tiny — millions of
lots trade, each one carries almost no information, and the book is deep enough
to absorb most flow without repricing.

On a **thin name** like BUMI at ~169 IDR: λ is expected to be large — daily
turnover is low, any institutional order represents a large fraction of total
flow, and the 1-tick book has almost no queue depth to absorb it. A few
thousand lots of one-sided flow may be enough to flip the best bid/ask.

The study measures λ empirically by regressing `Δmid ~ Q` over the BUMI
continuous session. The estimate gives us a concrete answer to "how thick is
this market?" — and a sanity check before building any execution strategy. If
λ is large, even small orders will move the price against us.

### 4.3 Estimation in practice

```
Δmid_t = λ · Q_[t−Δt, t] + α + ε_t
```

Fit with **Newey-West HAC** standard errors (same as the OBI regression) because
sequential trade flow is autocorrelated. Report λ in units of IDR per 100 lots.
Check: if λ is economically huge (e.g. 1 tick per 500 lots), that is a warning
that execution cost will dominate any signal.

### 4.4 OBI as a predictor of trade sign

A clean auxiliary test: does OBI before a trade predict whether that trade is
buyer- or seller-initiated?

```
P(ε_i = B | OBI₁ at time of trade) =?
```

If yes, the book is *leaning* in the direction of the next aggressor — which
means a market maker could skew quotes to extract the spread on the favoured
side, and a directional strategy could use OBI to anticipate the direction of
the next execution.

---

## 5. Pre-opening call auction microstructure

### 5.1 How the IDX pre-open works

The Indonesia Stock Exchange (IDX) runs a **call auction** before each session:

- **08:45–09:00 WIB** — pre-opening: participants submit limit orders;
  no executions yet. The exchange continuously publishes the **Indicative
  Equilibrium Price (IEP)** and **Indicative Equilibrium Volume (IEV)** — the
  price and quantity that would clear the maximum volume if the auction ended now.
- **09:00 WIB** — auction clears at the final IEP. A batch of executions is
  printed simultaneously (all with the same timestamp).
- **09:00–12:00 WIB** — Session 1 continuous trading begins.

The same structure repeats before Session 2 (lunch break 12:00–13:30 WIB).

### 5.2 The price-discovery thesis

During the call auction, information is being aggregated into the order book
in real time. The IEP trajectory is a running estimate of where participants
think fair value is. Academic work on call-auction microstructure (e.g.
**Madhavan 1992**, **Biais, Hillion & Spatt 1999**) shows:

- The IEP converges to the final opening print as the auction nears close.
- Late-arriving informed orders push the IEP most — early orders are more likely
  to be noise.
- The **book imbalance just before the auction clears** (i.e. the ratio of total
  resting bid lots to ask lots at the IEP) predicts whether the continuous
  session opens with buy or sell pressure.

### 5.3 What we can test

From the June 18 segment, the pre-open is visible from 08:45 to 08:58 WIB
(first continuous IEPIEV events). The questions are:

1. **IEP trajectory**: how much does the IEP move during the call, and does the
   direction of change predict the opening trade direction?
2. **Book imbalance at last IEP**: `sign(OBI₁ at 08:58)` vs
   `sign(Δmid from 08:58 to first 09:00 trade)` — a simple directional hit test.
3. **IEV as confidence**: does higher IEV (more volume clearing) at close predict
   a more directionally persistent first few minutes?

This is a small sample (one pre-open per day per symbol) so the test is
illustrative, not statistically decisive. It informs whether collecting the
pre-open book is worth doing systematically.

---

## 6. Multi-symbol generalization

The OBI gate passed on BUMI — one name, one day. The June 18 segment provides
five additional names: ANTM, DEWA, MDKA, INDY, ITMG. These are all LQ45 mining
names with varying liquidity profiles.

Running the same OBI/OFI harness across all six tests **cross-sectional
generalizability** — whether the imbalance-predicts-direction finding is a
property of thin IDX microstructure broadly, or specific to BUMI's particular
tick regime and participant mix.

Expected pattern if the thesis holds: hit-rates should be above 0.50 on all
names, but the magnitude should vary with liquidity — thinner names (INDY/ITMG
at ~2,500 trades today) should show stronger OBI effects than more active names
(BUMI at 49,000+ trades).

If a name shows a *reversed* or *zero* effect, that is a genuine falsification
of the book-imbalance thesis for that name and warrants investigation before
including it in any multi-name backtest.

---

## 7. Open questions for IDX / thin names

- **1-tick degeneracy**: on a 1-tick book, OBI and the weighted mid are the same
  signal. OFI *may* break the degeneracy but only if the intra-snapshot dynamics
  differ from what the snapshot OBI already captures. This is what the study
  resolves.
- **No side tag in pre-open batch**: the auction-clearing trades printed at 08:58
  have `side=` empty — they are bilateral matched, not aggressor-tagged. Signed
  flow is undefined for these.
- **Batch delivery**: trade events arrive in batches (all 08:58 prints arrive at
  the same `recv_ns`). Trade-level timing is therefore recv-time-of-batch, not
  exact match time. This limits the precision of OFI/signed-flow joins.
- **Thin names, small sample**: INDY and ITMG have ~2,500 trades each on this
  day. Statistical tests will have wide confidence intervals; interpret carefully.
- **Single day**: June 18 is one day. Results are reproducibility checks, not
  full backtests.

---

## 8. Implementation plan

Built as [plan 05](../plans/05-ofi-trade-flow-study.md). Status per question:

1. **OFI vs. OBI comparison (BUMI)** — ✅ done. Verdict: `OFI_NO_GAIN`. OFI incremental R² < 5 pp OOS; OBI1 at 0.77–0.82 hit-rate is the sufficient statistic on a 1-tick book.
2. **Signed flow + Kyle's lambda (BUMI)** — ✅ done. λ ≈ 0 IDR/100 lots (tiny but t ≈ 3.4); signed flow adds no OOS R² over OBI1.
3. **OBI → trade-sign prediction** — ✅ done. AUC 0.55; hit-rate 0.58/0.56 at |OBI1| > 0.3 buy/sell.
4. **Multi-symbol OBI generalization** — ✅ done. Verdict: `STRONG`. 6/6 symbols pass (OOS hit-rates 0.64–0.99 at 5 s).
5. **Pre-opening IEP study** — ✅ done (descriptive). IEP stable all 6 symbols on 2026-06-18; 0/0 assessable direction changes.

---

## References

- Kyle, A. S. (1985). *Continuous Auctions and Insider Trading.* Econometrica
  53(6), 1315–1335.
- Glosten, L. R., & Milgrom, P. R. (1985). *Bid, Ask and Transaction Prices in
  a Specialist Market with Heterogeneously Informed Traders.* Journal of
  Financial Economics 14(1), 71–100.
- Cont, R., Kukanov, A., & Stoikov, S. (2014). *The price impact of order book
  events.* Journal of Financial Econometrics 12(1), 47–88.
- Madhavan, A. (1992). *Trading mechanisms in securities markets.* Journal of
  Finance 47(2), 607–641.
- Biais, B., Hillion, P., & Spatt, C. (1999). *Price discovery and learning
  during the preopening period in the Paris Bourse.* Journal of Political Economy
  107(6), 1218–1248.
- Cartea, Á., Jaimungal, S., & Penalva, J. (2015). *Algorithmic and
  High-Frequency Trading.* Cambridge University Press.
