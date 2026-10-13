package live

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"strategy/engine"
)

// newDrainSink is a Sink with a fast poll interval, started on its own
// background context, for exercising Drain against the real poll loop.
func newDrainSink(t *testing.T, routes map[string]http.HandlerFunc) *Sink {
	t.Helper()
	c := newTestClient(t, routeServer(t, routes))
	s, err := NewSink(Options{Client: c, PollInterval: 10 * time.Millisecond})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	s.Start(context.Background())
	t.Cleanup(func() { _ = s.Close() })
	return s
}

const openSnap = `{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}]}`

// TestDrain_CancelsRestingOrderAndRefusesNewSubmits: Ctrl-C on a live run
// must not leave an order resting with nobody watching it (2026-10-13 review).
func TestDrain_CancelsRestingOrderAndRefusesNewSubmits(t *testing.T) {
	var cancelled atomic.Bool
	s := newDrainSink(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: func(w http.ResponseWriter, r *http.Request) {
			if cancelled.Load() {
				jsonHandler(http.StatusOK, `{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"WITHDRAWN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":0,"lot_total":1}}]}`)(w, r)
				return
			}
			jsonHandler(http.StatusOK, openSnap)(w, r)
		},
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			cancelled.Store(true)
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	if _, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := s.Drain(ctx); err != nil {
		t.Fatalf("Drain: %v", err)
	}
	if !cancelled.Load() {
		t.Fatal("Drain returned without cancelling the resting order")
	}
	if _, err := s.Submit(context.Background(), testIntent("rebalance-TLKM-1", 1)); !errors.Is(err, ErrShuttingDown) {
		t.Fatalf("Submit after Drain = %v, want ErrShuttingDown", err)
	}
}

// TestDrain_WaitsForAFillToBeDelivered: an order that fills during the drain
// is only settled once its AsyncFill has been handed to the engine, so the
// fill reaches the store before the engine is stopped.
func TestDrain_WaitsForAFillToBeDelivered(t *testing.T) {
	var cancelled atomic.Bool
	s := newDrainSink(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: func(w http.ResponseWriter, r *http.Request) {
			if cancelled.Load() {
				// Matched before the cancel took effect.
				jsonHandler(http.StatusOK, `{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":950000},"qty":{"lot_done":1,"lot_open":0,"lot_total":1}}]}`)(w, r)
				return
			}
			jsonHandler(http.StatusOK, openSnap)(w, r)
		},
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			cancelled.Store(true)
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	if _, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	// The engine's side: read fills slowly, recording when each arrived.
	var (
		mu       sync.Mutex
		received []engine.AsyncFill
	)
	go func() {
		for f := range s.Fills() {
			time.Sleep(50 * time.Millisecond)
			mu.Lock()
			received = append(received, f)
			mu.Unlock()
		}
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := s.Drain(ctx); err != nil {
		t.Fatalf("Drain: %v", err)
	}
	time.Sleep(100 * time.Millisecond) // let the reader finish recording a fill it already took
	mu.Lock()
	defer mu.Unlock()
	if len(received) != 1 || received[0].Fill.FillLots != 1 {
		t.Fatalf("fills delivered by the time Drain returned = %+v, want one 1-lot fill", received)
	}
}

// TestDrain_TimesOutOnAnOrderThatNeverSettles: Drain is bounded, and reports
// the unsettled order instead of claiming a clean stop.
func TestDrain_TimesOutOnAnOrderThatNeverSettles(t *testing.T) {
	s := newDrainSink(t, map[string]http.HandlerFunc{
		pathBuy:    jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList:   jsonHandler(http.StatusOK, openSnap),
		pathCancel: jsonHandler(http.StatusOK, `{"data":{}}`),
	})
	if _, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	err := s.Drain(ctx)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Drain = %v, want it to wrap context.DeadlineExceeded", err)
	}
}

func TestDrain_NoOrdersReturnsImmediately(t *testing.T) {
	s := newDrainSink(t, map[string]http.HandlerFunc{pathList: jsonHandler(http.StatusOK, `{"data":[]}`)})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := s.Drain(ctx); err != nil {
		t.Fatalf("Drain with no orders: %v", err)
	}
}
