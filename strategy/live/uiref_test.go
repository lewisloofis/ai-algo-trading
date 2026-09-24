package live

import (
	"strings"
	"testing"
)

func TestNewUIRef_ShapeAndLength(t *testing.T) {
	ref := NewUIRef()
	if !strings.HasPrefix(ref, "A") {
		t.Errorf("NewUIRef() = %q, want prefix \"A\"", ref)
	}
	if len(ref) > 40 {
		t.Errorf("NewUIRef() = %q, len %d exceeds the 40-char cap (docs/reference/04-order-lifecycle.md §5.3)", ref, len(ref))
	}
	if len(ref) < 10 {
		t.Errorf("NewUIRef() = %q, suspiciously short (len %d)", ref, len(ref))
	}
}

func TestNewUIRef_ConsecutiveCallsDiffer(t *testing.T) {
	seen := make(map[string]bool)
	for i := 0; i < 100; i++ {
		ref := NewUIRef()
		if seen[ref] {
			t.Fatalf("NewUIRef() produced a repeat: %q", ref)
		}
		seen[ref] = true
	}
}
