package main

import (
	"fmt"
	"sync"
)

// Signal is the output of the strategy on each book update.
type Signal int

const (
	SignalNone Signal = iota
	SignalBuy
	SignalSell
)

func (s Signal) String() string {
	switch s {
	case SignalBuy:
		return "BUY"
	case SignalSell:
		return "SELL"
	default:
		return "NONE"
	}
}

// ImbalanceStrategy computes top-N order-book volume imbalance:
//
//	imbalance = (sum bidVol - sum offerVol) / (sum bidVol + sum offerVol)   in [-1, 1]
//
// A strongly bid-heavy book (imbalance > +threshold) signals buying pressure;
// a strongly offer-heavy book (imbalance < -threshold) signals selling pressure.
// Bids and offers arrive as separate messages, so we cache the latest of each.
type ImbalanceStrategy struct {
	depth     int
	threshold float64

	mu     sync.Mutex
	bids   []level
	offers []level
	last   Signal
}

func NewImbalanceStrategy(depth int, threshold float64) *ImbalanceStrategy {
	if depth < 1 {
		depth = 1
	}
	return &ImbalanceStrategy{depth: depth, threshold: threshold, last: SignalNone}
}

// Update feeds one parsed book side in and returns the current signal plus the
// computed imbalance and the reference (mid/last) price.
func (s *ImbalanceStrategy) Update(b bookSide) (sig Signal, imbalance, refPrice float64, ready bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	switch b.side {
	case "BID":
		s.bids = b.levels
	case "OFFER":
		s.offers = b.levels
	}
	if len(s.bids) == 0 || len(s.offers) == 0 {
		return SignalNone, 0, 0, false
	}

	bidVol := sumVolume(s.bids, s.depth)
	offerVol := sumVolume(s.offers, s.depth)
	total := bidVol + offerVol
	if total <= 0 {
		return SignalNone, 0, 0, false
	}
	imbalance = (bidVol - offerVol) / total
	refPrice = (s.bids[0].price + s.offers[0].price) / 2

	sig = SignalNone
	if imbalance >= s.threshold {
		sig = SignalBuy
	} else if imbalance <= -s.threshold {
		sig = SignalSell
	}
	s.last = sig
	return sig, imbalance, refPrice, true
}

func sumVolume(levels []level, depth int) float64 {
	var sum float64
	for i := 0; i < len(levels) && i < depth; i++ {
		sum += levels[i].volume
	}
	return sum
}

// Describe returns a one-line snapshot for logging.
func (s *ImbalanceStrategy) Describe(imbalance, refPrice float64) string {
	return fmt.Sprintf("imbalance=%+.3f ref=%.0f (depth=%d, thr=%.2f)", imbalance, refPrice, s.depth, s.threshold)
}
