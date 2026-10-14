package main

import (
	"errors"
	"fmt"
	"testing"
	"time"
)

func TestStatusClass(t *testing.T) {
	cases := []struct {
		err  error
		want string
	}{
		{fmt.Errorf("websocket key failed (HTTP 403): null"), "403 (WAF/User-Agent block?)"},
		{fmt.Errorf("websocket key failed (HTTP 401): x: %w", errors.New("unauthorized")), "401 (token rejected)"},
		{fmt.Errorf("handshake failed: HTTP 502"), "HTTP 502"},
		{errors.New("connection reset by peer"), "other"},
		{nil, "none"},
	}
	for _, c := range cases {
		if got := statusClass(c.err); got != c.want {
			t.Errorf("statusClass(%v) = %q, want %q", c.err, got, c.want)
		}
	}
}

func TestReconnectBackoffDoublesAndCaps(t *testing.T) {
	r := newReconnectState()
	prev := time.Duration(0)
	var last time.Duration
	for i := 0; i < 30; i++ {
		last, _ = r.onFailure(errors.New("x"))
		if last < prev {
			t.Fatalf("backoff decreased: %s -> %s", prev, last)
		}
		prev = last
	}
	if last != reconnectMax {
		t.Errorf("backoff = %s, want cap %s", last, reconnectMax)
	}
	if reconnectMax < 30*time.Second {
		t.Errorf("reconnectMax %s too small to stop churn", reconnectMax)
	}
}

func TestSustainedFailureEscalatesAtThreshold(t *testing.T) {
	r := newReconnectState()
	err := fmt.Errorf("websocket key failed (HTTP 403): null")
	for i := 1; i < sustainedFailureThreshold; i++ {
		if _, escalate := r.onFailure(err); escalate {
			t.Fatalf("escalated early at failure %d", i)
		}
	}
	for i := 0; i < 3; i++ {
		if _, escalate := r.onFailure(err); !escalate {
			t.Fatalf("expected escalation once threshold reached (extra %d)", i)
		}
	}
	if r.consecutive != sustainedFailureThreshold+2 {
		t.Errorf("consecutive = %d", r.consecutive)
	}
}

func TestStableConnectionResetsState(t *testing.T) {
	r := newReconnectState()
	for i := 0; i < sustainedFailureThreshold+5; i++ {
		r.onFailure(errors.New("x"))
	}
	r.onStableConnection()
	if r.consecutive != 0 {
		t.Errorf("consecutive not reset: %d", r.consecutive)
	}
	if d, _ := r.onFailure(errors.New("x")); d != reconnectMin {
		t.Errorf("backoff after reset = %s, want %s", d, reconnectMin)
	}
}

func TestIsStableConnection(t *testing.T) {
	if isStableConnection(time.Second) {
		t.Error("1s run must not count as stable")
	}
	if !isStableConnection(stableConnectionAfter) {
		t.Error("run of stableConnectionAfter must count as stable")
	}
}
