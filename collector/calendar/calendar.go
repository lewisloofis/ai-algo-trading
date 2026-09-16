// Package calendar provides a holiday-aware IDX trading calendar.
// The holiday data is embedded from idx_holidays.json at compile time.
package calendar

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"time"
)

//go:embed idx_holidays.json
var holidayJSON []byte

type holiday struct {
	Date string `json:"date"`
	Kind string `json:"kind"`
	Desc string `json:"desc"`
}

type holidayFile struct {
	Source      string    `json:"source"`
	Provisional bool      `json:"provisional"`
	Weekmask    string    `json:"weekmask"`
	Holidays    []holiday `json:"holidays"`
}

// Calendar is a holiday-aware IDX trading calendar.
type Calendar struct {
	holidays    map[string]bool // key: "YYYY-MM-DD"
	Provisional bool
}

// Load parses the embedded idx_holidays.json and returns a Calendar.
func Load() (*Calendar, error) {
	var hf holidayFile
	if err := json.Unmarshal(holidayJSON, &hf); err != nil {
		return nil, fmt.Errorf("parse idx_holidays.json: %w", err)
	}
	h := make(map[string]bool, len(hf.Holidays))
	for _, entry := range hf.Holidays {
		h[entry.Date] = true
	}
	return &Calendar{holidays: h, Provisional: hf.Provisional}, nil
}

// Jakarta is the exchange's timezone: WIB, UTC+7, no DST — so a fixed zone
// is exact rather than an approximation.
//
// It is exported because "what date is it?" must be answered in Jakarta by
// every consumer, not in whatever zone the host happens to run in. The
// collector box runs on UTC, where the two disagree for the seven hours
// after midnight WIB: at 02:00 Saturday in Jakarta it is still Friday
// 19:00 UTC, and a caller passing an unconverted time.Now() to
// IsTradingDay would be told the weekend is a trading day. Callers convert
// explicitly — cal.IsTradingDay(now.In(calendar.Jakarta)) — rather than
// this package guessing, because a time.Time that is already a calendar
// date (as in AddTradingDays' arguments) must not be shifted again.
var Jakarta = time.FixedZone("WIB", 7*60*60)

// dateKey normalises a time.Time to a "YYYY-MM-DD" key (date only, no TZ shift).
func dateKey(d time.Time) string {
	return d.Format("2006-01-02")
}

// norm normalises d to UTC midnight for consistent date comparison.
func norm(d time.Time) time.Time {
	return time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, time.UTC)
}

// IsTradingDay returns true if d is Mon-Fri and not in the holiday set.
func (c *Calendar) IsTradingDay(d time.Time) bool {
	wd := d.Weekday()
	if wd == time.Saturday || wd == time.Sunday {
		return false
	}
	return !c.holidays[dateKey(norm(d))]
}

// NextTradingDay returns the nearest trading day strictly after d.
func (c *Calendar) NextTradingDay(d time.Time) time.Time {
	candidate := norm(d).AddDate(0, 0, 1)
	for !c.IsTradingDay(candidate) {
		candidate = candidate.AddDate(0, 0, 1)
	}
	return candidate
}

// PrevTradingDay returns the nearest trading day strictly before d.
func (c *Calendar) PrevTradingDay(d time.Time) time.Time {
	candidate := norm(d).AddDate(0, 0, -1)
	for !c.IsTradingDay(candidate) {
		candidate = candidate.AddDate(0, 0, -1)
	}
	return candidate
}

// AddTradingDays rolls n trading days forward (n<0 backward) from d.
func (c *Calendar) AddTradingDays(d time.Time, n int) time.Time {
	if n == 0 {
		return d
	}
	step := 1
	if n < 0 {
		step = -1
	}
	remaining := n
	if remaining < 0 {
		remaining = -remaining
	}
	current := norm(d)
	for remaining > 0 {
		current = current.AddDate(0, 0, step)
		if c.IsTradingDay(current) {
			remaining--
		}
	}
	return current
}

// TradingDaysBetween counts trading days in (a, b] — exclusive a, inclusive b.
func (c *Calendar) TradingDaysBetween(a, b time.Time) int {
	na := norm(a)
	nb := norm(b)
	if !na.Before(nb) {
		return 0
	}
	count := 0
	current := na.AddDate(0, 0, 1)
	for !current.After(nb) {
		if c.IsTradingDay(current) {
			count++
		}
		current = current.AddDate(0, 0, 1)
	}
	return count
}

// SettlementCalendarDays returns calendar days from entry until the
// T+tPlus trading-day settlement date.
func (c *Calendar) SettlementCalendarDays(entry time.Time, tPlus int) int {
	settlement := c.AddTradingDays(entry, tPlus)
	return int(norm(settlement).Sub(norm(entry)).Hours() / 24)
}
