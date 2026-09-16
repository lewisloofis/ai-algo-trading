package market

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"collector/calendar"
	"strategy/risk"
)

// Gate is the endpoint-backed risk.MarketGate. It answers the five
// reference-06 questions strategy/risk cannot decide locally, caches each
// answer for its own TTL, and returns Unknown — which rejects — for
// anything it could not confirm.
//
// Concurrency: safe for concurrent use. Network calls are made outside the
// mutex, so two goroutines asking about the same cold symbol may both fetch
// it; that is a duplicated GET, not a correctness problem, and it is
// preferable to holding a lock across a network call on the order path.
type Gate struct {
	cal    *calendar.Calendar
	client *Client
	ttl    TTLs
	now    func() time.Time

	mu       sync.Mutex
	session  cacheEntry[sessionSnapshot]
	tradable map[string]cacheEntry[TradableRow]
	band     map[string]cacheEntry[AutoRejectPrice]
}

// Gate implements the interface strategy/risk denies by default without.
var _ risk.MarketGate = (*Gate)(nil)

// wib is the exchange's timezone, and it is deliberately an alias rather
// than a second definition: the holiday check must ask "what date is it in
// Jakarta", not "what date is it where this process runs" (the collector
// box runs on UTC, and the two disagree for the seven hours after midnight
// WIB), and engine.LiveSource asks the same question on every reconnect.
// Two copies of a timezone that must agree is one copy too many.
var wib = calendar.Jakarta

// TTL defaults. Each is chosen from how fast the underlying fact can change
// and from what a stale answer would cost:
//
//   - Session phase turns over at fixed intraday boundaries (the 11:30 break,
//     the 16:00 close). 15s bounds how long an order can be admitted into a
//     phase that has just ended — where the only consequence is a Carina
//     rejection, since the exchange enforces the same rule.
//   - Tradability changes when the broker or the exchange halts a name,
//     which can happen intraday. 60s bounds the window in which a freshly
//     suspended name still looks tradable.
//   - The ARA/ARB band is set once per morning and only moves if an
//     instrument is halted and unhalted (reference 06 §4), so 5 minutes is
//     generous and still catches a mid-session reset.
const (
	DefaultSessionTTL  = 15 * time.Second
	DefaultTradableTTL = 60 * time.Second
	DefaultBandTTL     = 5 * time.Minute
)

// TTLs is how long each cached answer stays usable. A zero field takes the
// corresponding default.
type TTLs struct {
	Session  time.Duration
	Tradable time.Duration
	Band     time.Duration
}

func (t TTLs) withDefaults() TTLs {
	if t.Session <= 0 {
		t.Session = DefaultSessionTTL
	}
	if t.Tradable <= 0 {
		t.Tradable = DefaultTradableTTL
	}
	if t.Band <= 0 {
		t.Band = DefaultBandTTL
	}
	return t
}

// GateOptions overrides Gate's defaults. The zero value is correct for
// production; tests inject a fake clock.
type GateOptions struct {
	TTLs  TTLs
	Clock func() time.Time
}

// NewGate builds a Gate. cal and client are both required: without the
// calendar there is no holiday answer, and without the client there are no
// endpoint answers — and in either case the honest construction is
// risk.DenyAllGate, which the caller already has.
func NewGate(cal *calendar.Calendar, client *Client, opts GateOptions) (*Gate, error) {
	if cal == nil {
		return nil, errors.New("market: NewGate: calendar is required")
	}
	if client == nil {
		return nil, errors.New("market: NewGate: client is required")
	}
	now := opts.Clock
	if now == nil {
		now = time.Now
	}
	return &Gate{
		cal:      cal,
		client:   client,
		ttl:      opts.TTLs.withDefaults(),
		now:      now,
		tradable: make(map[string]cacheEntry[TradableRow]),
		band:     make(map[string]cacheEntry[AutoRejectPrice]),
	}, nil
}

// State implements risk.MarketGate.
//
// It returns the per-field verdict together with a non-nil error whenever
// any source could not be consulted. Both halves matter: risk.RiskSink
// rejects on a non-nil error regardless of the state, and the state's
// Unknown fields say which question went unanswered. The two can never
// disagree in the dangerous direction — a field is only Allow when its
// source answered.
func (g *Gate) State(ctx context.Context, symbol string, priceIDR int64) (risk.MarketState, error) {
	d := g.Detail(ctx, symbol, priceIDR)
	return d.State, d.Err()
}

// FieldResult is one MarketState field, with the source that answered it
// and a human-readable reason. Carried so a rejection can say "market_open
// = deny (state_name=STATE_NAME_BREAK)" instead of just "deny".
type FieldResult struct {
	Name   string
	Value  risk.TriState
	Source string
	Reason string
}

func (f FieldResult) String() string {
	return fmt.Sprintf("%-13s %-7s %-34s %s", f.Name, f.Value, f.Source, f.Reason)
}

// SuspensionProxyNote is the Source recorded for NotSuspended. It names the
// package doc's caveat on the value itself, so a log line or a probe report
// carries the limitation without anyone having to know it — the same
// device strategy/account uses for CashInfo.ScopeWarning.
const SuspensionProxyNote = "carina:/stock/tradable (suspension proxy)"

// Detail is everything Gate learned about one prospective order. It is what
// State is derived from, and what cmd/market-probe prints.
type Detail struct {
	Symbol   string
	PriceIDR int64
	At       time.Time
	State    risk.MarketState
	Fields   []FieldResult
	Errs     []error
}

// Err joins every source error into one, or returns nil if every source
// answered. A joined error, rather than the first one, because two
// independent hosts are consulted and "Exodus is down" plus "the trading
// token expired" are different operational problems.
func (d Detail) Err() error {
	switch len(d.Errs) {
	case 0:
		return nil
	case 1:
		return d.Errs[0]
	default:
		msgs := make([]string, 0, len(d.Errs))
		for _, e := range d.Errs {
			msgs = append(msgs, e.Error())
		}
		return errors.New(strings.Join(msgs, "; "))
	}
}

// Allowed reports whether every field came back Allow — the same condition
// risk.RiskSink applies, restated here so the probe cannot drift from it.
func (d Detail) Allowed() bool {
	s := d.State
	return s.NotHoliday == risk.Allow && s.MarketOpen == risk.Allow &&
		s.Tradable == risk.Allow && s.NotSuspended == risk.Allow &&
		s.WithinBand == risk.Allow
}

// Detail answers all five questions and reports how each was decided. It
// never returns early: a failure to reach Carina must not also suppress the
// holiday answer, because an operator reading a rejection needs to see
// everything that was and was not established.
func (g *Gate) Detail(ctx context.Context, symbol string, priceIDR int64) Detail {
	now := g.now()
	d := Detail{Symbol: symbol, PriceIDR: priceIDR, At: now}

	// --- NotHoliday (reference 06 §3) -----------------------------------
	// Offline and authoritative: the curated IDX holiday file (plan 14,
	// ADR-0012). Reference 06 §10 points at GET /holidays/active instead,
	// but that is a backoffice-gateway route this account is not known to
	// reach, and a gate that denies every order when a backoffice endpoint
	// is unavailable would be worse than one that reads the file the whole
	// repo already agrees on.
	today := now.In(wib)
	if g.cal.IsTradingDay(today) {
		d.State.NotHoliday = risk.Allow
		d.add("not_holiday", risk.Allow, "calendar:idx_holidays.json", today.Format("2006-01-02 (Mon)")+" is a trading day")
	} else {
		d.State.NotHoliday = risk.Deny
		d.add("not_holiday", risk.Deny, "calendar:idx_holidays.json", today.Format("2006-01-02 (Mon)")+" is a weekend or IDX holiday")
	}

	// --- MarketOpen (reference 06 §2) -----------------------------------
	snap, err := g.sessionSnapshot(ctx)
	if err != nil {
		d.State.MarketOpen = risk.Unknown
		d.add("market_open", risk.Unknown, "exodus:market-time+session", "could not be established: "+err.Error())
		d.Errs = append(d.Errs, fmt.Errorf("market_open: %w", err))
	} else {
		val, reason := classifyPhase(snap)
		d.State.MarketOpen = val
		d.add("market_open", val, "exodus:market-time+session", reason)
	}

	// --- Tradable and NotSuspended (reference 06 §7) ---------------------
	row, err := g.tradableRow(ctx, symbol)
	if err != nil {
		d.State.Tradable = risk.Unknown
		d.State.NotSuspended = risk.Unknown
		d.add("tradable", risk.Unknown, "carina:/stock/tradable", "could not be established: "+err.Error())
		d.add("not_suspended", risk.Unknown, SuspensionProxyNote, "could not be established: "+err.Error())
		d.Errs = append(d.Errs, fmt.Errorf("tradable: %w", err))
	} else if row.IsTradable {
		d.State.Tradable = risk.Allow
		d.State.NotSuspended = risk.Allow
		d.add("tradable", risk.Allow, "carina:/stock/tradable", "is_tradable=true")
		d.add("not_suspended", risk.Allow, SuspensionProxyNote, "is_tradable=true; no independent ITCH suspend_state is reachable (see package doc)")
	} else {
		d.State.Tradable = risk.Deny
		d.State.NotSuspended = risk.Deny
		d.add("tradable", risk.Deny, "carina:/stock/tradable", "is_tradable=false")
		d.add("not_suspended", risk.Deny, SuspensionProxyNote, "is_tradable=false — halted, suspended, or not permitted for this account")
	}

	// --- WithinBand (reference 06 §4) ------------------------------------
	band, err := g.autoRejectPrice(ctx, symbol)
	switch {
	case err != nil:
		d.State.WithinBand = risk.Unknown
		d.add("within_band", risk.Unknown, "carina:/nego-engine/v1/order-book", "could not be established: "+err.Error())
		d.Errs = append(d.Errs, fmt.Errorf("within_band: %w", err))
	case !band.usable():
		// A missing or inverted band is not an unbounded one.
		d.State.WithinBand = risk.Unknown
		d.add("within_band", risk.Unknown, "carina:/nego-engine/v1/order-book",
			fmt.Sprintf("unusable band below=%d above=%d (proto marks both required and >0)", band.Below, band.Above))
		d.Errs = append(d.Errs, fmt.Errorf("within_band: unusable band below=%d above=%d", band.Below, band.Above))
	case priceIDR <= 0:
		// Defence in depth: risk.checkStructural already rejects a
		// non-positive price before the gate is consulted, so reaching here
		// means the caller is not RiskSink. Answer honestly rather than
		// comparing a nonsense price against a real band.
		d.State.WithinBand = risk.Unknown
		d.add("within_band", risk.Unknown, "carina:/nego-engine/v1/order-book",
			fmt.Sprintf("price %d IDR is not a price", priceIDR))
	case band.contains(priceIDR):
		d.State.WithinBand = risk.Allow
		d.add("within_band", risk.Allow, "carina:/nego-engine/v1/order-book",
			fmt.Sprintf("ARB %d <= %d <= ARA %d", band.Below, priceIDR, band.Above))
	default:
		d.State.WithinBand = risk.Deny
		d.add("within_band", risk.Deny, "carina:/nego-engine/v1/order-book",
			fmt.Sprintf("price %d IDR outside ARB %d .. ARA %d", priceIDR, band.Below, band.Above))
	}

	return d
}

func (d *Detail) add(name string, v risk.TriState, source, reason string) {
	d.Fields = append(d.Fields, FieldResult{Name: name, Value: v, Source: source, Reason: reason})
}

// classifyPhase turns the two market-time endpoints into one verdict.
//
// Both must agree before an order is admitted. They are cheap, they are
// independent routes, and requiring both means a single endpoint answering
// stale or wrong cannot open the gate on its own. The asymmetry is
// deliberate: either source saying "closed" is enough to deny, but only
// both saying "open" is enough to allow.
//
// A phase name the exchange has not published (or an UNSPECIFIED status)
// resolves to Unknown, never to a confident Deny, so that a log line reads
// "I did not recognise this" rather than asserting a closure that was never
// established.
func classifyPhase(s sessionSnapshot) (risk.TriState, string) {
	phase := s.session.Detail.Regular.StateName
	status := s.marketTime.Market.Status
	desc := fmt.Sprintf("state_name=%s market.status=%s", phase, status)

	switch {
	case phase.acceptsRegularOrders() && status == StatusOpen:
		return risk.Allow, desc + " (regular session, market open)"
	case status == StatusClose:
		return risk.Deny, desc + " (market reports closed)"
	case phase.isKnownClosedPhase():
		return risk.Deny, desc + " (phase does not accept regular orders)"
	default:
		return risk.Unknown, desc + " (unrecognised combination — neither confirmed open nor confirmed closed)"
	}
}

// sessionSnapshot is the pair of market-time reads that together answer
// MarketOpen. They share one cache entry because they are consulted
// together and are meaningless apart.
type sessionSnapshot struct {
	marketTime MarketTimeData
	session    SessionData
}

func (g *Gate) sessionSnapshot(ctx context.Context) (sessionSnapshot, error) {
	g.mu.Lock()
	entry := g.session
	g.mu.Unlock()
	if fresh(entry, g.now()) {
		return entry.val, nil
	}

	mt, err := g.client.MarketTime(ctx)
	if err != nil {
		return sessionSnapshot{}, err
	}
	sd, err := g.client.SessionDetail(ctx)
	if err != nil {
		return sessionSnapshot{}, err
	}
	snap := sessionSnapshot{marketTime: mt, session: sd}

	g.mu.Lock()
	g.session = cacheEntry[sessionSnapshot]{val: snap, at: g.now(), ttl: g.ttl.Session, ok: true}
	g.mu.Unlock()
	return snap, nil
}

func (g *Gate) tradableRow(ctx context.Context, symbol string) (TradableRow, error) {
	key := strings.ToUpper(symbol)
	g.mu.Lock()
	entry := g.tradable[key]
	g.mu.Unlock()
	if fresh(entry, g.now()) {
		return entry.val, nil
	}

	row, err := g.client.Tradable(ctx, symbol)
	if err != nil {
		return TradableRow{}, err
	}

	g.mu.Lock()
	g.tradable[key] = cacheEntry[TradableRow]{val: row, at: g.now(), ttl: g.ttl.Tradable, ok: true}
	g.mu.Unlock()
	return row, nil
}

func (g *Gate) autoRejectPrice(ctx context.Context, symbol string) (AutoRejectPrice, error) {
	key := strings.ToUpper(symbol)
	g.mu.Lock()
	entry := g.band[key]
	g.mu.Unlock()
	if fresh(entry, g.now()) {
		return entry.val, nil
	}

	band, err := g.client.AutoRejectPrice(ctx, symbol)
	if err != nil {
		return AutoRejectPrice{}, err
	}

	g.mu.Lock()
	g.band[key] = cacheEntry[AutoRejectPrice]{val: band, at: g.now(), ttl: g.ttl.Band, ok: true}
	g.mu.Unlock()
	return band, nil
}

// cacheEntry is one cached answer and the instant it was obtained.
type cacheEntry[T any] struct {
	val T
	at  time.Time
	ttl time.Duration
	ok  bool
}

// fresh reports whether e may still be served.
//
// The whole staleness policy is here, and it has exactly one rule: an entry
// past its TTL is not served — not as a fallback, not when the refetch
// fails, not ever. Every caller above refetches on !fresh and returns the
// fetch error if that fails, so a stale value can never become an answer.
//
// A negative age (the entry is stamped in the future) is also treated as
// stale. That is not a theoretical case: an injected clock in a test, or a
// host clock stepped backwards by NTP, would otherwise pin an entry as
// fresh for the length of the jump.
func fresh[T any](e cacheEntry[T], now time.Time) bool {
	if !e.ok {
		return false
	}
	age := now.Sub(e.at)
	return age >= 0 && age < e.ttl
}
