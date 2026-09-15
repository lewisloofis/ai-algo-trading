package engine

import (
	"context"
	"errors"
	"fmt"
	"log"
	"sync"
	"time"

	"collector/calendar"
	"collector/feed"
	"collector/segment"
)

// LiveConn is the transport surface LiveSource needs from one live
// connection. *collector/feed.WsConn satisfies it directly (see the
// compile-time assertion below) — LiveSource does not implement a second
// WebSocket client, only this narrower, read-only view of the one collector
// already speaks in production.
//
// Close must unblock any ReadMessage call already in flight, exactly as
// closing a real socket unblocks a pending read (standard net.Conn
// behaviour, which *feed.WsConn gets for free from its underlying *tls.Conn).
// LiveSource relies on this contract to honour ctx cancellation promptly
// instead of waiting out a read deadline.
type LiveConn interface {
	// ReadMessage returns the next complete WS message (mirrors
	// feed.WsConn.ReadMessage). A non-nil error means the connection is
	// broken or closed; LiveSource treats it as a disconnect.
	ReadMessage() (opcode byte, data []byte, err error)

	// SetReadDeadline bounds how long the next ReadMessage may block, so a
	// TCP-alive-but-silent feed is still detected as broken. Matches
	// feed.WsConn.SetReadDeadline's signature exactly.
	SetReadDeadline(t time.Time)

	// Close releases the connection and must unblock any in-flight
	// ReadMessage call.
	Close() error
}

// compile-time assertion: the real transport satisfies LiveConn with no
// adapter code required.
var _ LiveConn = (*feed.WsConn)(nil)

// Dialer performs one connection attempt: authenticate if needed, dial the
// transport, and send the subscribe frame — mirroring the connect+subscribe
// sequence in collector/cmd/collector's runOnce. Each call is exactly one
// attempt; LiveSource is the layer that retries a failed or broken
// connection with backoff, so Dialer implementations should not retry
// internally. See NewWSDialer (live_ws.go) for the production implementation
// built on collector/feed; tests inject a synthetic Dialer so no test opens
// a socket.
type Dialer func(ctx context.Context) (LiveConn, error)

// ErrMarketClosed is returned by LiveSource when the calendar says today is
// not an IDX trading day (weekend or holiday). LiveSource refuses to start
// rather than opening a channel it immediately closes: a channel that is
// closed with zero events is exactly the "market closed" vs. "feed broken"
// ambiguity plan 14 §1 names as one of the three problems the calendar
// reader exists to solve, and a bare closed channel gives a caller no way to
// tell those two states apart. errors.Is(err, ErrMarketClosed) is how a
// caller distinguishes "not trading today" from every other failure mode.
var ErrMarketClosed = errors.New("live source: today is not an IDX trading day")

const (
	// liveReconnectMin and liveReconnectMax match
	// collector/cmd/collector's reconnectMin/reconnectMax (and the root
	// prototype's strategy/main.go copy of the same constants) exactly: the
	// same capped exponential backoff schedule against the same feed.
	liveReconnectMin = 750 * time.Millisecond
	liveReconnectMax = 3 * time.Second

	// liveReadDeadline matches collector/cmd/collector's readDeadline: a
	// connection that is TCP-alive but has gone silent (no data, no pong)
	// for this long is treated as broken and reconnected, exactly as the
	// collector treats it.
	liveReadDeadline = 60 * time.Second
)

// liveSourceOptions carries LiveSource's time seams. Production always uses
// defaultLiveSourceOptions (the real clock and a real, cancellable sleep);
// tests substitute a fake clock and a non-blocking or channel-synchronised
// sleep so reconnect/backoff tests are deterministic and instant instead of
// waiting out real backoff durations.
type liveSourceOptions struct {
	now        func() time.Time
	sleep      func(ctx context.Context, d time.Duration)
	minBackoff time.Duration
	maxBackoff time.Duration
}

func defaultLiveSourceOptions() liveSourceOptions {
	return liveSourceOptions{
		now:        time.Now,
		sleep:      waitOrCancel,
		minBackoff: liveReconnectMin,
		maxBackoff: liveReconnectMax,
	}
}

// waitOrCancel sleeps for d, or returns early if ctx is cancelled first.
func waitOrCancel(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

// LiveSource streams MarketEvents from a live connection established by
// dial, mirroring ReplaySource's contract: the returned channel is closed
// when the feed ends or ctx is cancelled. Unlike ReplaySource, LiveSource
// also returns an error — one that can fire before the channel is ever
// created, when cal says today is not a trading day. That extra return is a
// deliberate divergence from ReplaySource's signature: see ErrMarketClosed's
// doc for why a channel alone cannot safely carry that distinction.
//
// dial is retried, with capped exponential backoff (liveReconnectMin..Max,
// matching collector/cmd/collector), for as long as ctx is not cancelled.
// Every reconnect (i.e. every dial call after the connection has broken) is
// preceded by an EventControl carrying Control.Event == "RECONNECT" —
// written at the moment the disconnect is detected, before the backoff wait,
// exactly when and in the same shape collector/cmd/collector writes the
// segment control record of the same name. This is what makes
// PaperSink.Reset("") fire identically whether a strategy is replaying a
// captured session or running against this live one (ADR-0013 parity).
// Per-symbol order-book invalidations (Control.Event == "SWITCH_RESET") are
// detected on the same hot path the collector uses (feed.DetectSwitchReset),
// with the same per-symbol dedup, so a sustained reset does not repeat.
//
// cal is required: LiveSource must not silently trade on a day the market is
// closed (see ErrMarketClosed). dial is required.
//
// KNOWN GAP, and a prerequisite for B4's live routing: the calendar is
// consulted once, at start, and is NOT re-checked on reconnect. A process
// left running across midnight into a holiday keeps streaming instead of
// stopping. Nothing should arrive on a closed day, and today no order can be
// routed from here at all (--sink paper is the only sink), so the exposure is
// currently nil — but a long-lived process is exactly the shape B4 will have,
// and at that point this must become a per-reconnect check.
func LiveSource(ctx context.Context, dial Dialer, cal *calendar.Calendar) (<-chan MarketEvent, error) {
	return liveSource(ctx, dial, cal, defaultLiveSourceOptions())
}

func liveSource(ctx context.Context, dial Dialer, cal *calendar.Calendar, opts liveSourceOptions) (<-chan MarketEvent, error) {
	if dial == nil {
		return nil, errors.New("live source: dial is required")
	}
	if cal == nil {
		return nil, errors.New("live source: calendar is required (see ErrMarketClosed)")
	}
	if !cal.IsTradingDay(opts.now()) {
		return nil, ErrMarketClosed
	}

	ch := make(chan MarketEvent, 256)
	go runLiveSource(ctx, dial, ch, opts)
	return ch, nil
}

// runLiveSource is the reconnect loop: dial, pump events until the
// connection breaks, emit RECONNECT, back off, repeat — until ctx is
// cancelled. It mirrors collector/cmd/collector's runWithReconnect.
func runLiveSource(ctx context.Context, dial Dialer, ch chan<- MarketEvent, opts liveSourceOptions) {
	defer close(ch)

	backoff := opts.minBackoff
	attempt := 0

	for ctx.Err() == nil {
		err := runLiveConnOnce(ctx, dial, ch, opts)
		if ctx.Err() != nil {
			return
		}
		if err == nil {
			// runLiveConnOnce returns nil only when ctx is cancelled (checked
			// above); reaching here with a nil error would be a bug in this
			// function rather than a real disconnect. Treat it as one anyway
			// so the loop still makes forward progress instead of spinning.
			err = errors.New("live source: connection ended without error or cancellation")
		}
		log.Printf("live source: disconnected: %v — reconnecting in %s", err, backoff)

		ctrl := &segment.ControlEvent{Event: "RECONNECT", Attempt: attempt, BackoffMs: int(backoff.Milliseconds())}
		if !sendEvent(ctx, ch, MarketEvent{RecvNs: opts.now().UnixNano(), Kind: EventControl, Control: ctrl}) {
			return
		}

		opts.sleep(ctx, backoff)
		if ctx.Err() != nil {
			return
		}

		backoff *= 2
		if backoff > opts.maxBackoff {
			backoff = opts.maxBackoff
		}
		attempt++
	}
}

// runLiveConnOnce performs one connection attempt via dial and pumps decoded
// events to ch until the connection breaks or ctx is cancelled. A nil return
// means ctx was cancelled; any other return is a connection error the caller
// should treat as "reconnect".
func runLiveConnOnce(ctx context.Context, dial Dialer, ch chan<- MarketEvent, opts liveSourceOptions) error {
	conn, err := dial(ctx)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}

	// Per-symbol switch_reset dedup, scoped to THIS connection — created
	// here, not in the reconnect loop, because that is where
	// collector/cmd/collector's runOnce creates its own (main.go, just after
	// DialWS). A map carried across reconnects would suppress the first
	// SWITCH_RESET on a new connection for any symbol that was still in
	// reset when the previous one dropped, so a live run and a replay of a
	// capture of that same session would disagree — the one thing this file
	// exists to prevent. Covered by
	// TestLiveSource_SwitchResetDedupResetsPerConnection.
	inReset := make(map[string]bool)

	// Force-unblock a pending ReadMessage on ctx cancellation. A real socket
	// unblocks a pending read when closed; LiveConn's contract requires the
	// same (see its doc), so this is what lets the read loop below return
	// promptly instead of waiting out liveReadDeadline when the caller
	// cancels ctx.
	var closeOnce sync.Once
	closeConn := func() { closeOnce.Do(func() { conn.Close() }) }
	stopWatcher := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			closeConn()
		case <-stopWatcher:
		}
	}()
	defer func() {
		close(stopWatcher)
		closeConn()
	}()

	for {
		if ctx.Err() != nil {
			return nil
		}
		conn.SetReadDeadline(opts.now().Add(liveReadDeadline))
		opcode, data, err := conn.ReadMessage()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("read: %w", err)
		}
		if opcode != feed.OpBinary {
			continue
		}
		recvNs := opts.now().UnixNano()

		// switch_reset detection: cheap partial decode, only on orderbook
		// frames. Emit at most one SWITCH_RESET control record per symbol
		// per reset transition — mirrors collector/cmd/collector's hot path
		// exactly, including the per-symbol dedup map.
		if sym, isReset := feed.DetectSwitchReset(data); sym != "" {
			if isReset && !inReset[sym] {
				ctrl := &segment.ControlEvent{Event: "SWITCH_RESET", Symbol: sym}
				if !sendEvent(ctx, ch, MarketEvent{RecvNs: recvNs, Kind: EventControl, Control: ctrl}) {
					return nil
				}
			}
			inReset[sym] = isReset
		}

		decoded, err := feed.DecodeWrap(data)
		if err != nil {
			log.Printf("live source: decode error: %v", err)
			continue
		}
		for _, evt := range marketEventsFromDecoded(recvNs, decoded) {
			if !sendEvent(ctx, ch, evt) {
				return nil
			}
		}
	}
}

// sendEvent sends evt on ch, or reports false if ctx is cancelled first.
func sendEvent(ctx context.Context, ch chan<- MarketEvent, evt MarketEvent) bool {
	select {
	case ch <- evt:
		return true
	case <-ctx.Done():
		return false
	}
}

// marketEventsFromDecoded converts one decoded live frame into the
// MarketEvents it represents. It mirrors collector/replay's (unexported)
// decodeRecord/dataKind for the data-record case exactly, so a live run and
// a replay of a captured segment emit the same event shapes for the same
// wire bytes. Control records have no live equivalent here: RECONNECT and
// SWITCH_RESET are constructed directly by the caller (runLiveSource /
// runLiveConnOnce), since a live source has no segment.Record to unmarshal.
func marketEventsFromDecoded(recvNs int64, d feed.Decoded) []MarketEvent {
	kind := liveEventKind(d.Kind)

	// Expand running_trade batches: emit one event per trade, exactly as
	// collector/replay's decodeRecord does, so per-symbol logic sees the
	// same event granularity live and in replay.
	if kind == EventRunningTrade && len(d.Trades) > 0 {
		evts := make([]MarketEvent, 0, len(d.Trades))
		for i := range d.Trades {
			tr := d.Trades[i]
			single := feed.Decoded{
				Kind:   "running_trade",
				Symbol: tr.Symbol,
				Trades: []feed.Trade{tr},
			}
			evts = append(evts, MarketEvent{
				RecvNs:  recvNs,
				Kind:    EventRunningTrade,
				Symbol:  tr.Symbol,
				Decoded: &single,
			})
		}
		return evts
	}

	dd := d
	return []MarketEvent{{RecvNs: recvNs, Kind: kind, Symbol: d.Symbol, Decoded: &dd}}
}

// liveEventKind maps a feed.Decoded.Kind string to engine.EventKind. This is
// a deliberate duplicate of collector/replay's unexported dataKind — the two
// must be kept in sync (same five cases) — because it is exactly this
// mapping that makes a live event and a replayed segment event compare equal
// in Kind for the same wire frame.
func liveEventKind(kind string) EventKind {
	switch kind {
	case "liveprice":
		return EventLivePrice
	case "orderbook":
		return EventOrderbook
	case "running_trade":
		return EventRunningTrade
	case "iepiev":
		return EventIEPIEV
	default:
		// "ping", "error", "other" — keep the raw string as EventKind so
		// callers can still filter on evt.Kind if needed, same as replay.
		return EventKind(kind)
	}
}
