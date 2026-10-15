package rebalance

import (
	"context"
	"errors"
	"testing"
)

// TestOrderID_RetrySuffix pins the --retry id contract: 0 is byte-for-byte
// the historical id; N >= 2 appends "-r<N>"; the same N is restart-stable.
func TestOrderID_RetrySuffix(t *testing.T) {
	cases := []struct {
		name  string
		retry int
		want  string
	}{
		{"zero is unchanged", 0, "rebalance-AAA-2026-09-16"},
		{"retry 2", 2, "rebalance-AAA-2026-09-16-r2"},
		{"retry 3", 3, "rebalance-AAA-2026-09-16-r3"},
		{"retry 12", 12, "rebalance-AAA-2026-09-16-r12"},
	}
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			for i := 0; i < 2; i++ { // second pass = simulated restart
				strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0), Retry: c.retry})
				if err != nil {
					t.Fatalf("New: %v", err)
				}
				fe := &fakeEngine{now: wibTime(9, 5)}
				strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
				if len(fe.calls) != 1 {
					t.Fatalf("SubmitOrder called %d times, want 1", len(fe.calls))
				}
				if got := fe.calls[0].OrderID; got != c.want {
					t.Fatalf("order id = %q, want %q", got, c.want)
				}
			}
		})
	}
}

func TestNew_RejectsInvalidRetry(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	for _, retry := range []int{1, -1, -5} {
		_, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0), Retry: retry})
		if !errors.Is(err, ErrInvalidOptions) {
			t.Fatalf("Retry=%d: err = %v, want ErrInvalidOptions", retry, err)
		}
	}
}
