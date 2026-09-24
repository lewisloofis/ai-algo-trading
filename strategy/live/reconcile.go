package live

import (
	"context"
	"fmt"
	"sort"
)

// Baseline snapshots GET /order/v2/list before this run starts submitting
// anything, so ReconcileTerminal can later tell a pre-existing order apart
// from one that showed up during the run.
//
// This exists because /order/v2/list is NOT account-scoped (confirmed live,
// docs/plans/18-alpha-and-infrastructure-split.md §3.4, mirroring
// account.OpenOrders' doc comment for the read-only view of this same
// endpoint): it returns every order the trading token can see across every
// sub-account, including the core book's, and a Snapshot carries no account
// field to filter on. That rules out the obvious divergence signal — "an
// order neither ours nor previously known exists, therefore something is
// wrong" — because the core book always has some resting orders of its
// own. Baseline is the only way this package has to tell "was already there"
// apart from "appeared during this run".
func (s *Sink) Baseline(ctx context.Context) error {
	orders, err := s.client.List(ctx)
	if err != nil {
		return fmt.Errorf("live: Baseline: %w", err)
	}
	baseline := make(map[string]struct{}, len(orders))
	for _, o := range orders {
		baseline[o.OrderID] = struct{}{}
	}
	s.mu.Lock()
	s.baseline = baseline
	s.mu.Unlock()
	return nil
}

// LotMismatch names one order, among those that reached a terminal state,
// where the broker's own lot_done disagrees with the lots this Sink itself
// emitted as AsyncFills for it.
type LotMismatch struct {
	ClientOrderID   string
	BrokerLotDone   int
	SinkEmittedLots int
}

// Divergence is ReconcileTerminal's report. A zero-value (Empty()) Divergence
// means every order this Sink placed reached a terminal state with its
// lot accounting agreeing with Carina's — anything else is a Sink whose
// picture of the run does not match reality and should not be trusted for
// account.Reconcile's position comparison (strategy/account/recon.go): that
// comparison is only as good as the fill data this package (or the
// operator) has already found agrees with the broker's own view of every
// order this package itself opened.
type Divergence struct {
	// NotTerminal lists client order ids this Sink placed that had not
	// reached a terminal state by the time ReconcileTerminal ran.
	NotTerminal []string
	// LotMismatch lists terminal orders whose broker-reported lot_done
	// disagrees with what this Sink emitted as fills.
	LotMismatch []LotMismatch
	// Unbaselined lists broker order ids seen in List() that are neither
	// in the Baseline snapshot nor tracked by this Sink — an order that
	// appeared from somewhere else during the run. See Baseline's doc
	// comment for why this, not "any unknown order", is the actual
	// divergence signal available on this endpoint.
	Unbaselined []string
}

// Empty reports whether d found nothing to flag.
func (d Divergence) Empty() bool {
	return len(d.NotTerminal) == 0 && len(d.LotMismatch) == 0 && len(d.Unbaselined) == 0
}

// ReconcileTerminal is the end-of-run check: every order this Sink placed
// must have reached a terminal state, and for each that did, the broker's
// lot_done must equal the lots this Sink itself emitted as AsyncFills. This
// is what makes strategy/account.Reconcile's position comparison meaningful
// — that comparison assumes the fills feeding the post-trade store are
// complete and correct, and this is the check that assumption is actually
// true for this run, rather than an unstated hope.
func (s *Sink) ReconcileTerminal(ctx context.Context) (Divergence, error) {
	orders, err := s.client.List(ctx)
	if err != nil {
		return Divergence{}, fmt.Errorf("live: ReconcileTerminal: %w", err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	byBroker := make(map[string]Snapshot, len(orders))
	for _, o := range orders {
		byBroker[o.OrderID] = o
	}

	var div Divergence
	for clientID, t := range s.orders {
		if !IsTerminal(t.State) {
			div.NotTerminal = append(div.NotTerminal, clientID)
			continue
		}
		snap, ok := byBroker[t.BrokerOrderID]
		if !ok {
			// Terminal and no longer listed at all: nothing left to
			// compare lot_done against. Not itself a divergence — a
			// cancelled or rejected order can legitimately drop out of
			// GET /order/v2/list once resolved.
			continue
		}
		if brokerLotDone := int(snap.Qty.LotDone); brokerLotDone != t.LotsFilled {
			div.LotMismatch = append(div.LotMismatch, LotMismatch{
				ClientOrderID:   clientID,
				BrokerLotDone:   brokerLotDone,
				SinkEmittedLots: t.LotsFilled,
			})
		}
	}

	for _, o := range orders {
		if _, wasBaseline := s.baseline[o.OrderID]; wasBaseline {
			continue
		}
		if _, isOurs := s.byBroker[o.OrderID]; isOurs {
			continue
		}
		div.Unbaselined = append(div.Unbaselined, o.OrderID)
	}

	sort.Strings(div.NotTerminal)
	sort.Slice(div.LotMismatch, func(i, j int) bool { return div.LotMismatch[i].ClientOrderID < div.LotMismatch[j].ClientOrderID })
	sort.Strings(div.Unbaselined)

	return div, nil
}
