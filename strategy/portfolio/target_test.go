package portfolio

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// validDoc returns the plan 18 §3.1 example as a decoded map, so a test can
// mutate exactly one field and leave the rest valid. Building the fixture
// this way — rather than by string-replacing JSON — keeps each test's
// mutation unambiguous.
func validDoc() map[string]any {
	return map[string]any{
		"schema_version":    1,
		"as_of":             "2026-09-13T09:00:00+07:00",
		"strategy_id":       "calibration",
		"capital_model":     "owned_cash",
		"base_notional_idr": 15000000,
		"targets": []any{
			map[string]any{"symbol": "BBCA", "weight": 0.25},
			map[string]any{"symbol": "TLKM", "weight": 0.25},
		},
		"constraints": map[string]any{
			"max_participation_rate": 0.05,
			"complete_by":            "2026-09-13T15:00:00+07:00",
			"board":                  "RG",
		},
	}
}

func encode(t *testing.T, doc map[string]any) []byte {
	t.Helper()
	b, err := json.Marshal(doc)
	if err != nil {
		t.Fatalf("marshal fixture: %v", err)
	}
	return b
}

// parseDoc runs the fixture through the real decoder.
func parseDoc(t *testing.T, doc map[string]any) (TargetPortfolio, error) {
	t.Helper()
	return Parse(strings.NewReader(string(encode(t, doc))))
}

// mustReject asserts that doc fails validation, and that the message names
// the field at fault — a validation error nobody can act on is barely
// better than no validation.
func mustReject(t *testing.T, doc map[string]any, wantSubstr string) {
	t.Helper()
	_, err := parseDoc(t, doc)
	if err == nil {
		t.Fatalf("expected rejection mentioning %q, got nil error", wantSubstr)
	}
	if !errors.Is(err, ErrInvalidPortfolio) {
		t.Errorf("error should wrap ErrInvalidPortfolio, got %v", err)
	}
	if !strings.Contains(err.Error(), wantSubstr) {
		t.Errorf("error %q does not mention %q", err.Error(), wantSubstr)
	}
}

func TestParse_ValidPortfolio(t *testing.T) {
	got, err := parseDoc(t, validDoc())
	if err != nil {
		t.Fatalf("valid portfolio rejected: %v", err)
	}
	if got.SchemaVersion != SchemaVersion {
		t.Errorf("schema version: got %d want %d", got.SchemaVersion, SchemaVersion)
	}
	if got.StrategyID != CalibrationStrategyID {
		t.Errorf("strategy id: got %q", got.StrategyID)
	}
	if got.CapitalModel != CapitalOwnedCash {
		t.Errorf("capital model: got %q", got.CapitalModel)
	}
	if got.BaseNotionalIDR != 15000000 {
		t.Errorf("base notional: got %d", got.BaseNotionalIDR)
	}
	if len(got.Targets) != 2 {
		t.Fatalf("targets: got %d want 2", len(got.Targets))
	}
	if got.Targets[0].Symbol != "BBCA" || got.Targets[0].Weight != 0.25 {
		t.Errorf("target 0: got %+v", got.Targets[0])
	}
	if got.Constraints.Board != BoardRG {
		t.Errorf("board: got %q", got.Constraints.Board)
	}
	if got.Constraints.MaxParticipationRate != 0.05 {
		t.Errorf("participation: got %v", got.Constraints.MaxParticipationRate)
	}
}

// The offset is load-bearing: as_of drives the rebalance clock, and a time
// silently reinterpreted as UTC would move every decision 7 hours.
func TestParse_PreservesJakartaOffset(t *testing.T) {
	got, err := parseDoc(t, validDoc())
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	_, offset := got.AsOf.Zone()
	if offset != 7*60*60 {
		t.Errorf("as_of offset: got %ds want %ds", offset, 7*60*60)
	}
	if h := got.AsOf.Hour(); h != 9 {
		t.Errorf("as_of hour in its own zone: got %d want 9", h)
	}
}

// A missing schema_version decodes to 0. It must not be read as "version 1"
// — the same zero-value trap risk.TriState and engine.BoardType exist to
// avoid.
func TestParse_RejectsMissingSchemaVersion(t *testing.T) {
	doc := validDoc()
	delete(doc, "schema_version")
	mustReject(t, doc, "schema_version")
}

func TestParse_RejectsUnknownSchemaVersion(t *testing.T) {
	doc := validDoc()
	doc["schema_version"] = 2
	mustReject(t, doc, "schema_version")
}

// A missing base_notional_idr decodes to 0, which would silently size every
// order at zero lots and look exactly like "the strategy had no signal".
func TestParse_RejectsMissingBaseNotional(t *testing.T) {
	doc := validDoc()
	delete(doc, "base_notional_idr")
	mustReject(t, doc, "base_notional_idr")
}

func TestParse_RejectsNonPositiveBaseNotional(t *testing.T) {
	for _, v := range []int64{0, -1} {
		doc := validDoc()
		doc["base_notional_idr"] = v
		mustReject(t, doc, "base_notional_idr")
	}
}

// Long-only until short support exists (plan 18 §11).
func TestParse_RejectsNegativeWeight(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{map[string]any{"symbol": "BBCA", "weight": -0.25}}
	mustReject(t, doc, "weight")
}

func TestParse_RejectsWeightsSummingAboveOne(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{
		map[string]any{"symbol": "BBCA", "weight": 0.6},
		map[string]any{"symbol": "TLKM", "weight": 0.6},
	}
	mustReject(t, doc, "sum")
}

// Five equal weights of 0.2 is the null portfolio (§3.3). Floating-point
// summation must not make the canonical case fail its own schema.
func TestParse_AcceptsFiveEqualWeights(t *testing.T) {
	doc := validDoc()
	var targets []any
	for _, s := range []string{"TINS", "AMMN", "ENRG", "PTRO", "ANTM"} {
		targets = append(targets, map[string]any{"symbol": s, "weight": 0.2})
	}
	doc["targets"] = targets
	p, err := parseDoc(t, doc)
	if err != nil {
		t.Fatalf("five equal weights rejected: %v", err)
	}
	if got := p.TotalWeight(); math.Abs(got-1.0) > 1e-9 {
		t.Errorf("total weight: got %v", got)
	}
}

// Weight 0 is the exit leg: "I still track this name, and I want none of
// it". Rejecting it would make flattening the null portfolio inexpressible.
func TestParse_AcceptsZeroWeight(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{
		map[string]any{"symbol": "BBCA", "weight": 0.0},
		map[string]any{"symbol": "TLKM", "weight": 0.0},
	}
	if _, err := parseDoc(t, doc); err != nil {
		t.Fatalf("zero weight rejected: %v", err)
	}
}

// Two rows for one symbol have no defined meaning — sum? last wins? —
// so the file is rejected rather than silently resolved.
func TestParse_RejectsDuplicateSymbol(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{
		map[string]any{"symbol": "BBCA", "weight": 0.25},
		map[string]any{"symbol": "BBCA", "weight": 0.25},
	}
	mustReject(t, doc, "duplicate")
}

func TestParse_RejectsEmptyTargets(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{}
	mustReject(t, doc, "targets")
}

func TestParse_RejectsMissingWeight(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{map[string]any{"symbol": "BBCA"}}
	mustReject(t, doc, "weight")
}

func TestParse_RejectsBlankSymbol(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{map[string]any{"symbol": "  ", "weight": 0.25}}
	mustReject(t, doc, "symbol")
}

// An absent board decodes to "" and must not be read as RG, for the same
// reason engine.OrderIntent's zero BoardType is rejected by strategy/risk.
func TestParse_RejectsMissingBoard(t *testing.T) {
	doc := validDoc()
	c := doc["constraints"].(map[string]any)
	delete(c, "board")
	mustReject(t, doc, "board")
}

func TestParse_RejectsNonRGBoard(t *testing.T) {
	for _, b := range []string{"TN", "NG"} {
		doc := validDoc()
		doc["constraints"].(map[string]any)["board"] = b
		mustReject(t, doc, "board")
	}
}

// A participation rate of 0 blocks every slice, which fails closed — but it
// is indistinguishable from a forgotten field, and a blocked execution looks
// exactly like "no signal today". Require it.
func TestParse_RejectsMissingParticipationRate(t *testing.T) {
	doc := validDoc()
	c := doc["constraints"].(map[string]any)
	delete(c, "max_participation_rate")
	mustReject(t, doc, "max_participation_rate")
}

func TestParse_RejectsOutOfRangeParticipationRate(t *testing.T) {
	for _, v := range []float64{0, -0.1, 1.5} {
		doc := validDoc()
		doc["constraints"].(map[string]any)["max_participation_rate"] = v
		mustReject(t, doc, "max_participation_rate")
	}
}

func TestParse_RejectsCompleteByBeforeAsOf(t *testing.T) {
	doc := validDoc()
	doc["constraints"].(map[string]any)["complete_by"] = "2026-09-13T08:00:00+07:00"
	mustReject(t, doc, "complete_by")
}

func TestParse_RejectsMissingConstraints(t *testing.T) {
	doc := validDoc()
	delete(doc, "constraints")
	mustReject(t, doc, "constraints")
}

func TestParse_RejectsUnknownCapitalModel(t *testing.T) {
	doc := validDoc()
	doc["capital_model"] = "margin"
	mustReject(t, doc, "capital_model")
}

func TestParse_RejectsMissingStrategyID(t *testing.T) {
	doc := validDoc()
	delete(doc, "strategy_id")
	mustReject(t, doc, "strategy_id")
}

// The file is hand-written and hand-edited. A typo'd key that decoded
// silently would leave the intended constraint unapplied.
func TestParse_RejectsUnknownField(t *testing.T) {
	doc := validDoc()
	doc["base_notional"] = 15000000 // typo of base_notional_idr
	mustReject(t, doc, "base_notional")
}

func TestParse_RejectsUnknownTargetField(t *testing.T) {
	doc := validDoc()
	doc["targets"] = []any{map[string]any{"symbol": "BBCA", "weigth": 0.25}}
	mustReject(t, doc, "weigth")
}

func TestParse_RejectsMalformedAsOf(t *testing.T) {
	doc := validDoc()
	doc["as_of"] = "2026-09-13 09:00:00"
	mustReject(t, doc, "as_of")
}

func TestLoad_ReadsFromDisk(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "target_portfolio.json")
	if err := os.WriteFile(path, encode(t, validDoc()), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	got, err := Load(path)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(got.Targets) != 2 {
		t.Errorf("targets: got %d", len(got.Targets))
	}
}

func TestLoad_MissingFileIsAnError(t *testing.T) {
	if _, err := Load(filepath.Join(t.TempDir(), "absent.json")); err == nil {
		t.Fatal("expected an error for a missing file")
	}
}

func TestTargetNotionalIDR(t *testing.T) {
	p, err := parseDoc(t, validDoc())
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if got, ok := p.TargetNotionalIDR("BBCA"); !ok || got != 3750000 {
		t.Errorf("BBCA notional: got %d ok=%v want 3750000", got, ok)
	}
	if _, ok := p.TargetNotionalIDR("ZZZZ"); ok {
		t.Error("an untargeted symbol must report ok=false, not a zero notional")
	}
}

func TestSymbols_IsDeterministic(t *testing.T) {
	p, err := parseDoc(t, validDoc())
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	first := p.Symbols()
	for i := 0; i < 20; i++ {
		got := p.Symbols()
		for j := range got {
			if got[j] != first[j] {
				t.Fatalf("Symbols() is not deterministic: %v vs %v", got, first)
			}
		}
	}
	if first[0] != "BBCA" || first[1] != "TLKM" {
		t.Errorf("Symbols() should preserve file order: got %v", first)
	}
}

// Symbols() hands out a slice; a caller mutating it must not corrupt the
// portfolio it came from.
func TestSymbols_CallerCannotMutatePortfolio(t *testing.T) {
	p, err := parseDoc(t, validDoc())
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	s := p.Symbols()
	s[0] = "MUTATED"
	if again := p.Symbols(); again[0] != "BBCA" {
		t.Errorf("caller mutated the portfolio through Symbols(): got %v", again[0])
	}
}

var _ = time.Time{}
