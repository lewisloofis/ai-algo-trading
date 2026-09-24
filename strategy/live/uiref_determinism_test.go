package live

import (
	"strings"
	"testing"
)

// TestDeterministicUIRef_SameInputsSameOutput is the core restart-durability
// property (plan 18 §5 B4 gap): the same client order id on the same
// trading day must always mint the same ui_ref, whether that happens twice
// in one process or once each in two processes either side of a crash.
func TestDeterministicUIRef_SameInputsSameOutput(t *testing.T) {
	a := DeterministicUIRef("rebalance-TINS-2026-09-24", "2026-09-24")
	b := DeterministicUIRef("rebalance-TINS-2026-09-24", "2026-09-24")
	if a != b {
		t.Fatalf("DeterministicUIRef is not deterministic: %q != %q", a, b)
	}
}

// TestDeterministicUIRef_DifferentDaySameOrderIDDiffers guards the failure
// uiref.go's NewUIRef doc comment already describes for time-based ui_refs:
// a client order id that repeats on a LATER day (e.g. strategies/orb's
// "orb-BBCA-1", which is not itself day-scoped) must not collide with
// yesterday's ui_ref, or Carina's idempotency guard would reject a
// genuinely new order as a duplicate of one from a prior session.
func TestDeterministicUIRef_DifferentDaySameOrderIDDiffers(t *testing.T) {
	a := DeterministicUIRef("orb-BBCA-1", "2026-09-24")
	b := DeterministicUIRef("orb-BBCA-1", "2026-09-25")
	if a == b {
		t.Fatalf("DeterministicUIRef(same order id, different day) collided: %q", a)
	}
}

// TestDeterministicUIRef_DifferentOrderIDSameDayDiffers guards the other
// axis: two distinct orders placed the same day must not collide either.
func TestDeterministicUIRef_DifferentOrderIDSameDayDiffers(t *testing.T) {
	a := DeterministicUIRef("rebalance-TINS-2026-09-24", "2026-09-24")
	b := DeterministicUIRef("rebalance-AMMN-2026-09-24", "2026-09-24")
	if a == b {
		t.Fatalf("DeterministicUIRef(different order ids, same day) collided: %q", a)
	}
}

// TestDeterministicUIRef_ShapeAndLength mirrors TestNewUIRef_ShapeAndLength:
// the result must still be a valid ui_ref by Carina's own constraints
// (docs/reference/04-order-lifecycle.md §5.3).
func TestDeterministicUIRef_ShapeAndLength(t *testing.T) {
	ref := DeterministicUIRef("rebalance-TINS-2026-09-24", "2026-09-24")
	if len(ref) > 40 {
		t.Errorf("DeterministicUIRef() = %q, len %d exceeds the 40-char cap", ref, len(ref))
	}
	if len(ref) < 10 {
		t.Errorf("DeterministicUIRef() = %q, suspiciously short (len %d)", ref, len(ref))
	}
	if strings.ContainsAny(ref, "|") {
		t.Errorf("DeterministicUIRef() = %q contains a separator character", ref)
	}
}
