package live

import (
	"errors"
	"testing"
)

func TestTransition_LegalPaths(t *testing.T) {
	tests := []struct {
		from, to State
	}{
		{StateNew, StateSending},
		{StateSending, StateOpen},
		{StateSending, StatePartial},
		{StateSending, StateFilled},
		{StateSending, StateRejected},
		{StateOpen, StatePartial},
		{StateOpen, StateFilled},
		{StateOpen, StateCancelled},
		{StatePartial, StatePartial},
		{StatePartial, StateFilled},
		{StatePartial, StateCancelled},
		// Re-observation of the same state is always legal, even for a
		// terminal state.
		{StateFilled, StateFilled},
		{StateCancelled, StateCancelled},
	}
	for _, tt := range tests {
		t.Run(tt.from.String()+"->"+tt.to.String(), func(t *testing.T) {
			got, err := Transition(tt.from, tt.to)
			if err != nil {
				t.Fatalf("Transition(%s, %s) error = %v, want nil", tt.from, tt.to, err)
			}
			if got != tt.to {
				t.Errorf("Transition(%s, %s) = %s, want %s", tt.from, tt.to, got, tt.to)
			}
		})
	}
}

// TestTransition_FilledNeverGoesBackToPartial is the IDX quirk from
// docs/reference/04-order-lifecycle.md §3.3: a stray PARTIAL event arriving
// after the full match must not un-fill the order.
func TestTransition_FilledNeverGoesBackToPartial(t *testing.T) {
	got, err := Transition(StateFilled, StatePartial)
	if err == nil {
		t.Fatal("Transition(Filled, Partial): got nil error, want ErrIllegalTransition")
	}
	if !errors.Is(err, ErrIllegalTransition) {
		t.Errorf("Transition(Filled, Partial) error = %v, want errors.Is(_, ErrIllegalTransition)", err)
	}
	if got != StateFilled {
		t.Errorf("Transition(Filled, Partial) returned state = %s, want unchanged StateFilled", got)
	}
}

func TestTransition_IllegalPaths(t *testing.T) {
	tests := []struct {
		from, to State
	}{
		{StateNew, StateOpen},       // must go through Sending
		{StateNew, StateFilled},     // must go through Sending
		{StateOpen, StateSending},   // no going backwards
		{StateCancelled, StateOpen}, // terminal states have no outbound transition
		{StateRejected, StateOpen},
		{StateReplaced, StatePartial},
	}
	for _, tt := range tests {
		t.Run(tt.from.String()+"->"+tt.to.String(), func(t *testing.T) {
			got, err := Transition(tt.from, tt.to)
			if !errors.Is(err, ErrIllegalTransition) {
				t.Fatalf("Transition(%s, %s) error = %v, want ErrIllegalTransition", tt.from, tt.to, err)
			}
			if got != tt.from {
				t.Errorf("Transition(%s, %s) returned state = %s, want unchanged %s", tt.from, tt.to, got, tt.from)
			}
		})
	}
}

func TestIsTerminal(t *testing.T) {
	terminal := map[State]bool{
		StateNew: false, StateSending: false, StateOpen: false, StatePartial: false,
		StateFilled: true, StateCancelled: true, StateRejected: true, StateReplaced: true,
	}
	for s, want := range terminal {
		if got := IsTerminal(s); got != want {
			t.Errorf("IsTerminal(%s) = %v, want %v", s, got, want)
		}
	}
}

func TestStatusToState(t *testing.T) {
	tests := []struct {
		text string
		want State
		ok   bool
	}{
		{"OPEN", StateOpen, true},
		{"PARTIAL", StatePartial, true},
		{"MATCH", StateFilled, true},
		{"WITHDRAWN", StateCancelled, true},
		{"REJECTED", StateRejected, true},
		{"AMENDED", StateReplaced, true},
		{"READY", StateNew, false}, // smart-order-only label; unrecognised here
		{"", StateNew, false},
	}
	for _, tt := range tests {
		got, ok := statusToState(tt.text)
		if ok != tt.ok {
			t.Errorf("statusToState(%q) ok = %v, want %v", tt.text, ok, tt.ok)
			continue
		}
		if ok && got != tt.want {
			t.Errorf("statusToState(%q) = %s, want %s", tt.text, got, tt.want)
		}
	}
}
