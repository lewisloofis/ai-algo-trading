package main

import "testing"

// TestFormatInt covers the thousands-separator grouping across the sizes
// that previously exposed the off-by-one bug (see the fix history in
// formatInt's doc comment): the boundary right at 3 vs 4 digits, a 5-digit
// value (the exact case a reviewer spotted in seqcheck's table output,
// "1 5136" instead of "15 136"), 6+ digits, zero, and negative numbers of
// both 3-digit and 5+ digit magnitude (the sign byte must not shift the
// digit grouping).
func TestFormatInt(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name string
		n    int
		want string
	}{
		{"zero", 0, "0"},
		{"single digit", 7, "7"},
		{"three digits: no separator", 890, "890"},
		{"four digits: one group boundary", 9890, "9 890"},
		{"five digits: boundary after the first two, not the first one", 15136, "15 136"},
		{"six digits: boundary after the first three", 123456, "123 456"},
		{"seven digits: two separators", 1234567, "1 234 567"},
		{"negative three digits: sign does not consume a digit slot", -168, "-168"},
		{"negative five digits: grouped the same as the positive magnitude", -15136, "-15 136"},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if got := formatInt(tc.n); got != tc.want {
				t.Errorf("formatInt(%d): got %q want %q", tc.n, got, tc.want)
			}
		})
	}
}
