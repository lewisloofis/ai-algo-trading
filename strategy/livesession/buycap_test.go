package livesession

import (
	"strconv"
	"testing"
)

func TestChildArgs_ForwardsTheBuyCapOnlyWhenSet(t *testing.T) {
	s := &Session{cfg: Config{Sink: "paper", Source: "replay", Strategy: "orb", MaxBuyNotionalIDR: 750_000}.withDefaults()}
	args := s.childArgs("runs/x.db", []string{"g"}, PreflightResult{})
	if got := argValue(args, "--max-buy-notional-idr"); got != strconv.Itoa(750_000) {
		t.Fatalf("--max-buy-notional-idr = %q, args %v", got, args)
	}
	s.cfg.MaxBuyNotionalIDR = 0
	if hasArg(s.childArgs("runs/x.db", []string{"g"}, PreflightResult{}), "--max-buy-notional-idr") {
		t.Fatal("flag forwarded with the cap off")
	}
}

func TestPreflight_RefusesANegativeBuyCap(t *testing.T) {
	e := newEnv(t)
	e.cfg.MaxBuyNotionalIDR = -1
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want refused", code)
	}
	if e.child.count() != 0 {
		t.Fatal("child started with a negative cap")
	}
}
