package live

import (
	"context"
	"math"
	"time"

	"strategy/engine"
)

// pendingCancel is a cancel decided on during pollOnce's locked pass, to be
// executed after the lock is released (see pollOnce's doc comment on why).
type pendingCancel struct {
	clientID string
	brokerID string
}

// pendingFill is a fill computed during pollOnce's locked pass, queued for
// delivery on s.fills after the lock is released. clientID, lotsFilled and
// matchedIDR are carried alongside the AsyncFill itself so pollOnce's
// sending loop can commit them to the tracked order's bookkeeping — but only
// once the fill has actually been handed off; see that loop's doc comment
// for why the commit must not happen any earlier.
type pendingFill struct {
	fill       engine.AsyncFill
	clientID   string
	lotsFilled int
	matchedIDR float64
}

// pendingReject is a REJECTED transition observed during pollOnce's locked
// pass, queued for delivery on s.rejects after the lock is released.
type pendingReject struct {
	reject   engine.AsyncReject
	clientID string
}

func (s *Sink) pollLoop(ctx context.Context) {
	defer s.wg.Done()
	ticker := time.NewTicker(s.pollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-s.done:
			return
		case <-ticker.C:
			s.pollOnce(ctx)
		}
	}
}

// pollOnce is one GET /order/v2/list, applied to every tracked order it
// matches. It gathers the fills to emit and the cancels to issue while
// holding mu, then releases mu before doing either: sending on s.fills can
// block until engine.Run's event loop drains it, and that same event loop
// is what calls Submit/Cancel, which also need mu — holding the lock across
// either the channel send or the cancel's HTTP call would risk a deadlock
// (the poller waiting on the event loop to read a fill, while the event
// loop is waiting on Submit/Cancel to acquire mu) and would in any case
// serialise unrelated work behind an outbound network call.
func (s *Sink) pollOnce(ctx context.Context) {
	listCtx, cancelList := context.WithTimeout(ctx, s.pollHTTPTimeout())
	orders, err := s.client.List(listCtx)
	cancelList()
	if err != nil {
		s.logf("live: poll: List: %v", err)
		return
	}

	now := s.clock()
	var toEmit []pendingFill
	var toCancel []pendingCancel
	var toReject []pendingReject

	s.mu.Lock()
	for _, snap := range orders {
		clientID, ok := s.byBroker[snap.OrderID]
		if !ok {
			continue // not one of ours — someone else's order, or our own baseline
		}
		t := s.orders[clientID]
		if pf, emit := s.applySnapshot(t, snap, now); emit {
			pf.clientID = clientID
			toEmit = append(toEmit, pf)
		}
		if pr, emit := s.rejectFor(clientID, t, snap, now); emit {
			toReject = append(toReject, pr)
		}
	}
	for clientID, t := range s.orders {
		if target, shouldCancel := s.checkTimeouts(clientID, t, now); shouldCancel {
			toCancel = append(toCancel, target)
		}
	}
	s.mu.Unlock()

	// Each fill is committed to the tracked order's own bookkeeping
	// (LotsFilled, MatchedAmountIDR) only once it has actually been handed
	// off here — never earlier, and never regardless of outcome. If Close()
	// or ctx cancellation aborts the send (either abort case below), the
	// commit is skipped: the order's LotsFilled stays at its last-delivered
	// value, so the next poll recomputes and retries the very same delta
	// instead of silently treating an undelivered execution as applied, and
	// ReconcileTerminal's lot_done comparison stays honest — a terminal
	// order whose last fill never reached engine.Run shows up as a
	// LotMismatch rather than a false-clean reconciliation. See
	// TestPoll_UndeliveredFillIsNotCommittedToBookkeeping.
	for _, pf := range toEmit {
		select {
		case s.fills <- pf.fill:
			s.mu.Lock()
			if t, ok := s.orders[pf.clientID]; ok {
				t.LotsFilled = pf.lotsFilled
				t.MatchedAmountIDR = pf.matchedIDR
			}
			s.mu.Unlock()
		case <-ctx.Done():
			return
		case <-s.done:
			return
		}
	}
	// Rejects are delivered the same way: handed off outside the lock, and
	// marked delivered (for Drain) only once the send succeeded.
	for _, pr := range toReject {
		select {
		case s.rejects <- pr.reject:
			s.mu.Lock()
			if t, ok := s.orders[pr.clientID]; ok {
				t.rejectDelivered = true
			}
			s.mu.Unlock()
		case <-ctx.Done():
			return
		case <-s.done:
			return
		}
	}
	for _, target := range toCancel {
		s.cancelForTimeout(ctx, target)
	}
}

// pollHTTPTimeout bounds a single poll's GET /order/v2/list call. It is
// capped at s.pollInterval so a slow request cannot itself run longer than
// the cadence it is supposed to keep, and floored at a few seconds so a very
// short PollInterval (mainly in tests) still gives a real request a chance
// to complete.
func (s *Sink) pollHTTPTimeout() time.Duration {
	const floor = 2 * time.Second
	if s.pollInterval < floor {
		return floor
	}
	return s.pollInterval
}

// applySnapshot updates t's state from snap and reports the pendingFill to
// emit, if any. Caller must hold mu.
//
// Fill emission is computed from the raw lot_done delta BEFORE the state
// transition is attempted, and independently of whether that transition
// turns out to be legal. This is what makes the IDX
// partial-after-full-match quirk (docs/reference/04-order-lifecycle.md
// §3.3) harmless rather than merely rejected: by the time a stray PARTIAL
// arrives after the order has already reached StateFilled, lot_done is
// already at lot_total, so the delta computed here is zero and no second
// fill is emitted — the illegal-transition rejection in Transition is a
// second, independent safety net, not the only thing preventing a double
// count.
//
// applySnapshot itself does NOT mutate t.LotsFilled or t.MatchedAmountIDR —
// that commit happens in pollOnce's sending loop, and only once the fill has
// actually been handed off on s.fills. See pendingFill's doc comment for
// why: committing here, unconditionally, would let a fill that never
// reached engine.Run still be recorded as if it had.
func (s *Sink) applySnapshot(t *trackedOrder, snap Snapshot, now time.Time) (pendingFill, bool) {
	var pf pendingFill
	var emit bool

	if lotDone := int(snap.Qty.LotDone); lotDone > t.brokerLotDone {
		t.brokerLotDone = lotDone
	}
	deltaLots := int(snap.Qty.LotDone) - t.LotsFilled
	if deltaLots > 0 {
		priceIDR, usedMatchedTotal := s.marginalFillPriceIDR(t, snap, deltaLots)
		if !usedMatchedTotal {
			s.logf("live: %s: marginal fill price derived from Price.Average.Price fallback, not matched_total delta — a multi-leg fill may be mispriced here", t.ClientOrderID)
		}
		pf = pendingFill{
			fill: engine.AsyncFill{
				Symbol: t.Symbol,
				Side:   t.Side,
				RecvNs: now.UnixNano(),
				Fill: engine.FillRecord{
					OrderID:      engine.OrderID(t.ClientOrderID),
					FillPriceIDR: priceIDR,
					FillLots:     deltaLots,
					// FeeIDR is the fee schedule's estimate on this leg's own
					// notional, not Carina's figure: see
					// SnapshotAveragePrice.Fee's doc comment for why the
					// broker's cumulative fee is not used.
					FeeIDR:      engine.ScheduleFeeIDR(t.Side, priceIDR*int64(deltaLots)*sharesPerLot),
					SlippageBps: slippageBps(t.DecisionPriceIDR, priceIDR),
				},
			},
			lotsFilled: int(snap.Qty.LotDone),
			matchedIDR: snap.Amount.MatchedTotal,
		}
		emit = true
	}

	// Completion is decided by lot_done == lot_total, never by seeing a
	// particular status text (the same §3.3 quirk): force the target state
	// to Filled whenever the broker's own quantities say every lot has
	// matched, even if status_text has not (yet) caught up to MATCH.
	target, ok := statusToState(snap.StatusText)
	if snap.Qty.LotTotal > 0 && snap.Qty.LotDone >= snap.Qty.LotTotal {
		target, ok = StateFilled, true
	}

	switch {
	case !ok:
		s.logf("live: %s: unrecognised status_text %q from Carina, state unchanged (%s)", t.ClientOrderID, snap.StatusText, t.State)
	default:
		if next, err := Transition(t.State, target); err != nil {
			s.logf("live: %s: %v (status_text=%q)", t.ClientOrderID, err, snap.StatusText)
		} else {
			if (next == StateOpen || next == StatePartial) && t.openSince.IsZero() {
				t.openSince = now
			}
			t.State = next
		}
	}
	t.UpdatedAt = now

	return pf, emit
}

// rejectFor reports the AsyncReject to emit when t has just been observed in
// StateRejected, exactly once per order (rejectQueued latches). The reason is
// Snapshot.Message — Carina's human-readable IDX/broker text on a REJECTED
// row (docs/reference/04-order-lifecycle.md §5, §7.4) — falling back to
// status_text when it is empty. Caller must hold mu.
func (s *Sink) rejectFor(clientID string, t *trackedOrder, snap Snapshot, now time.Time) (pendingReject, bool) {
	if t.State != StateRejected || t.rejectQueued {
		return pendingReject{}, false
	}
	t.rejectQueued = true
	reason := snap.Message
	if reason == "" {
		reason = snap.StatusText
	}
	return pendingReject{
		clientID: clientID,
		reject: engine.AsyncReject{
			OrderID:  clientID,
			Symbol:   t.Symbol,
			Side:     t.Side,
			PriceIDR: t.DecisionPriceIDR,
			Lots:     t.LotsRequested,
			Reason:   reason,
			RecvNs:   now.UnixNano(),
		},
	}, true
}

// marginalFillPriceIDR derives the price of the newest deltaLots-lot
// execution and reports whether it used the preferred method.
//
// Preferred: the change in Amount.MatchedTotal (cumulative matched IDR)
// divided by the shares just matched — the true marginal execution price
// even when earlier legs of the same order filled at a different price.
// Falls back to the reported VWAP (Price.Average.Price) when
// MatchedTotal's delta is not usable (stale/zero snapshot, or a
// non-positive result) — that fallback misprices a multi-leg fill, since it
// reports the whole order's blended average as if it were this leg's own
// price. Acceptable for B4, which has no multi-leg execution algo (that's
// B5); not acceptable for B6, which reads FillRecord.FillPriceIDR directly
// for realized-cost measurement — hence the Logf call at every call site
// that falls back.
func (s *Sink) marginalFillPriceIDR(t *trackedOrder, snap Snapshot, deltaLots int) (int64, bool) {
	deltaAmount := snap.Amount.MatchedTotal - t.MatchedAmountIDR
	deltaShares := float64(deltaLots) * sharesPerLot
	if deltaAmount > 0 && deltaShares > 0 {
		if px := deltaAmount / deltaShares; px > 0 {
			return int64(math.Round(px)), true
		}
	}
	return int64(math.Round(snap.Price.Average.Price)), false
}

// slippageBps mirrors engine.PaperSink.Submit's own slippage formula
// exactly, so a live fill and a paper fill are comparable on the same
// metric: unsigned (fill - decision) / decision, in basis points, and 0
// when decisionPriceIDR is non-positive rather than dividing by it.
func slippageBps(decisionPriceIDR, fillPriceIDR int64) float64 {
	if decisionPriceIDR <= 0 {
		return 0
	}
	return float64(fillPriceIDR-decisionPriceIDR) / float64(decisionPriceIDR) * 10000
}

// checkTimeouts reports a cancel to issue for t, if any. Caller must hold
// mu; the actual Cancel HTTP call happens later, unlocked (see pollOnce).
//
// A non-terminal order with no OPEN/terminal state after AckTimeout is
// logged once (ackAlerted latches so this does not repeat every tick) and
// left alone: it is not safe to blind-cancel an order that may simply be
// mid-flight to IDX — cancelling something Carina has not yet acknowledged
// risks a cancel-reject race the sink cannot resolve any better than
// waiting. An order that HAS reached OPEN or PARTIAL and stayed there past
// OpenTimeout is different — it is unambiguously resting and unfilled, and
// is cancelled once (cancelRequested latches the same way ackAlerted does,
// so a slow-to-resolve cancel is not resent every poll tick).
func (s *Sink) checkTimeouts(clientID string, t *trackedOrder, now time.Time) (pendingCancel, bool) {
	switch t.State {
	case StateNew, StateSending:
		if !t.ackAlerted && now.Sub(t.SubmittedAt) > s.ackTimeout {
			t.ackAlerted = true
			s.logf("live: %s: ALERT no OPEN/terminal state %s after submission (AckTimeout=%s)", clientID, now.Sub(t.SubmittedAt), s.ackTimeout)
		}
	case StateOpen, StatePartial:
		if !t.cancelRequested && !t.openSince.IsZero() && now.Sub(t.openSince) > s.openTimeout {
			t.cancelRequested = true
			s.logf("live: %s: still %s after OpenTimeout=%s since first opening, cancelling", clientID, t.State, s.openTimeout)
			return pendingCancel{clientID: clientID, brokerID: t.BrokerOrderID}, true
		}
	}
	return pendingCancel{}, false
}

// cancelForTimeout issues the actual, unlocked Cancel HTTP call for target.
// checkTimeouts already latched cancelRequested before this ran (so a
// slow-but-successful cancel is not resent every poll tick); if the call
// itself fails, that latch is cleared here so the NEXT poll tick's
// checkTimeouts sees this order as still needing a cancel and tries again,
// rather than leaving a resting, unfilled order past its OpenTimeout
// exposed to the market forever because one HTTP call happened to fail.
func (s *Sink) cancelForTimeout(ctx context.Context, target pendingCancel) {
	cancelCtx, cancel := context.WithTimeout(ctx, cancelHTTPTimeout)
	defer cancel()
	if err := s.client.Cancel(cancelCtx, target.brokerID, NewUIRef()); err != nil {
		s.logf("live: %s: OpenTimeout cancel failed: %v", target.clientID, err)
		s.mu.Lock()
		if t, ok := s.orders[target.clientID]; ok {
			t.cancelRequested = false
		}
		s.mu.Unlock()
	}
}
