package main

import (
	"fmt"
	"regexp"
	"time"
)

const (
	// sustainedFailureThreshold is the number of consecutive failed connect
	// attempts after which every backoff window logs one ERROR line. Before this
	// a WAF 403 / dead token looked like ordinary reconnect churn (2026-10-08..14:
	// 5 sessions, zero data records, nothing alerted).
	sustainedFailureThreshold = 20
	// stableConnectionAfter is how long a connection must have lived to count
	// as healthy; shorter runs are failed attempts even if the dial succeeded.
	stableConnectionAfter = 30 * time.Second
)

var httpStatusRe = regexp.MustCompile(`HTTP (\d{3})`)

// statusClass labels an error by the HTTP status embedded in feed errors
// ("... (HTTP 403) ..."), distinguishing token rejection (401) from a
// possible WAF/User-Agent block (403). Never includes the error body.
func statusClass(err error) string {
	if err == nil {
		return "none"
	}
	m := httpStatusRe.FindStringSubmatch(err.Error())
	if m == nil {
		return "other"
	}
	switch m[1] {
	case "401":
		return "401 (token rejected)"
	case "403":
		return "403 (WAF/User-Agent block?)"
	}
	return "HTTP " + m[1]
}

func isStableConnection(ran time.Duration) bool { return ran >= stableConnectionAfter }

// reconnectState tracks consecutive failures and the capped exponential backoff.
type reconnectState struct {
	backoff     time.Duration
	consecutive int
}

func newReconnectState() *reconnectState {
	return &reconnectState{backoff: reconnectMin}
}

// onFailure records a failed attempt and returns the wait before the next one
// and whether the failure is sustained (>= sustainedFailureThreshold in a row).
func (r *reconnectState) onFailure(_ error) (wait time.Duration, sustained bool) {
	r.consecutive++
	wait = r.backoff
	r.backoff *= 2
	if r.backoff > reconnectMax {
		r.backoff = reconnectMax
	}
	return wait, r.consecutive >= sustainedFailureThreshold
}

// onStableConnection resets the failure count and backoff after a healthy run.
func (r *reconnectState) onStableConnection() {
	r.consecutive = 0
	r.backoff = reconnectMin
}

func sustainedFailureMessage(n int, err error, wait time.Duration) string {
	return fmt.Sprintf(
		"[feed] ERROR: %d consecutive failed connect attempts, last error class %s: %v — next retry in %s; "+
			"no data is being captured (check token / WAF User-Agent / network)",
		n, statusClass(err), err, wait)
}
