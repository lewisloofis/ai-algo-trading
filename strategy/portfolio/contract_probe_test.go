package portfolio

import (
	"bytes"
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"collector/calendar"
)

// PROBE 1: DisallowUnknownFields is asserted at the top level and inside a
// target element, but nothing covered the constraints object — and
// constraints is where a silently-dropped key does the most damage (a
// mistyped max_participation_rate would leave execution uncapped).
func TestProbe_RejectsUnknownConstraintField(t *testing.T) {
	doc := validDoc()
	doc["constraints"].(map[string]any)["max_participation"] = 0.05 // typo
	if _, err := parseDoc(t, doc); err == nil {
		t.Fatal("an unknown key inside constraints was silently accepted")
	}
}

// PROBE 2: Encode must not normalise times to UTC. A round trip that
// preserves the instant but loses the +07:00 offset would still parse, and
// every downstream WIB-local decision would silently shift.
func TestProbe_EncodePreservesJakartaOffset(t *testing.T) {
	asOf := time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta)
	p, err := NullPortfolio(NullOptions{AsOf: asOf, BaseNotionalIDR: 15_000_000})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}

	var buf bytes.Buffer
	if err := p.Encode(&buf); err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if !strings.Contains(buf.String(), "+07:00") {
		t.Errorf("encoded portfolio lost the WIB offset:\n%s", buf.String())
	}

	back, err := Parse(bytes.NewReader(buf.Bytes()))
	if err != nil {
		t.Fatalf("round trip failed to parse: %v", err)
	}
	if _, off := back.AsOf.Zone(); off != 7*60*60 {
		t.Errorf("as_of offset after round trip: got %ds want %ds", off, 7*60*60)
	}
	if _, off := back.Constraints.CompleteBy.Zone(); off != 7*60*60 {
		t.Errorf("complete_by offset after round trip: got %ds want %ds", off, 7*60*60)
	}
	if !back.AsOf.Equal(p.AsOf) {
		t.Errorf("as_of instant moved: %v -> %v", p.AsOf, back.AsOf)
	}
}

// PROBE 3: the wire format must match plan 18 §3.1 key-for-key. A loader
// and a generator that agree with each other but not with the documented
// contract would pass every round-trip test and still be unreadable by
// Track A's Python side (research/strategy_fit/portfolio_json.py).
func TestProbe_WireFormatMatchesContract(t *testing.T) {
	p, err := NullPortfolio(NullOptions{
		AsOf:            time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta),
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	var buf bytes.Buffer
	if err := p.Encode(&buf); err != nil {
		t.Fatalf("Encode: %v", err)
	}

	var got map[string]any
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatalf("encoded output is not valid JSON: %v", err)
	}
	for _, k := range []string{"schema_version", "as_of", "strategy_id", "capital_model", "base_notional_idr", "targets", "constraints"} {
		if _, ok := got[k]; !ok {
			t.Errorf("encoded output is missing contract key %q", k)
		}
	}
	if len(got) != 7 {
		t.Errorf("encoded output has %d top-level keys, want exactly the 7 in the contract: %v", len(got), got)
	}
	c, ok := got["constraints"].(map[string]any)
	if !ok {
		t.Fatalf("constraints is not an object: %T", got["constraints"])
	}
	for _, k := range []string{"max_participation_rate", "complete_by", "board"} {
		if _, ok := c[k]; !ok {
			t.Errorf("constraints is missing contract key %q", k)
		}
	}
	tg, ok := got["targets"].([]any)
	if !ok || len(tg) == 0 {
		t.Fatalf("targets is not a non-empty array: %T", got["targets"])
	}
	t0 := tg[0].(map[string]any)
	if _, ok := t0["symbol"]; !ok {
		t.Error("target element is missing \"symbol\"")
	}
	if _, ok := t0["weight"]; !ok {
		t.Error("target element is missing \"weight\"")
	}
}

// PROBE 4: is the sum epsilon actually load-bearing, or does
// TestParse_AcceptsFiveEqualWeights pass vacuously? If no equal-weight N in
// the plausible range ever sums above 1.0 in float64, the tolerance is
// untested in practice and a future refactor could drop it unnoticed. This
// records which N are genuinely over 1.0 so the answer is on the record
// rather than assumed.
func TestProbe_EqualWeightFloatSummation(t *testing.T) {
	over := []int{}
	for n := 2; n <= 20; n++ {
		w := 1.0 / float64(n)
		sum := 0.0
		for i := 0; i < n; i++ {
			sum += w
		}
		if sum > 1.0 {
			over = append(over, n)
		}
		if math.Abs(sum-1.0) > 1e-9 {
			t.Errorf("N=%d: equal weights sum to %.20f, outside the 1e-9 tolerance the loader allows", n, sum)
		}
	}
	t.Logf("equal-weight N whose float64 sum strictly exceeds 1.0: %v", over)
	if len(over) == 0 {
		t.Log("NOTE: no N in 2..20 sums above 1.0 — the epsilon is defensive, not currently exercised by any equal-weight case")
	}
}

// PROBE 5: the agent's design rests on the claim that every absent optional
// field decodes to a zero value that is ALREADY invalid, so one validate()
// catches both "missing" and "explicitly wrong". That is a reasoning claim
// about safety, so check it exhaustively rather than on the sampled fields
// the spec happened to name: delete each contract key in turn and require a
// rejection. A key that survives deletion is a field with a silent default.
func TestProbe_EveryContractKeyIsRequired(t *testing.T) {
	topLevel := []string{"schema_version", "as_of", "strategy_id", "capital_model", "base_notional_idr", "targets", "constraints"}
	for _, k := range topLevel {
		doc := validDoc()
		delete(doc, k)
		if _, err := parseDoc(t, doc); err == nil {
			t.Errorf("top-level key %q is optional — its absence was accepted", k)
		}
	}
	for _, k := range []string{"max_participation_rate", "complete_by", "board"} {
		doc := validDoc()
		delete(doc["constraints"].(map[string]any), k)
		if _, err := parseDoc(t, doc); err == nil {
			t.Errorf("constraints key %q is optional — its absence was accepted", k)
		}
	}
	for _, k := range []string{"symbol", "weight"} {
		doc := validDoc()
		tgt := map[string]any{"symbol": "BBCA", "weight": 0.25}
		delete(tgt, k)
		doc["targets"] = []any{tgt}
		if _, err := parseDoc(t, doc); err == nil {
			t.Errorf("target key %q is optional — its absence was accepted", k)
		}
	}
}

// PROBE 6: null portfolios are generated per rebalance and must be
// byte-identical for identical inputs, or a diff of two days' files is
// unreadable noise.
func TestProbe_NullPortfolioIsDeterministic(t *testing.T) {
	opts := NullOptions{
		AsOf:            time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta),
		BaseNotionalIDR: 15_000_000,
	}
	var first string
	for i := 0; i < 10; i++ {
		p, err := NullPortfolio(opts)
		if err != nil {
			t.Fatalf("NullPortfolio: %v", err)
		}
		var buf bytes.Buffer
		if err := p.Encode(&buf); err != nil {
			t.Fatalf("Encode: %v", err)
		}
		if i == 0 {
			first = buf.String()
			continue
		}
		if buf.String() != first {
			t.Fatalf("NullPortfolio is not deterministic:\n%s\nvs\n%s", first, buf.String())
		}
	}
}

// PROBE 7: Flattened is the exit leg of every calibration round trip. It
// must zero the weights without disturbing the name set or its order, and
// without mutating the portfolio it came from.
func TestProbe_FlattenedIsAnImmutableZeroingOfWeights(t *testing.T) {
	asOf := time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta)
	p, err := NullPortfolio(NullOptions{AsOf: asOf, BaseNotionalIDR: 15_000_000})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	before := p.Symbols()
	beforeWeights := make([]float64, len(p.Targets))
	for i, tg := range p.Targets {
		beforeWeights[i] = tg.Weight
	}

	flat := p.Flattened(asOf.Add(3*time.Hour), asOf.Add(6*time.Hour))

	for i, tg := range flat.Targets {
		if tg.Weight != 0 {
			t.Errorf("flattened target %s has weight %v, want 0", tg.Symbol, tg.Weight)
		}
		if tg.Symbol != before[i] {
			t.Errorf("flattened name order changed at %d: %s vs %s", i, tg.Symbol, before[i])
		}
	}
	for i, tg := range p.Targets {
		if tg.Weight != beforeWeights[i] {
			t.Fatalf("Flattened mutated its receiver: target %d weight %v -> %v", i, beforeWeights[i], tg.Weight)
		}
	}
	// The exit leg must itself be a valid portfolio, or it cannot be written
	// out and replayed like any other rebalance.
	var buf bytes.Buffer
	if err := flat.Encode(&buf); err != nil {
		t.Fatalf("Encode flattened: %v", err)
	}
	if _, err := Parse(bytes.NewReader(buf.Bytes())); err != nil {
		t.Fatalf("the flattened exit leg does not survive its own loader: %v", err)
	}
}
