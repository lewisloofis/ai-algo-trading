package account

import (
	"context"
	"errors"
	"fmt"
	"time"

	"strategy/engine"
)

// DailyPnL implements the running realized+unrealized PnL figure a risk
// layer needs to enforce a daily loss limit (strategy/risk.PnLProvider).
// This package does not import strategy/risk — satisfying that interface
// structurally is a one-way dependency (risk depends on account's shape,
// not the other way around), and there is no compile-time assertion in this
// file for it; see dailypnl_test.go, which imports strategy/risk harmlessly
// (test files are not part of the package's dependency graph proper) to
// assert *DailyPnL satisfies it.
//
// DailyPnL only reads: a GET through Client (PortfolioList) and read-only
// queries through PostTradeStore. It adds no HTTP verb, so it does not
// disturb this package's structurally-read-only guarantee — see
// readonly_test.go.
type DailyPnL struct {
	client   *Client
	store    *engine.PostTradeStore
	sinkKind string
	fees     FeeSchedule
	now      func() time.Time
}

// NewDailyPnL returns a DailyPnL scoped to sinkKind, reading positions
// through client and closed round-trips through store.
//
// client, store and sinkKind are required and rejected when nil/empty
// rather than silently defaulted — the same stance New takes on accNo (see
// its doc comment): a caller that forgot to wire one of these should get a
// construction error, not a DailyPnL that quietly reads the wrong sink's
// rows or panics on first use.
//
// fees is passed straight to ComputePortfolioPnL for the unrealized half;
// its zero value does not corrupt the result — FeeSchedule only affects
// ComputePositionPnL's BreakEvenPrice, which DailyPnLIDR never reads — but a
// caller should still pass the real schedule (DefaultFeeSchedule or better)
// since nothing here will validate it.
//
// clock supplies "now" for the Jakarta trading-day boundary, defaulting to
// time.Now; tests inject a fixed clock so the day boundary is deterministic.
func NewDailyPnL(client *Client, store *engine.PostTradeStore, sinkKind string, fees FeeSchedule, clock func() time.Time) (*DailyPnL, error) {
	if client == nil {
		return nil, errors.New("account: NewDailyPnL: client is required")
	}
	if store == nil {
		return nil, errors.New("account: NewDailyPnL: store is required")
	}
	if sinkKind == "" {
		return nil, errors.New("account: NewDailyPnL: sinkKind is required (an empty value would silently read whichever sink_kind happens to default to it, mixing paper and live rows into one loss figure)")
	}
	if clock == nil {
		clock = time.Now
	}
	return &DailyPnL{client: client, store: store, sinkKind: sinkKind, fees: fees, now: clock}, nil
}

// DailyPnLIDR returns today's realized+unrealized PnL in IDR: realized
// closed round-trips (engine.PostTradeStore.RealizedPnLIDRByDate) plus the
// broker's own unrealized figure for whatever is currently held
// (ComputePortfolioPnL(client.PortfolioList(...)).TotalUnrealizedPnLIDR).
//
// The unrealized half is NOT day-scoped, and that is a real limitation to
// keep in mind, not an oversight: the broker reports unrealized PnL against
// a position's full cost basis, not against today's opening mark, so a
// position carried in from a previous session has its entire multi-day
// unrealized move counted as if it happened today. (PortfolioSummary also
// carries a profit_loss.unrealised field, but §2.4 of
// docs/reference/05-account-state.md only documents profit_loss.realised as
// "for the period" — undocumented and unconfirmed which period — so it is
// not a safer substitute and is not used here.) This errs in the safe
// direction for a loss limit: it can only trip the limit earlier than a
// precisely day-scoped figure would, never later, and the sleeve this
// serves is a <=2-day-horizon book that starts each cycle flat (see
// docs/adr/0017-sleeve-is-a-funded-sub-account.md), so the over-count is
// expected to be small in practice. A future reader must not mistake the
// number this returns for a precise day-scoped PnL.
//
// An error from either half is returned as-is, never degraded to a 0 PnL.
// In particular a 401 (ErrUnauthorized) or 403 (ErrForbidden) from
// PortfolioList surfaces as an error
// (wrapped, so errors.Is still matches) — this package has already shipped
// the bug where an expired token's 401 became an empty result read as a
// confident "nothing found" (see ErrUnauthorized's doc comment), and
// RiskSink is built to deny-by-default on a PnLProvider error, which is
// correct only if that error actually reaches it.
func (d *DailyPnL) DailyPnLIDR(ctx context.Context) (float64, error) {
	asOfDate := engine.JakartaDate(d.now().UnixNano())

	realizedIDR, err := d.store.RealizedPnLIDRByDate(d.sinkKind, asOfDate)
	if err != nil {
		return 0, fmt.Errorf("account: DailyPnLIDR: realized: %w", err)
	}

	list, err := d.client.PortfolioList(ctx)
	if err != nil {
		return 0, fmt.Errorf("account: DailyPnLIDR: portfolio: %w", err)
	}
	unrealizedIDR := ComputePortfolioPnL(list.Results, d.fees).TotalUnrealizedPnLIDR

	return float64(realizedIDR) + float64(unrealizedIDR), nil
}
