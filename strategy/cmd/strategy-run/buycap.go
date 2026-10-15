package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"collector/calendar"
	"strategy/engine"
	"strategy/live"
	"strategy/risk"
)

// notPlacedClassifier says which inner.Submit errors PROVE an order never reached
// the market, so the risk layer may refund its reservation (exposure, day
// notional, buy cap). Everything else keeps the reservation: a timeout, a
// transport error, a 5xx or an unrecognised error may mean Carina accepted the
// order, and refunding it would let later buys exceed the caps.
//
// Definite: a typed broker refusal (invalid parameter, suspended account,
// token rejected), a 4xx other than 408, and the sink refusing because it is
// shutting down. A paper sink places nothing, so any paper error is definite.
func notPlacedClassifier(sinkName string) risk.NotPlacedFunc {
	return func(err error) bool {
		if sinkName == "paper" {
			return true
		}
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
			return false
		}
		if errors.Is(err, live.ErrInvalidParameter) || errors.Is(err, live.ErrUserSuspended) ||
			errors.Is(err, live.ErrUnauthorized) || errors.Is(err, live.ErrShuttingDown) {
			return true
		}
		var api *live.APIError
		if errors.As(err, &api) {
			return api.Status >= 400 && api.Status < 500 && api.Status != http.StatusRequestTimeout
		}
		return false
	}
}

// seedBoughtToday sums the notional bought today (Jakarta date of now) across
// every store matched by glob, for sinkKind: what an earlier attempt of the
// same day already spent against the buy cap. A glob matching nothing is the
// day's first run and seeds zero.
func seedBoughtToday(glob, sinkKind string, now time.Time) (float64, error) {
	if glob == "" {
		return 0, nil
	}
	paths, err := globFiles(glob)
	if err != nil {
		return 0, fmt.Errorf("resolve %q: %w", glob, err)
	}
	date := now.In(calendar.Jakarta).Format("2006-01-02")
	var total int64
	for _, path := range paths {
		s, err := engine.OpenStore(path)
		if err != nil {
			return 0, fmt.Errorf("open %s: %w", path, err)
		}
		n, err := s.BuyNotionalIDRByDate(sinkKind, date)
		cerr := s.Close()
		if err != nil {
			return 0, fmt.Errorf("bought notional %s: %w", path, err)
		}
		if cerr != nil {
			return 0, fmt.Errorf("close %s: %w", path, cerr)
		}
		total += n
	}
	return float64(total), nil
}

// buyCapOptions turns --max-buy-notional-idr into risk options. 0 means no
// absolute cap (the default); negative is refused. The cap is seeded from the
// stores in priorStores so a restart cannot hand out a fresh day's budget, and
// a day already at or over the cap still starts (sells are uncapped) but the
// sink then refuses every further buy.
func buyCapOptions(maxBuyIDR int64, priorStores, sinkKind string, now time.Time) ([]risk.Option, error) {
	if maxBuyIDR < 0 {
		return nil, fmt.Errorf("--max-buy-notional-idr must not be negative (got %d)", maxBuyIDR)
	}
	if maxBuyIDR == 0 {
		return nil, nil
	}
	seed, err := seedBoughtToday(priorStores, sinkKind, now)
	if err != nil {
		return nil, fmt.Errorf("--max-buy-notional-idr: cannot count today's earlier buys (refusing rather than guessing): %w", err)
	}
	// A seed at or over the cap is NOT a reason to refuse to start: sells are
	// uncapped and a multi-name target may still need one. Every further buy is
	// refused by the sink.
	return []risk.Option{risk.WithMaxBoughtNotionalIDR(float64(maxBuyIDR), seed)}, nil
}
