package live

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"collector/calendar"
	"strategy/engine"
)

// Default poll cadence and timeouts (Options fields fall back to these when
// left zero). See NewSink.
const (
	DefaultPollInterval = 3 * time.Second
	DefaultAckTimeout   = 30 * time.Second
	DefaultOpenTimeout  = 5 * time.Minute
)

// submitHTTPTimeout bounds the one HTTP POST Submit makes. Submit runs on
// engine.Run's single event-loop goroutine (Strategy.OnEvent's documented
// contract), so it must not block on anything slow — see Sink's doc
// comment. cancelHTTPTimeout bounds every Cancel call, whether issued by a
// caller or by the poller on an OpenTimeout breach. The poller's own
// GET /order/v2/list calls are bounded separately, by pollHTTPTimeout
// (poller.go) — capped at PollInterval so one slow poll cannot run long
// enough to overlap the next tick.
const (
	submitHTTPTimeout = 10 * time.Second
	cancelHTTPTimeout = 10 * time.Second
)

// Sink is the engine.OrderSink this package builds: Submit places a real
// order and returns immediately (the client order id), and fills arrive
// later on the channel Fills returns, produced by a poller goroutine
// started by Start. It also implements the unexported sinkKinder/asyncSink
// interfaces strategy/risk and strategy/engine duck-type against — see
// SinkKind and Fills.
//
// Submit/Cancel run on the caller's goroutine (engine.Run's single event
// loop); the poller started by Start runs on its own. Every field below
// mu guards is reachable from both, so all of it is accessed only while
// holding mu — except during the HTTP calls and the channel send
// themselves, which release mu first (see pollOnce and Submit): holding a
// lock across a network call or a blocking channel send would let a slow
// broker or a slow consumer stall the other side's access to the order
// table, and in Submit's case would deadlock outright, since engine.Run's
// event loop is the only goroutine that ever drains Fills().
type Sink struct {
	client       *Client
	pollInterval time.Duration
	ackTimeout   time.Duration
	openTimeout  time.Duration
	clock        func() time.Time
	logf         func(string, ...any)

	mu       sync.Mutex
	orders   map[string]*trackedOrder // clientOrderID -> order
	byBroker map[string]string        // brokerOrderID -> clientOrderID
	uiRefs   map[string]string        // clientOrderID -> ui_ref (see uiref.go)
	baseline map[string]struct{}      // brokerOrderID set present before this run started (see Baseline)

	fills     chan engine.AsyncFill
	done      chan struct{}
	closeOnce sync.Once
	wg        sync.WaitGroup
}

// trackedOrder is Order plus the housekeeping Sink itself needs that has no
// business being part of the public record: whether an AckTimeout alert has
// already fired (so it fires once, not every poll tick) and when the order
// first became OPEN/PARTIAL (OpenTimeout's clock starts there, not at
// submission — an order can legitimately sit in StateSending for a while
// before IDX acks it).
type trackedOrder struct {
	Order
	ackAlerted      bool
	openSince       time.Time
	cancelRequested bool // OpenTimeout cancel already issued; do not resend every poll tick
}

// Options configures NewSink. Only Client is required; every other field
// falls back to a documented default when left zero.
type Options struct {
	Client *Client

	// PollInterval defaults to DefaultPollInterval.
	PollInterval time.Duration
	// AckTimeout defaults to DefaultAckTimeout: an order with no
	// OPEN/terminal state within this long of submission is logged as an
	// alert, not cancelled — see checkTimeouts' doc comment for why
	// cancelling here would be unsafe.
	AckTimeout time.Duration
	// OpenTimeout defaults to DefaultOpenTimeout: an order still resting
	// (OPEN or PARTIAL) this long after first becoming so is cancelled.
	OpenTimeout time.Duration
	// Clock defaults to time.Now. Tests inject a fixed or steppable clock.
	Clock func() time.Time
	// Logf receives every non-fatal event the poller or Submit/Cancel want
	// surfaced: alerts, classification fallbacks, cancel failures. It must
	// never receive a token — see structural_test.go. Defaults to a no-op.
	Logf func(string, ...any)
}

// NewSink validates opts and returns a Sink ready for Start.
func NewSink(opts Options) (*Sink, error) {
	if opts.Client == nil {
		return nil, errors.New("live: NewSink: Client is required")
	}
	pollInterval := opts.PollInterval
	if pollInterval <= 0 {
		pollInterval = DefaultPollInterval
	}
	ackTimeout := opts.AckTimeout
	if ackTimeout <= 0 {
		ackTimeout = DefaultAckTimeout
	}
	openTimeout := opts.OpenTimeout
	if openTimeout <= 0 {
		openTimeout = DefaultOpenTimeout
	}
	clock := opts.Clock
	if clock == nil {
		clock = time.Now
	}
	logf := opts.Logf
	if logf == nil {
		logf = func(string, ...any) {}
	}

	return &Sink{
		client:       opts.Client,
		pollInterval: pollInterval,
		ackTimeout:   ackTimeout,
		openTimeout:  openTimeout,
		clock:        clock,
		logf:         logf,
		orders:       make(map[string]*trackedOrder),
		byBroker:     make(map[string]string),
		uiRefs:       make(map[string]string),
		baseline:     make(map[string]struct{}),
		fills:        make(chan engine.AsyncFill),
		done:         make(chan struct{}),
	}, nil
}

// SinkKind names this sink for the post-trade store's sink_kind column
// (strategy/risk/forwarding.go's sinkKinder).
func (s *Sink) SinkKind() string { return "live" }

// Fills returns the channel AsyncFills are delivered on. It is closed by
// Close, once the poller goroutine has fully stopped — see engine.Run's
// contract for asyncSink: a nil channel (before Start) blocks forever in
// engine.Run's select, which is exactly the paper-sink behaviour of "no
// async fills are coming".
func (s *Sink) Fills() <-chan engine.AsyncFill { return s.fills }

// Submit places req as a regular day limit order and returns the CLIENT
// order id (intent.OrderID) — see this package's doc comment for why that,
// and not Carina's broker order id, is what every caller must key on.
//
// Submit does exactly one thing that can block: the HTTP POST, bounded by
// submitHTTPTimeout. It does not tick-snap, size, or cap intent — that is
// strategy/risk's and the strategy's job; an off-tick price or over-cap
// size reaching here is a bug upstream, and Client.Place surfaces Carina's
// resulting 4xx rather than silently rounding it away.
func (s *Sink) Submit(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	if intent.OrderID == "" {
		return "", errors.New("live: Submit: OrderIntent.OrderID (the client order id) is required")
	}
	if intent.Symbol == "" {
		return "", fmt.Errorf("live: Submit %s: Symbol is required", intent.OrderID)
	}
	if intent.Lots <= 0 {
		return "", fmt.Errorf("live: Submit %s: Lots must be positive, got %d", intent.OrderID, intent.Lots)
	}

	s.mu.Lock()
	uiRef, seen := s.uiRefs[intent.OrderID]
	if !seen {
		// DeterministicUIRef, not NewUIRef: see its doc comment. Keying on
		// the client order id and today's trading day means a same-day
		// restart's resubmission (a fresh Sink, empty s.uiRefs) mints the
		// IDENTICAL ui_ref this process would have, so Carina's own
		// idempotency guard — not this map, which a crash just erased — is
		// what actually stops the double-submit.
		uiRef = DeterministicUIRef(intent.OrderID, s.tradingDay())
		s.uiRefs[intent.OrderID] = uiRef
	}
	s.mu.Unlock()

	req := PlaceRequest{
		UIRef:     uiRef,
		Symbol:    intent.Symbol,
		Side:      intent.Side,
		PriceIDR:  intent.PriceIDR,
		Shares:    int64(intent.Lots) * sharesPerLot,
		BoardType: string(intent.BoardType),
	}

	placeCtx, cancel := context.WithTimeout(ctx, submitHTTPTimeout)
	brokerID, err := s.client.Place(placeCtx, req)
	cancel()

	if err != nil {
		if !errors.Is(err, ErrDuplicateOrder) {
			return "", err
		}
		brokerID, err = s.resolveDuplicate(ctx, intent)
		if err != nil {
			return "", err
		}
	}

	now := s.clock()
	s.mu.Lock()
	s.orders[intent.OrderID] = &trackedOrder{
		Order: Order{
			ClientOrderID:    intent.OrderID,
			UIRef:            uiRef,
			BrokerOrderID:    brokerID,
			Symbol:           intent.Symbol,
			Side:             intent.Side,
			DecisionPriceIDR: intent.PriceIDR,
			LotsRequested:    intent.Lots,
			State:            StateSending,
			SubmittedAt:      now,
			UpdatedAt:        now,
		},
	}
	s.byBroker[brokerID] = intent.OrderID
	s.mu.Unlock()

	return engine.OrderID(intent.OrderID), nil
}

// tradingDay renders s.clock() as a Jakarta ("2006-01-02") calendar date —
// the same zone engine.LiveSource's holiday check and market.Gate's session
// check use, and for the same reason: the box this runs on may be UTC,
// which disagrees with WIB for the seven hours after Jakarta midnight. Used
// only to derive a deterministic ui_ref (see DeterministicUIRef); it is not
// a trading-calendar lookup and does not need one — a Saturday's "trading
// day" string is a harmless, unambiguous input for a Submit that should not
// be happening then anyway.
func (s *Sink) tradingDay() string {
	return s.clock().In(calendar.Jakarta).Format("2006-01-02")
}

// resolveDuplicate adopts the order Carina says already exists for this
// ui_ref, rather than treating ErrDuplicateOrder as a failure
// (docs/reference/04-order-lifecycle.md §5.3: ErrorDuplicateOrder is the
// idempotency guard firing on a resubmitted ui_ref — the safe response is
// to find the order that already resulted from the first attempt, not
// error out from underneath a strategy that has already reserved this
// order's lots as in-flight).
//
// GUESS, flagged for review: there is no captured example of Carina's
// actual ErrorDuplicateOrder response body, so it is not known whether it
// names the pre-existing order_id directly (which would make this trivial).
// Lacking that, and since GET /order/v2/list's confirmed field set
// (strategy/account.Order's doc comment) does NOT include ui_ref, this
// matches List() rows by symbol + side + price + shares among orders this
// Sink does not already track. Zero matches or more than one is reported
// as an error rather than guessed at — matching this repo's rule that an
// ambiguous or absent answer must not read as a confident one. This is
// accepted as adequate for B4 because strategy/strategies/rebalance mints
// exactly one order per (symbol, side) per run, so two distinct client
// orders sharing every one of these fields should not occur in practice;
// B5's execution slicer, which may place repeated same-parameter clips,
// will need to revisit this — ideally with a real captured duplicate-order
// response to replace the heuristic outright.
//
// A candidate is also excluded when it is in s.baseline — present before
// this run started (see Baseline). A ui_ref collision can only mean "this
// exact action, minted by this run, was already sent once"; it can never
// mean "a pre-existing order happens to match," and List() is not
// account-scoped (this package's doc comment, plan 18 §3.4), so a
// coincidentally matching baseline row is not hypothetical — it is exactly
// as likely to be the core book's own resting order as it is to be
// anything of the sleeve's. Adopting it would make this Sink believe a
// core-book order belongs to the sleeve, corrupting every fill and
// reconciliation this package produces for it from that point on. Note this
// only protects a run that called Baseline before submitting anything; see
// Baseline's doc comment.
func (s *Sink) resolveDuplicate(ctx context.Context, intent engine.OrderIntent) (string, error) {
	orders, err := s.client.List(ctx)
	if err != nil {
		return "", fmt.Errorf("live: resolve duplicate for %s: %w", intent.OrderID, err)
	}
	wantShares := float64(intent.Lots * sharesPerLot)

	s.mu.Lock()
	defer s.mu.Unlock()

	var match string
	matches := 0
	for _, o := range orders {
		if !strings.EqualFold(o.Symbol, intent.Symbol) {
			continue
		}
		if !sideMatchesSnapshot(intent.Side, o.Side) {
			continue
		}
		if o.Qty.LotTotal*sharesPerLot != wantShares {
			continue
		}
		if int64(o.Price.Order) != intent.PriceIDR {
			continue
		}
		if _, alreadyTracked := s.byBroker[o.OrderID]; alreadyTracked {
			continue
		}
		if _, wasBaseline := s.baseline[o.OrderID]; wasBaseline {
			continue
		}
		match = o.OrderID
		matches++
	}

	switch matches {
	case 0:
		return "", fmt.Errorf("live: %w: %s: no matching untracked order found in List()", ErrDuplicateOrder, intent.OrderID)
	case 1:
		return match, nil
	default:
		return "", fmt.Errorf("live: %w: %s: %d ambiguous candidates in List() (identical symbol/side/price/shares) — refusing to guess", ErrDuplicateOrder, intent.OrderID, matches)
	}
}

// sideMatchesSnapshot compares engine's "buy"/"sell" convention against
// Carina's "SIDE_BUY"/"SIDE_SELL" (Snapshot.Side's confirmed values).
func sideMatchesSnapshot(engineSide, snapshotSide string) bool {
	switch engineSide {
	case "buy":
		return snapshotSide == "SIDE_BUY"
	case "sell":
		return snapshotSide == "SIDE_SELL"
	default:
		return false
	}
}

// Cancel withdraws id (the client order id). Cancelling an order already in
// a terminal state is a no-op, not an error: the poller — not Cancel — is
// the only writer of Order.State, so by the time a caller's Cancel call
// observes a terminal state, there is nothing left to withdraw.
func (s *Sink) Cancel(ctx context.Context, id engine.OrderID) error {
	clientID := string(id)

	s.mu.Lock()
	t, ok := s.orders[clientID]
	var brokerID string
	var state State
	if ok {
		brokerID, state = t.BrokerOrderID, t.State
	}
	s.mu.Unlock()

	if !ok {
		return fmt.Errorf("live: Cancel: unknown client order id %q", clientID)
	}
	if IsTerminal(state) {
		return nil
	}

	cancelCtx, cancel := context.WithTimeout(ctx, cancelHTTPTimeout)
	defer cancel()
	if err := s.client.Cancel(cancelCtx, brokerID, NewUIRef()); err != nil {
		return fmt.Errorf("live: Cancel %s: %w", clientID, err)
	}
	return nil
}

// Start launches the poller goroutine. It must be called at most once per
// Sink; Close stops it and closes Fills().
func (s *Sink) Start(ctx context.Context) {
	s.wg.Add(1)
	go s.pollLoop(ctx)
}

// Close stops the poller and closes the channel Fills() returns. Safe to
// call more than once or concurrently: sync.Once ensures the shutdown
// sequence — signal the poller, wait for it to actually stop, then close
// fills — runs exactly once, and every caller blocks until it has
// completed, so no caller can observe Fills() as "not yet closed" after
// Close returns.
func (s *Sink) Close() error {
	s.closeOnce.Do(func() {
		close(s.done)
		s.wg.Wait()
		close(s.fills)
	})
	return nil
}
