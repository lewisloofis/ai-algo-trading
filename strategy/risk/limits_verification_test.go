package risk

import (
	"context"
	"errors"
	"testing"

	"strategy/engine"
)

// This file is an independent verification pass for plan 18 §10's box:
//
//	"B2 limits provably reject: an over-cap order, an over-exposure basket,
//	 and a post-loss-limit order — each with a test."
//
// sink_test.go already exercises each of these gates individually as part of
// B2's own development (TestSubmit_PerOrderNotionalCap_BoundaryAndOverLimit,
// TestSubmit_MaxGrossExposure_MultiSymbolAndPartialReduction,
// TestSubmit_DailyLossLimit_BoundaryThenBreachTripsKillSwitch). The three
// tests below are deliberately separate: they exist to tick the §10 box on
// their own, read as a self-contained proof of the three named scenarios,
// and were each confirmed non-vacuous by mutation — see the note at the
// bottom of this file for what was mutated and reverted, and the report this
// task returns for the observed RED/GREEN result of each.

// TestVerification_OverCapOrder_Rejects proves an order whose own notional
// exceeds the per-order cap is rejected and never reaches the inner sink.
func TestVerification_OverCapOrder_Rejects(t *testing.T) {
	inner := &recordingSink{}
	limits := permissiveLimits()
	limits.PerOrderNotionalFraction = 0.1 // cap = 1,500,000 IDR on a 15M sleeve

	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	// 2 lots @ 10,000 IDR * 100 shares/lot = 2,000,000 IDR — over the
	// 1,500,000 cap by a comfortable margin, not a boundary case (the
	// boundary is already covered by sink_test.go).
	over := intent("BBCA", "buy", 10_000, 2)

	_, err = sink.Submit(context.Background(), over)
	if err == nil {
		t.Fatal("expected the over-cap order to be rejected, got nil error")
	}
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded, got %v", err)
	}
	if inner.count() != 0 {
		t.Fatalf("over-cap order reached the inner sink: count=%d", inner.count())
	}
}

// TestVerification_OverExposureBasket_Rejects proves a basket of orders that,
// taken together, would push gross exposure over the cap is rejected on the
// order that tips it over — even though every individual order in the
// basket is well within the per-order cap on its own. The first two orders
// admit; the third, which only breaches the cap in combination with the
// first two, is rejected and never reaches the inner sink.
func TestVerification_OverExposureBasket_Rejects(t *testing.T) {
	inner := &recordingSink{}
	limits := permissiveLimits()
	limits.MaxGrossExposureFraction = 0.3 // cap = 4,500,000 IDR on a 15M sleeve
	// Per-order cap left permissive (1x equity) so it cannot be the gate
	// that fires here — this test isolates the basket/gross-exposure gate.

	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	basket := []engine.OrderIntent{
		intent("BBCA", "buy", 10_000, 2), // 2,000,000 IDR — gross so far 2,000,000
		intent("TLKM", "buy", 10_000, 2), // 2,000,000 IDR — gross so far 4,000,000
		intent("BMRI", "buy", 10_000, 1), // 1,000,000 IDR — would push gross to 5,000,000 > 4,500,000
	}

	for i, in := range basket[:2] {
		if _, err := sink.Submit(context.Background(), in); err != nil {
			t.Fatalf("basket leg %d (%s) expected to admit, got %v", i, in.Symbol, err)
		}
	}
	if inner.count() != 2 {
		t.Fatalf("expected the first two basket legs to reach inner sink, count=%d", inner.count())
	}

	_, err = sink.Submit(context.Background(), basket[2])
	if err == nil {
		t.Fatal("expected the basket-completing order to be rejected, got nil error")
	}
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded on the over-exposure basket, got %v", err)
	}
	if inner.count() != 2 {
		t.Fatalf("the basket-completing order reached the inner sink: count=%d, want 2", inner.count())
	}
}

// TestVerification_PostLossLimitOrder_Rejects proves that once the day's
// realized+unrealized PnL is already beyond the configured daily loss
// limit, a fresh, otherwise entirely valid order submitted afterward is
// rejected — both directly (checkDailyLoss fires again on every Submit,
// independent of the kill switch) and via the kill switch the breach trips.
// Neither the breaching order nor the order submitted after it reaches the
// inner sink.
func TestVerification_PostLossLimitOrder_Rejects(t *testing.T) {
	inner := &recordingSink{}
	limits := permissiveLimits()
	limits.DailyLossLimitFraction = 0.1 // cap = 1,500,000 IDR on a 15M sleeve
	pnl := &mutablePnL{pnl: -2_000_000} // already beyond the limit before any order is submitted

	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), limits, allowGate{}, pnl, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	// The order submitted while already beyond the limit: rejected by
	// checkDailyLoss itself, and it trips the kill switch (RiskSink.Submit
	// -> checkDailyLoss -> a confirmed breach trips in-process).
	first := intent("BBCA", "buy", 10_000, 1)
	_, err = sink.Submit(context.Background(), first)
	if !errors.Is(err, ErrDailyLossLimitBreached) {
		t.Fatalf("expected ErrDailyLossLimitBreached for the post-loss-limit order, got %v", err)
	}
	if tripped, _ := sink.KillSwitchTripped(); !tripped {
		t.Fatal("expected a confirmed daily-loss breach to trip the kill switch")
	}

	// A second, independent order submitted afterward must also be
	// rejected — now via the tripped kill switch, which is checked before
	// any other gate in Submit.
	second := intent("TLKM", "buy", 10_000, 1)
	_, err = sink.Submit(context.Background(), second)
	if !errors.Is(err, ErrKillSwitchTripped) {
		t.Fatalf("expected ErrKillSwitchTripped for the order after the breach, got %v", err)
	}

	if inner.count() != 0 {
		t.Fatalf("no order should reach the inner sink once the daily loss limit is breached, count=%d", inner.count())
	}
}

// --- Non-vacuity ------------------------------------------------------
//
// Each test above was confirmed non-vacuous by hand, once, by temporarily
// mutating the corresponding gate in sink.go to always admit, re-running
// the single test with `go test -run <name> ./risk/...`, observing it FAIL,
// and then reverting with `git checkout -- strategy/risk/sink.go` (sink.go
// was clean/tracked at HEAD at the time, so the revert is exact and
// verified with `git diff --stat` showing no residual change). This is not
// left as a permanent code path — Go has no supported "always fail" build
// tag for a single boolean condition — so it is recorded here instead:
//
//   - TestVerification_OverCapOrder_Rejects: changed
//     `if notional > r.caps.perOrderNotionalIDR {` to `if false {` in
//     RiskSink.Submit. Result: FAIL (order admitted, inner.count()=1, no
//     error). Reverted; re-ran and confirmed PASS.
//   - TestVerification_OverExposureBasket_Rejects: changed
//     `if projectedGross > r.caps.maxGrossExposureIDR {` to `if false {` in
//     RiskSink.Submit. Result: FAIL (all three basket legs admitted,
//     inner.count()=3). Reverted; re-ran and confirmed PASS.
//   - TestVerification_PostLossLimitOrder_Rejects: changed
//     `if pnl < -r.caps.dailyLossLimitIDR {` to `if false {` in
//     RiskSink.checkDailyLoss. Result: FAIL (both orders admitted,
//     inner.count()=2, kill switch never tripped). Reverted; re-ran and
//     confirmed PASS.
