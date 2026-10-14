package live

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"strategy/engine"
)

// pollOnceCollectRejects runs one pollOnce and returns every AsyncReject it
// emitted. It also fails on any fill, since these scenarios carry none.
func pollOnceCollectRejects(t *testing.T, s *Sink, ctx context.Context) []engine.AsyncReject {
	t.Helper()
	done := make(chan struct{})
	go func() {
		s.pollOnce(ctx)
		close(done)
	}()
	var got []engine.AsyncReject
	timeout := time.After(2 * time.Second)
	for {
		select {
		case r := <-s.rejects:
			got = append(got, r)
		case f := <-s.fills:
			t.Fatalf("unexpected fill: %+v", f)
		case <-done:
			return got
		case <-timeout:
			t.Fatal("pollOnce hung")
		}
	}
}

func listWith(status, msg string) string {
	return `{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"` + status + `","message":"` + msg + `","price":{"order":9500,"average":{"price":0}},"amount":{"matched_total":0},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}]}`
}

func TestPoll_RejectedEmitsExactlyOneAsyncReject(t *testing.T) {
	var status atomic.Value
	status.Store("OPEN")
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: func(w http.ResponseWriter, r *http.Request) {
			st := status.Load().(string)
			jsonHandler(http.StatusOK, listWith(st, "[C-006] insufficient trading balance"))(w, r)
		},
	})
	s := newTestSink(t, srv)
	ctx := context.Background()
	if _, err := s.Submit(ctx, testIntent("rej-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	if got := pollOnceCollectRejects(t, s, ctx); len(got) != 0 {
		t.Fatalf("OPEN poll emitted %d rejects, want 0", len(got))
	}

	status.Store("REJECTED")
	got := pollOnceCollectRejects(t, s, ctx)
	if len(got) != 1 {
		t.Fatalf("REJECTED poll emitted %d rejects, want 1", len(got))
	}
	want := engine.AsyncReject{
		OrderID: "rej-1", Symbol: "BBCA", Side: "buy", PriceIDR: 9500, Lots: 1,
		Reason: "[C-006] insufficient trading balance", RecvNs: time.Unix(1_700_000_000, 0).UnixNano(),
	}
	if got[0] != want {
		t.Errorf("reject = %+v, want %+v", got[0], want)
	}

	if again := pollOnceCollectRejects(t, s, ctx); len(again) != 0 {
		t.Errorf("second REJECTED poll emitted %d rejects, want 0", len(again))
	}
}

func TestPoll_RejectedWithoutMessageFallsBackToStatusText(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:  jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, listWith("REJECTED", "")),
	})
	s := newTestSink(t, srv)
	ctx := context.Background()
	if _, err := s.Submit(ctx, testIntent("rej-2", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	got := pollOnceCollectRejects(t, s, ctx)
	if len(got) != 1 || got[0].Reason != "REJECTED" {
		t.Fatalf("got %+v, want one reject with reason REJECTED", got)
	}
}

func TestPoll_NonRejectTerminalEmitsNoReject(t *testing.T) {
	cases := []struct{ name, status string }{
		{"cancelled", "WITHDRAWN"},
		{"open", "OPEN"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := routeServer(t, map[string]http.HandlerFunc{
				pathBuy:  jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
				pathList: jsonHandler(http.StatusOK, listWith(tc.status, "")),
			})
			s := newTestSink(t, srv)
			ctx := context.Background()
			if _, err := s.Submit(ctx, testIntent("x-1", 1)); err != nil {
				t.Fatalf("Submit: %v", err)
			}
			if got := pollOnceCollectRejects(t, s, ctx); len(got) != 0 {
				t.Errorf("emitted %d rejects, want 0", len(got))
			}
		})
	}
}

func TestPoll_FilledEmitsNoReject(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:  jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":950000},"qty":{"lot_done":1,"lot_open":0,"lot_total":1}}]}`),
	})
	s := newTestSink(t, srv)
	ctx := context.Background()
	if _, err := s.Submit(ctx, testIntent("f-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	done := make(chan struct{})
	go func() { s.pollOnce(ctx); close(done) }()
	for {
		select {
		case <-s.fills:
		case r := <-s.rejects:
			t.Fatalf("unexpected reject: %+v", r)
		case <-done:
			return
		case <-time.After(2 * time.Second):
			t.Fatal("hung")
		}
	}
}

// A REJECTED notice not yet handed off keeps Drain from reporting settled.
func TestDrainStatus_UndeliveredRejectIsUnsettled(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
	})
	s := newTestSink(t, srv)
	if _, err := s.Submit(context.Background(), testIntent("d-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	s.mu.Lock()
	tr := s.orders["d-1"]
	tr.State = StateRejected
	tr.rejectQueued = true
	s.mu.Unlock()
	if un, _ := s.drainStatus(map[string]bool{}); len(un) != 1 {
		t.Fatalf("unsettled = %v, want [d-1]", un)
	}
	s.mu.Lock()
	tr.rejectDelivered = true
	s.mu.Unlock()
	if un, _ := s.drainStatus(map[string]bool{}); len(un) != 0 {
		t.Fatalf("unsettled = %v, want none once delivered", un)
	}
}
