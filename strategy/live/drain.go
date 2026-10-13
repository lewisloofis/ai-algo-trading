package live

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"strategy/engine"
)

// ErrShuttingDown is returned by Submit once Drain has started: a run that
// is stopping must not place new orders while it cancels the old ones.
var ErrShuttingDown = errors.New("live: sink is shutting down; no new orders")

// drainCheckInterval is how often Drain re-checks order state. The poller,
// not Drain, refreshes that state from Carina (every PollInterval).
const drainCheckInterval = 100 * time.Millisecond

// Drain is the operator-stop path (SIGINT/SIGTERM): refuse new submissions,
// cancel every order still resting, and wait until each order this Sink
// placed is settled — terminal at the broker AND every lot the broker
// reports done already handed off on Fills(). The poller must still be
// running (Start's context not yet cancelled) and engine.Run must still be
// reading Fills(), or nothing can settle; stop both only after Drain
// returns.
//
// Before this existed, Ctrl-C stopped the poller with orders still resting:
// a later fill was never recorded and the run still ended "complete"
// (review of 2f5a4e0, 2026-10-13).
//
// Returns nil once everything is settled, or ctx's error (wrapped, naming
// the unsettled client order ids) if ctx ends first. A cancel that fails is
// logged and retried on the next check.
func (s *Sink) Drain(ctx context.Context) error {
	s.mu.Lock()
	s.draining = true
	s.mu.Unlock()

	cancelled := make(map[string]bool)
	ticker := time.NewTicker(drainCheckInterval)
	defer ticker.Stop()
	for {
		unsettled, toCancel := s.drainStatus(cancelled)
		if len(unsettled) == 0 {
			return nil
		}
		for _, id := range toCancel {
			if err := s.Cancel(ctx, engine.OrderID(id)); err != nil {
				s.logf("live: drain: cancel %s: %v (will retry)", id, err)
				continue
			}
			cancelled[id] = true
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("live: drain: orders not settled %v: %w", unsettled, ctx.Err())
		case <-ticker.C:
		}
	}
}

// drainStatus lists, under mu, the client order ids not yet settled, and
// which of those still need a cancel sent: resting at the broker (has a
// broker id, not terminal) and not already cancelled by this drain.
func (s *Sink) drainStatus(cancelled map[string]bool) (unsettled, toCancel []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, t := range s.orders {
		if IsTerminal(t.State) && t.LotsFilled >= t.brokerLotDone {
			continue
		}
		unsettled = append(unsettled, id)
		if !IsTerminal(t.State) && t.BrokerOrderID != "" && !cancelled[id] {
			toCancel = append(toCancel, id)
		}
	}
	sort.Strings(unsettled)
	sort.Strings(toCancel)
	return unsettled, toCancel
}
