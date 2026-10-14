// Package recon reconciles the bot's own record of what it did (the SQLite
// post-trade stores) with the broker's record (holdings and trade history),
// and with trades the owner placed by hand. The broker is the source of
// truth: every disagreement is reported, never silently resolved.
//
// The package is structurally read-only. It imports no package that can
// place an order and builds no HTTP request itself (readonly_test.go).
package recon

import (
	"math"
	"sort"
	"time"

	"strategy/account"
	"strategy/engine"
)

const (
	// DefaultMatchTolerance is how far apart a broker trade's time and a
	// store fill's receive time may be. The poller observes a fill seconds
	// after the exchange does; minutes would be a different trade.
	DefaultMatchTolerance = 5 * time.Minute
	// FeeGapFlagIDR is the fee difference above which a matched pair is
	// flagged. The broker's fee is the truth.
	FeeGapFlagIDR = 0.5
	// priceEpsilon absorbs float noise comparing a broker price to the
	// store's whole-IDR fill price.
	priceEpsilon = 0.005
)

// Pair is a broker trade matched to the bot fill that produced it.
type Pair struct {
	Broker account.HistoryTrade
	Fill   engine.StoreFill
}

// FeeGap is broker fee minus store fee: positive when the store under-charged.
func (p Pair) FeeGap() float64 { return round2(p.Broker.FeeIDR - p.Fill.FeeIDR) }

// FeeGapFlagged reports a gap beyond FeeGapFlagIDR.
func (p Pair) FeeGapFlagged() bool { return math.Abs(p.FeeGap()) > FeeGapFlagIDR }

// TimeDiff is |broker time - fill time|.
func (p Pair) TimeDiff() time.Duration { return absDur(p.Broker.Time.Sub(fillTime(p.Fill))) }

// MatchResult is the three-way outcome of comparing broker trades with fills.
type MatchResult struct {
	Matched    []Pair
	BrokerOnly []account.HistoryTrade // a manual trade in the app
	StoreOnly  []engine.StoreFill     // a phantom fill: a bot bug
}

func fillTime(f engine.StoreFill) time.Time { return time.Unix(0, f.RecvNs).In(account.WIB) }

func fillDate(f engine.StoreFill) string { return fillTime(f).Format("2006-01-02") }

func absDur(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}

func round2(x float64) float64 { return math.Round(x*100) / 100 }

// MatchTrades pairs each broker trade with at most one store fill having the
// same symbol, side, Jakarta date, price and lots, and a time within tol.
// The broker's id does not carry our order id, so that is all there is to
// match on.
//
// It is deterministic whatever the input order: every feasible pair is ranked
// by (time gap, broker time, broker id, fill time, order id, run id, seq) and
// assigned greedily, so two identical trades always pair the same way.
func MatchTrades(broker []account.HistoryTrade, fills []engine.StoreFill, tol time.Duration) MatchResult {
	type cand struct {
		b, f int
		gap  time.Duration
	}
	var cands []cand
	for bi, b := range broker {
		for fi, f := range fills {
			if b.Symbol != f.Symbol || b.Side != f.Side || b.Lots != f.Lots ||
				b.Date() != fillDate(f) || math.Abs(b.PriceIDR-float64(f.PriceIDR)) > priceEpsilon {
				continue
			}
			if gap := absDur(b.Time.Sub(fillTime(f))); gap <= tol {
				cands = append(cands, cand{bi, fi, gap})
			}
		}
	}
	sort.Slice(cands, func(i, j int) bool {
		a, b := cands[i], cands[j]
		if a.gap != b.gap {
			return a.gap < b.gap
		}
		ba, bb := broker[a.b], broker[b.b]
		if !ba.Time.Equal(bb.Time) {
			return ba.Time.Before(bb.Time)
		}
		if ba.ID != bb.ID {
			return ba.ID < bb.ID
		}
		return fillLess(fills[a.f], fills[b.f])
	})

	usedB := make([]bool, len(broker))
	usedF := make([]bool, len(fills))
	var res MatchResult
	for _, c := range cands {
		if usedB[c.b] || usedF[c.f] {
			continue
		}
		usedB[c.b], usedF[c.f] = true, true
		res.Matched = append(res.Matched, Pair{Broker: broker[c.b], Fill: fills[c.f]})
	}
	for i, b := range broker {
		if !usedB[i] {
			res.BrokerOnly = append(res.BrokerOnly, b)
		}
	}
	for i, f := range fills {
		if !usedF[i] {
			res.StoreOnly = append(res.StoreOnly, f)
		}
	}
	sort.Slice(res.Matched, func(i, j int) bool {
		a, b := res.Matched[i].Broker, res.Matched[j].Broker
		if !a.Time.Equal(b.Time) {
			return a.Time.Before(b.Time)
		}
		return a.ID < b.ID
	})
	sort.Slice(res.BrokerOnly, func(i, j int) bool {
		a, b := res.BrokerOnly[i], res.BrokerOnly[j]
		if !a.Time.Equal(b.Time) {
			return a.Time.Before(b.Time)
		}
		return a.ID < b.ID
	})
	sort.Slice(res.StoreOnly, func(i, j int) bool { return fillLess(res.StoreOnly[i], res.StoreOnly[j]) })
	return res
}

func fillLess(a, b engine.StoreFill) bool {
	if a.RecvNs != b.RecvNs {
		return a.RecvNs < b.RecvNs
	}
	if a.OrderID != b.OrderID {
		return a.OrderID < b.OrderID
	}
	if a.RunID != b.RunID {
		return a.RunID < b.RunID
	}
	return a.Seq < b.Seq
}
