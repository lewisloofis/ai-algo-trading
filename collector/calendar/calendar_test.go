package calendar

import (
	"testing"
	"time"
)

func mustDate(s string) time.Time {
	t, err := time.Parse("2006-01-02", s)
	if err != nil {
		panic(err)
	}
	return t
}

func loadOrFatal(t *testing.T) *Calendar {
	t.Helper()
	cal, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	return cal
}

func TestIsTradingDay(t *testing.T) {
	cal := loadOrFatal(t)
	cases := []struct {
		date     string
		expected bool
	}{
		{"2026-01-01", false}, // Thu, Tahun Baru holiday
		{"2026-01-02", true},  // Fri
		{"2026-01-03", false}, // Sat
		{"2026-01-05", true},  // Mon
		{"2026-06-01", false}, // Mon, Hari Lahir Pancasila
	}
	for _, tc := range cases {
		t.Run(tc.date, func(t *testing.T) {
			got := cal.IsTradingDay(mustDate(tc.date))
			if got != tc.expected {
				t.Errorf("IsTradingDay(%s) = %v, want %v", tc.date, got, tc.expected)
			}
		})
	}
}

func TestTradingDayCount2026(t *testing.T) {
	cal := loadOrFatal(t)
	start := mustDate("2026-01-01")
	end := mustDate("2026-12-31")
	count := 0
	for d := start; !d.After(end); d = d.AddDate(0, 0, 1) {
		if cal.IsTradingDay(d) {
			count++
		}
	}
	if count != 239 {
		t.Errorf("2026 trading day count = %d, want 239", count)
	}
}

func TestSettlementCalendarDays(t *testing.T) {
	cal := loadOrFatal(t)
	cases := []struct {
		entry    string
		tPlus    int
		expected int
	}{
		{"2026-01-08", 2, 4}, // Thu entry: T+1=Jan9(Fri), T+2=Jan12(Mon); 4 cal days
		{"2026-05-29", 2, 5}, // Fri entry; May30-31 weekend, Jun1 holiday; T+1=Jun2, T+2=Jun3; 5 cal days
	}
	for _, tc := range cases {
		t.Run(tc.entry, func(t *testing.T) {
			got := cal.SettlementCalendarDays(mustDate(tc.entry), tc.tPlus)
			if got != tc.expected {
				t.Errorf("SettlementCalendarDays(%s, %d) = %d, want %d", tc.entry, tc.tPlus, got, tc.expected)
			}
		})
	}
}

func TestAddTradingDays(t *testing.T) {
	cal := loadOrFatal(t)
	cases := []struct {
		start    string
		n        int
		expected string
	}{
		{"2026-01-08", 0, "2026-01-08"}, // no change
		{"2026-01-08", 2, "2026-01-12"}, // Thu+2: Fri=T+1, Mon=T+2
	}
	for _, tc := range cases {
		t.Run(tc.start, func(t *testing.T) {
			got := cal.AddTradingDays(mustDate(tc.start), tc.n)
			want := mustDate(tc.expected)
			if !got.Equal(want) {
				t.Errorf("AddTradingDays(%s, %d) = %s, want %s", tc.start, tc.n, got.Format("2006-01-02"), tc.expected)
			}
		})
	}
}

func TestTradingDaysBetween(t *testing.T) {
	cal := loadOrFatal(t)
	// (Jan 1, Jan 5]: Jan 2 (Fri) + Jan 5 (Mon) = 2
	got := cal.TradingDaysBetween(mustDate("2026-01-01"), mustDate("2026-01-05"))
	if got != 2 {
		t.Errorf("TradingDaysBetween(Jan1, Jan5) = %d, want 2", got)
	}

	// same date → 0
	got = cal.TradingDaysBetween(mustDate("2026-01-01"), mustDate("2026-01-01"))
	if got != 0 {
		t.Errorf("TradingDaysBetween(same, same) = %d, want 0", got)
	}
}
