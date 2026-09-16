package portfolio

import (
	"bytes"
	"math"
	"testing"
	"time"

	"collector/calendar"
)

func mustAsOf(t *testing.T) time.Time {
	t.Helper()
	asOf, err := time.Parse(time.RFC3339, "2026-09-13T09:00:00+07:00")
	if err != nil {
		t.Fatalf("parse fixture as_of: %v", err)
	}
	return asOf
}

// A generator that can emit something its own loader rejects is a latent
// outage: this round-trips NullPortfolio's output through Encode and Parse
// and asserts the result is both valid and unchanged.
func TestNullPortfolio_RoundTripsThroughEncodeAndParse(t *testing.T) {
	p, err := NullPortfolio(NullOptions{
		AsOf:            mustAsOf(t),
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}

	var buf bytes.Buffer
	if err := p.Encode(&buf); err != nil {
		t.Fatalf("Encode: %v", err)
	}

	got, err := Parse(&buf)
	if err != nil {
		t.Fatalf("round-tripped portfolio rejected by its own loader: %v", err)
	}

	if !got.AsOf.Equal(p.AsOf) {
		t.Errorf("AsOf: got %v want %v", got.AsOf, p.AsOf)
	}
	if !got.Constraints.CompleteBy.Equal(p.Constraints.CompleteBy) {
		t.Errorf("CompleteBy: got %v want %v", got.Constraints.CompleteBy, p.Constraints.CompleteBy)
	}
	if got.SchemaVersion != p.SchemaVersion ||
		got.StrategyID != p.StrategyID ||
		got.CapitalModel != p.CapitalModel ||
		got.BaseNotionalIDR != p.BaseNotionalIDR ||
		got.Constraints.MaxParticipationRate != p.Constraints.MaxParticipationRate ||
		got.Constraints.Board != p.Constraints.Board {
		t.Errorf("round trip changed a scalar field: got %+v want %+v", got, p)
	}
	if len(got.Targets) != len(p.Targets) {
		t.Fatalf("targets: got %d want %d", len(got.Targets), len(p.Targets))
	}
	for i := range p.Targets {
		if got.Targets[i] != p.Targets[i] {
			t.Errorf("target %d: got %+v want %+v", i, got.Targets[i], p.Targets[i])
		}
	}
}

func TestNullPortfolio_StampsCalibrationAndOwnedCash(t *testing.T) {
	p, err := NullPortfolio(NullOptions{
		AsOf:            mustAsOf(t),
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	if p.StrategyID != CalibrationStrategyID {
		t.Errorf("strategy id: got %q want %q", p.StrategyID, CalibrationStrategyID)
	}
	if p.CapitalModel != CapitalOwnedCash {
		t.Errorf("capital model: got %q want %q", p.CapitalModel, CapitalOwnedCash)
	}
	if p.Constraints.Board != BoardRG {
		t.Errorf("board: got %q want %q", p.Constraints.Board, BoardRG)
	}
}

// Equal weights: N names each get 1/N, and TotalWeight is within 1e-9 of
// 1.0. N=7 is the float-summation-hostile case (1/7 has no exact float64
// representation).
func TestNullPortfolio_EqualWeights(t *testing.T) {
	for _, n := range []int{3, 5, 7} {
		names := make([]string, n)
		for i := range names {
			names[i] = DefaultNullNames[i%len(DefaultNullNames)] + string(rune('A'+i))
		}
		p, err := NullPortfolio(NullOptions{
			AsOf:            mustAsOf(t),
			Names:           names,
			BaseNotionalIDR: 15_000_000,
		})
		if err != nil {
			t.Fatalf("N=%d: NullPortfolio: %v", n, err)
		}
		if len(p.Targets) != n {
			t.Fatalf("N=%d: got %d targets", n, len(p.Targets))
		}
		want := 1.0 / float64(n)
		for _, target := range p.Targets {
			if target.Weight != want {
				t.Errorf("N=%d: target %s weight: got %v want %v", n, target.Symbol, target.Weight, want)
			}
		}
		if got := p.TotalWeight(); math.Abs(got-1.0) > 1e-9 {
			t.Errorf("N=%d: total weight: got %v", n, got)
		}
	}
}

func TestNullPortfolio_DefaultsNamesParticipationAndCompleteBy(t *testing.T) {
	asOf := mustAsOf(t)
	p, err := NullPortfolio(NullOptions{
		AsOf:            asOf,
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	if got := p.Symbols(); len(got) != len(DefaultNullNames) {
		t.Fatalf("names: got %v want %v", got, DefaultNullNames)
	} else {
		for i, want := range DefaultNullNames {
			if got[i] != want {
				t.Errorf("names[%d]: got %q want %q", i, got[i], want)
			}
		}
	}
	if p.Constraints.MaxParticipationRate != defaultMaxParticipationRate {
		t.Errorf("max participation rate: got %v want %v", p.Constraints.MaxParticipationRate, defaultMaxParticipationRate)
	}
	wantCompleteBy := time.Date(2026, 9, 13, defaultCompleteByHourWIB, 0, 0, 0, calendar.Jakarta)
	if !p.Constraints.CompleteBy.Equal(wantCompleteBy) {
		t.Errorf("complete_by: got %v want %v", p.Constraints.CompleteBy, wantCompleteBy)
	}
	if zone, _ := p.Constraints.CompleteBy.Zone(); zone != "WIB" {
		t.Errorf("complete_by zone: got %q want WIB", zone)
	}
}

func TestNullPortfolio_RejectsZeroAsOf(t *testing.T) {
	_, err := NullPortfolio(NullOptions{BaseNotionalIDR: 15_000_000})
	if err == nil {
		t.Fatal("expected an error for zero AsOf")
	}
}

func TestNullPortfolio_RejectsNonPositiveBaseNotional(t *testing.T) {
	for _, v := range []int64{0, -1} {
		_, err := NullPortfolio(NullOptions{AsOf: mustAsOf(t), BaseNotionalIDR: v})
		if err == nil {
			t.Fatalf("base_notional_idr=%d: expected an error", v)
		}
	}
}

func TestFlattened_ZeroesWeightsKeepsSymbolsAndOrder(t *testing.T) {
	p, err := NullPortfolio(NullOptions{
		AsOf:            mustAsOf(t),
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	origSymbols := p.Symbols()
	origWeights := make([]float64, len(p.Targets))
	for i, target := range p.Targets {
		origWeights[i] = target.Weight
	}

	newAsOf := p.Constraints.CompleteBy
	newCompleteBy := newAsOf.Add(6 * time.Hour)
	flat := p.Flattened(newAsOf, newCompleteBy)

	if len(flat.Targets) != len(p.Targets) {
		t.Fatalf("flattened target count: got %d want %d", len(flat.Targets), len(p.Targets))
	}
	for i, target := range flat.Targets {
		if target.Symbol != origSymbols[i] {
			t.Errorf("flattened symbol %d: got %q want %q", i, target.Symbol, origSymbols[i])
		}
		if target.Weight != 0 {
			t.Errorf("flattened weight %d: got %v want 0", i, target.Weight)
		}
	}
	if !flat.AsOf.Equal(newAsOf) {
		t.Errorf("flattened AsOf: got %v want %v", flat.AsOf, newAsOf)
	}
	if !flat.Constraints.CompleteBy.Equal(newCompleteBy) {
		t.Errorf("flattened CompleteBy: got %v want %v", flat.Constraints.CompleteBy, newCompleteBy)
	}

	// The receiver must not be mutated.
	for i, target := range p.Targets {
		if target.Symbol != origSymbols[i] || target.Weight != origWeights[i] {
			t.Errorf("Flattened mutated the receiver at %d: got %+v", i, target)
		}
	}
}
