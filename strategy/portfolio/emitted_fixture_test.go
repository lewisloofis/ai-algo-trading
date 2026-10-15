package portfolio

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Cross-language check: the golden target files committed by the Python daily
// target emitter (research/strategy_fit/target_emitter.py) must load through
// this package's strict Parse. The Python test
// research/strategy_fit/test_target_emitter_job.py regenerates them and fails
// on drift, so the two sides cannot diverge silently. The *.signal.json
// sidecars are NOT part of the contract and must be rejected by Parse — that
// is exactly why the explanatory fields live outside the target file.

const emittedFixtureDir = "../../research/strategy_fit/fixtures/target_emitter"

// emitterRebalanceAt is the --rebalance-at the fixtures were generated for
// (the emitter's default); the window must cover it.
const emitterRebalanceHour, emitterRebalanceMin = 9, 30

var emittedFixtures = []string{"emitted_multi_symbol", "emitted_single_symbol"}

type emittedSidecar struct {
	LotsPerName int `json:"lots_per_name"`
	Symbols     []struct {
		Symbol    string  `json:"symbol"`
		Weight    float64 `json:"weight"`
		LastClose float64 `json:"last_close"`
	} `json:"symbols"`
}

func loadSidecar(t *testing.T, name string) emittedSidecar {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(emittedFixtureDir, name+".signal.json"))
	if err != nil {
		t.Fatalf("read sidecar: %v", err)
	}
	var s emittedSidecar
	if err := json.Unmarshal(b, &s); err != nil {
		t.Fatalf("decode sidecar: %v", err)
	}
	return s
}

func TestEmittedFixtures_ParseWithStrictLoader(t *testing.T) {
	for _, name := range emittedFixtures {
		t.Run(name, func(t *testing.T) {
			p, err := Load(filepath.Join(emittedFixtureDir, name+".json"))
			if err != nil {
				t.Fatalf("emitter output rejected by Parse: %v", err)
			}
			if p.CapitalModel != CapitalOwnedCash || p.Constraints.Board != BoardRG {
				t.Errorf("unexpected capital model/board: %v %v", p.CapitalModel, p.Constraints.Board)
			}
			if p.TotalWeight() > 1.0+weightSumEpsilon {
				t.Errorf("weights sum to %v", p.TotalWeight())
			}
		})
	}
}

func TestEmittedFixtures_WindowCoversTheRebalanceTime(t *testing.T) {
	for _, name := range emittedFixtures {
		t.Run(name, func(t *testing.T) {
			p, err := Load(filepath.Join(emittedFixtureDir, name+".json"))
			if err != nil {
				t.Fatal(err)
			}
			at := time.Date(p.AsOf.Year(), p.AsOf.Month(), p.AsOf.Day(),
				emitterRebalanceHour, emitterRebalanceMin, 0, 0, p.AsOf.Location())
			if at.Before(p.AsOf) || !at.Before(p.Constraints.CompleteBy) {
				t.Errorf("rebalance time %s outside [%s, %s)", at, p.AsOf, p.Constraints.CompleteBy)
			}
			// The strategy-run default (15:00) is after complete_by: it must not fit.
			def := time.Date(at.Year(), at.Month(), at.Day(), 15, 0, 0, 0, at.Location())
			if def.Before(p.Constraints.CompleteBy) {
				t.Errorf("window %s still covers the 15:00 default", p.Constraints.CompleteBy)
			}
		})
	}
}

// Each active name's target notional must cover its own exact-lots need and
// stay below one more lot's worth at the signal close: weight*base is what the
// rebalance strategy floors into lots.
func TestEmittedFixtures_ActiveNamesSizeToTheSpecLots(t *testing.T) {
	for _, name := range emittedFixtures {
		t.Run(name, func(t *testing.T) {
			p, err := Load(filepath.Join(emittedFixtureDir, name+".json"))
			if err != nil {
				t.Fatal(err)
			}
			side := loadSidecar(t, name)
			if len(side.Symbols) != len(p.Targets) {
				t.Fatalf("sidecar has %d symbols, target %d", len(side.Symbols), len(p.Targets))
			}
			for i, s := range side.Symbols {
				if p.Targets[i].Symbol != s.Symbol || p.Targets[i].Weight != s.Weight {
					t.Fatalf("target[%d] %+v != sidecar %+v", i, p.Targets[i], s)
				}
				notional, ok := p.TargetNotionalIDR(s.Symbol)
				if !ok {
					t.Fatalf("%s missing from targets", s.Symbol)
				}
				if s.Weight == 0 {
					if notional != 0 {
						t.Errorf("%s flat but notional %d", s.Symbol, notional)
					}
					continue
				}
				need := math.Ceil(1.3 * s.LastClose * 100 * float64(side.LotsPerName))
				if float64(notional) < need {
					t.Errorf("%s notional %d < exact-lots need %.0f", s.Symbol, notional, need)
				}
				oneMore := s.LastClose * 100 * float64(side.LotsPerName+1)
				if float64(notional) >= oneMore*1.3 {
					t.Errorf("%s notional %d would fund an extra lot (%.0f)", s.Symbol, notional, oneMore)
				}
			}
		})
	}
}

func TestEmittedFixtures_SidecarIsNotPartOfTheContract(t *testing.T) {
	for _, name := range emittedFixtures {
		_, err := Load(filepath.Join(emittedFixtureDir, name+".signal.json"))
		if !errors.Is(err, ErrInvalidPortfolio) {
			t.Errorf("%s sidecar must be rejected (unknown fields), got %v", name, err)
		}
	}
}
