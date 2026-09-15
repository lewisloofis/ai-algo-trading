package engine

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"sync"
	"testing"
	"time"

	"collector/calendar"
	"collector/feed"
	"collector/sbproto"
)

// ---------- synthetic transport (no network) ----------

// fakeFrame is one queued ReadMessage result for fakeConn.
type fakeFrame struct {
	opcode byte
	data   []byte
	err    error
}

// fakeConn is a synthetic LiveConn. Queued frames are returned in order;
// once exhausted, ReadMessage blocks (like a real idle socket) until either
// a new frame is pushed or Close is called — which unblocks it with an
// error, matching real socket semantics (closing an fd unblocks a pending
// read).
type fakeConn struct {
	mu          sync.Mutex
	cond        *sync.Cond
	frames      []fakeFrame
	closed      bool
	closeCalls  int
	blockedOnce sync.Once
	blockedCh   chan struct{}
}

func newFakeConn(frames ...fakeFrame) *fakeConn {
	c := &fakeConn{frames: frames, blockedCh: make(chan struct{})}
	c.cond = sync.NewCond(&c.mu)
	return c
}

// push appends a frame and wakes any blocked reader.
func (c *fakeConn) push(f fakeFrame) {
	c.mu.Lock()
	c.frames = append(c.frames, f)
	c.cond.Broadcast()
	c.mu.Unlock()
}

func (c *fakeConn) ReadMessage() (byte, []byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for len(c.frames) == 0 && !c.closed {
		c.blockedOnce.Do(func() { close(c.blockedCh) })
		c.cond.Wait()
	}
	if len(c.frames) == 0 {
		return 0, nil, errors.New("fakeConn: closed")
	}
	f := c.frames[0]
	c.frames = c.frames[1:]
	return f.opcode, f.data, f.err
}

func (c *fakeConn) SetReadDeadline(time.Time) {}

func (c *fakeConn) Close() error {
	c.mu.Lock()
	c.closed = true
	c.closeCalls++
	c.cond.Broadcast()
	c.mu.Unlock()
	return nil
}

func (c *fakeConn) closeCallCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.closeCalls
}

// waitBlocked waits until the fake connection has entered (or is about to
// enter) its blocking read state, so tests can cancel ctx deterministically
// instead of racing a real goroutine schedule.
func (c *fakeConn) waitBlocked(t *testing.T, timeout time.Duration) {
	t.Helper()
	select {
	case <-c.blockedCh:
	case <-time.After(timeout):
		t.Fatal("fakeConn: timed out waiting for a blocked reader")
	}
}

// fakeDialer is a synthetic Dialer. fn is called once per dial attempt with
// a zero-based call counter, so tests can return different connections (or
// errors) on successive attempts.
type fakeDialer struct {
	mu    sync.Mutex
	calls int
	fn    func(call int) (LiveConn, error)
}

func (f *fakeDialer) dial(ctx context.Context) (LiveConn, error) {
	f.mu.Lock()
	call := f.calls
	f.calls++
	f.mu.Unlock()
	return f.fn(call)
}

func (f *fakeDialer) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

// ---------- synthetic frame builders ----------

// buildOrderbookFrame builds a WebsocketWrapMessageChannel orderbook frame
// (field 10) carrying stock_code (field 1) and switch_reset (field 6),
// mirroring collector/feed/switchreset_test.go's buildOrderbookWrap exactly
// — this is a real wire-format frame, decoded by the real feed.DecodeWrap
// and feed.DetectSwitchReset, not a stand-in.
func buildOrderbookFrame(symbol string, switchReset int) []byte {
	var ob sbproto.Writer
	ob.String(1, symbol)
	ob.Tag(6, sbproto.WireVarint)
	ob.Varint(uint64(switchReset))

	var wrap sbproto.Writer
	wrap.Message(10, ob.Bytes())
	return wrap.Bytes()
}

// buildRunningTradeBatchFrame builds a WebsocketWrapMessageChannel
// running_trade_batch frame (field 8) containing one batch entry (field 1)
// per symbol, each carrying only stock_code (field 2) — price/volume are
// optional fields the decoder tolerates missing.
func buildRunningTradeBatchFrame(symbols ...string) []byte {
	var batch sbproto.Writer
	for _, sym := range symbols {
		var trade sbproto.Writer
		trade.String(2, sym) // stock
		batch.Message(1, trade.Bytes())
	}
	var wrap sbproto.Writer
	wrap.Message(8, batch.Bytes()) // field 8 = running_trade_batch
	return wrap.Bytes()
}

// ---------- test seams ----------

func instantSleep(context.Context, time.Duration) {}

// sleepRecorder is a sleep seam that hands each requested duration to the
// test over a channel instead of actually waiting, so backoff sequencing can
// be asserted without a real-time test. It stays context-aware so a test
// that cancels ctx mid-backoff does not leak the production goroutine.
type sleepRecorder struct {
	ch chan time.Duration
}

func newSleepRecorder() *sleepRecorder {
	return &sleepRecorder{ch: make(chan time.Duration)}
}

func (r *sleepRecorder) sleep(ctx context.Context, d time.Duration) {
	select {
	case r.ch <- d:
	case <-ctx.Done():
	}
}

func recvDuration(t *testing.T, ch <-chan time.Duration, timeout time.Duration) time.Duration {
	t.Helper()
	select {
	case d := <-ch:
		return d
	case <-time.After(timeout):
		t.Fatal("timed out waiting for a recorded backoff duration")
		return 0
	}
}

func testOpts(now func() time.Time, sleep func(context.Context, time.Duration)) liveSourceOptions {
	return liveSourceOptions{
		now:        now,
		sleep:      sleep,
		minBackoff: liveReconnectMin,
		maxBackoff: liveReconnectMax,
	}
}

func loadCalendar(t *testing.T) *calendar.Calendar {
	t.Helper()
	cal, err := calendar.Load()
	if err != nil {
		t.Fatalf("calendar.Load: %v", err)
	}
	return cal
}

// drain reads exactly n events from ch, failing the test on early close or
// on timeout.
func drain(t *testing.T, ch <-chan MarketEvent, n int, timeout time.Duration) []MarketEvent {
	t.Helper()
	got := make([]MarketEvent, 0, n)
	deadline := time.After(timeout)
	for len(got) < n {
		select {
		case e, ok := <-ch:
			if !ok {
				t.Fatalf("channel closed early: got %d/%d events", len(got), n)
			}
			got = append(got, e)
		case <-deadline:
			t.Fatalf("timed out waiting for %d events, got %d", n, len(got))
		}
	}
	return got
}

// assertClosed drains ch until it observes closure, then reads a few more
// times to confirm the channel stays closed (never yields a value, never
// panics) — the "closed exactly once" property.
func assertClosed(t *testing.T, ch <-chan MarketEvent, timeout time.Duration) {
	t.Helper()
	deadline := time.After(timeout)
	sawClose := false
	for !sawClose {
		select {
		case _, ok := <-ch:
			if !ok {
				sawClose = true
			}
		case <-deadline:
			t.Fatal("channel did not close in time")
		}
	}
	for i := 0; i < 3; i++ {
		select {
		case v, ok := <-ch:
			if ok {
				t.Fatalf("channel yielded a value after close: %+v", v)
			}
		case <-time.After(50 * time.Millisecond):
			t.Fatal("read after close unexpectedly blocked")
		}
	}
}

// assertNoGoroutineLeak polls runtime.NumGoroutine() until it returns to (or
// below) baseline, or fails after timeout. This is a real assertion, not an
// assumption: it fails the test if the reconnect-loop or watcher goroutines
// are still alive after the channel has closed.
func assertNoGoroutineLeak(t *testing.T, baseline int, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		if n := runtime.NumGoroutine(); n <= baseline {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("goroutine leak: have %d goroutines, want <= %d baseline", runtime.NumGoroutine(), baseline)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// ---------- tests ----------

func TestLiveSource_CleanEventFlow(t *testing.T) {
	conn := newFakeConn()
	dialer := &fakeDialer{fn: func(int) (LiveConn, error) { return conn, nil }}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, instantSleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	conn.push(fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BBCA", 0)})

	events := drain(t, ch, 1, 2*time.Second)
	if events[0].Kind != EventOrderbook {
		t.Errorf("Kind = %v, want %v", events[0].Kind, EventOrderbook)
	}
	if events[0].Symbol != "BBCA" {
		t.Errorf("Symbol = %q, want %q", events[0].Symbol, "BBCA")
	}
	if events[0].RecvNs == 0 {
		t.Error("RecvNs is zero, want a real arrival timestamp")
	}

	cancel()
	assertClosed(t, ch, 2*time.Second)
}

func TestLiveSource_CtxCancelClosesChannelAndDoesNotLeak(t *testing.T) {
	before := runtime.NumGoroutine()

	conn := newFakeConn() // no frames queued: ReadMessage blocks
	dialer := &fakeDialer{fn: func(int) (LiveConn, error) { return conn, nil }}

	ctx, cancel := context.WithCancel(context.Background())

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, instantSleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	// Wait until the reconnect-loop goroutine has actually reached the
	// blocking read, so cancellation exercises the "unblock a pending read"
	// path deterministically rather than racing a fresh connection attempt.
	conn.waitBlocked(t, 2*time.Second)

	cancel()
	assertClosed(t, ch, 2*time.Second)

	if conn.closeCallCount() < 1 {
		t.Error("expected ctx cancellation to close the underlying connection")
	}

	assertNoGoroutineLeak(t, before, 2*time.Second)
}

func TestLiveSource_ReconnectEmitsControlBeforeNextMarketEvent(t *testing.T) {
	conn1 := newFakeConn(
		fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BBCA", 0)},
		fakeFrame{err: errors.New("simulated disconnect")},
	)
	conn2 := newFakeConn(
		fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BMRI", 0)},
	)
	dialer := &fakeDialer{fn: func(call int) (LiveConn, error) {
		if call == 0 {
			return conn1, nil
		}
		return conn2, nil
	}}

	rec := newSleepRecorder()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, rec.sleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	first := drain(t, ch, 2, 2*time.Second)
	if first[0].Kind != EventOrderbook || first[0].Symbol != "BBCA" {
		t.Fatalf("event 0 = %+v, want orderbook BBCA", first[0])
	}
	if first[1].Kind != EventControl || first[1].Control == nil || first[1].Control.Event != "RECONNECT" {
		t.Fatalf("event 1 = %+v, want a RECONNECT control event", first[1])
	}
	if first[1].Control.Attempt != 0 {
		t.Errorf("RECONNECT attempt = %d, want 0", first[1].Control.Attempt)
	}
	if first[1].Control.BackoffMs != int(liveReconnectMin.Milliseconds()) {
		t.Errorf("RECONNECT backoff_ms = %d, want %d", first[1].Control.BackoffMs, liveReconnectMin.Milliseconds())
	}

	// Unblock the recorded backoff wait so the second connection attempt
	// proceeds.
	d := recvDuration(t, rec.ch, 2*time.Second)
	if d != liveReconnectMin {
		t.Errorf("recorded backoff = %s, want %s", d, liveReconnectMin)
	}

	second := drain(t, ch, 1, 2*time.Second)
	if second[0].Kind != EventOrderbook || second[0].Symbol != "BMRI" {
		t.Fatalf("event after reconnect = %+v, want orderbook BMRI", second[0])
	}

	if dialer.callCount() != 2 {
		t.Errorf("dial call count = %d, want 2", dialer.callCount())
	}
}

func TestLiveSource_BackoffBoundedAcrossRepeatedFailures(t *testing.T) {
	dialer := &fakeDialer{fn: func(call int) (LiveConn, error) {
		return nil, fmt.Errorf("simulated dial failure (attempt %d)", call)
	}}

	rec := newSleepRecorder()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, rec.sleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	want := []time.Duration{
		750 * time.Millisecond,
		1500 * time.Millisecond,
		3 * time.Second,
		3 * time.Second, // capped at liveReconnectMax
	}
	for i, wantBackoff := range want {
		evts := drain(t, ch, 1, 2*time.Second)
		if evts[0].Kind != EventControl || evts[0].Control == nil || evts[0].Control.Event != "RECONNECT" {
			t.Fatalf("iter %d: event = %+v, want RECONNECT control", i, evts[0])
		}
		if evts[0].Control.Attempt != i {
			t.Errorf("iter %d: attempt = %d, want %d", i, evts[0].Control.Attempt, i)
		}
		got := recvDuration(t, rec.ch, 2*time.Second)
		if got != wantBackoff {
			t.Errorf("iter %d: backoff = %s, want %s", i, got, wantBackoff)
		}
	}

	cancel()
	assertClosed(t, ch, 2*time.Second)
}

func TestLiveSource_SwitchResetDedupAndControlEvent(t *testing.T) {
	conn := newFakeConn(
		fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BBCA", 1)}, // reset begins
		fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BBCA", 1)}, // sustained — no re-emit
		fakeFrame{opcode: feed.OpBinary, data: buildOrderbookFrame("BBCA", 0)}, // reset clears
	)
	dialer := &fakeDialer{fn: func(int) (LiveConn, error) { return conn, nil }}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, instantSleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	// One SWITCH_RESET control event (from the first frame) followed by
	// three ordinary orderbook data events (one per frame) — the sustained
	// reset on frame 2 must not produce a second control event.
	evts := drain(t, ch, 4, 2*time.Second)
	if evts[0].Kind != EventControl || evts[0].Control == nil || evts[0].Control.Event != "SWITCH_RESET" {
		t.Fatalf("event 0 = %+v, want SWITCH_RESET control", evts[0])
	}
	if evts[0].Control.Symbol != "BBCA" {
		t.Errorf("SWITCH_RESET symbol = %q, want %q", evts[0].Control.Symbol, "BBCA")
	}
	for i := 1; i < 4; i++ {
		if evts[i].Kind != EventOrderbook || evts[i].Symbol != "BBCA" {
			t.Errorf("event %d = %+v, want orderbook BBCA", i, evts[i])
		}
		if evts[i].Kind == EventControl {
			t.Errorf("event %d: unexpected second SWITCH_RESET control event", i)
		}
	}
}

func TestLiveSource_RefusesToStartOnNonTradingDay(t *testing.T) {
	cal := loadCalendar(t)
	saturday := time.Date(2026, 9, 19, 10, 0, 0, 0, time.UTC) // confirmed Saturday

	dialer := &fakeDialer{fn: func(int) (LiveConn, error) {
		t.Fatal("dial must not be called when the market is closed")
		return nil, nil
	}}

	opts := testOpts(func() time.Time { return saturday }, instantSleep)
	ch, err := liveSource(context.Background(), dialer.dial, cal, opts)

	if !errors.Is(err, ErrMarketClosed) {
		t.Fatalf("err = %v, want ErrMarketClosed", err)
	}
	if ch != nil {
		t.Error("expected a nil channel when the market is closed")
	}
	if dialer.callCount() != 0 {
		t.Errorf("dial call count = %d, want 0 (must refuse before connecting)", dialer.callCount())
	}
}

func TestLiveSource_RequiresDialAndCalendar(t *testing.T) {
	cal := loadCalendar(t)
	if _, err := LiveSource(context.Background(), nil, cal); err == nil {
		t.Error("expected an error when dial is nil")
	}

	dial := func(ctx context.Context) (LiveConn, error) { return nil, nil }
	if _, err := LiveSource(context.Background(), dial, nil); err == nil {
		t.Error("expected an error when calendar is nil")
	}
}

func TestLiveSource_RunningTradeBatchExpandsToOneEventPerTrade(t *testing.T) {
	conn := newFakeConn(fakeFrame{
		opcode: feed.OpBinary,
		data:   buildRunningTradeBatchFrame("BBCA", "BMRI"),
	})
	dialer := &fakeDialer{fn: func(int) (LiveConn, error) { return conn, nil }}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t), testOpts(time.Now, instantSleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	evts := drain(t, ch, 2, 2*time.Second)
	wantSymbols := []string{"BBCA", "BMRI"}
	for i, want := range wantSymbols {
		if evts[i].Kind != EventRunningTrade {
			t.Errorf("event %d: Kind = %v, want %v", i, evts[i].Kind, EventRunningTrade)
		}
		if evts[i].Symbol != want {
			t.Errorf("event %d: Symbol = %q, want %q", i, evts[i].Symbol, want)
		}
		if evts[i].Decoded == nil || len(evts[i].Decoded.Trades) != 1 {
			t.Errorf("event %d: Decoded.Trades = %+v, want exactly one trade", i, evts[i].Decoded)
		}
	}
}

func TestLiveEventKind_MapsAllDecodedKinds(t *testing.T) {
	cases := []struct {
		in   string
		want EventKind
	}{
		{"liveprice", EventLivePrice},
		{"orderbook", EventOrderbook},
		{"running_trade", EventRunningTrade},
		{"iepiev", EventIEPIEV},
		{"ping", EventKind("ping")},
		{"other", EventKind("other")},
	}
	for _, tc := range cases {
		if got := liveEventKind(tc.in); got != tc.want {
			t.Errorf("liveEventKind(%q) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestSendEvent(t *testing.T) {
	t.Run("succeeds and delivers the event", func(t *testing.T) {
		ch := make(chan MarketEvent, 1)
		if !sendEvent(context.Background(), ch, MarketEvent{Symbol: "BBCA"}) {
			t.Fatal("expected sendEvent to report true")
		}
		if got := <-ch; got.Symbol != "BBCA" {
			t.Errorf("Symbol = %q, want BBCA", got.Symbol)
		}
	})

	t.Run("reports false when ctx is already cancelled", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		ch := make(chan MarketEvent) // unbuffered, no receiver
		if sendEvent(ctx, ch, MarketEvent{}) {
			t.Error("expected sendEvent to report false for a cancelled ctx")
		}
	})
}

func TestWaitOrCancel(t *testing.T) {
	t.Run("returns after the requested duration", func(t *testing.T) {
		start := time.Now()
		waitOrCancel(context.Background(), 10*time.Millisecond)
		if elapsed := time.Since(start); elapsed < 10*time.Millisecond {
			t.Errorf("returned after %s, want >= 10ms", elapsed)
		}
	})

	t.Run("returns early when ctx is already cancelled", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		start := time.Now()
		waitOrCancel(ctx, 5*time.Second)
		if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
			t.Errorf("did not return promptly on a cancelled ctx: %s", elapsed)
		}
	})
}

// The collector creates its per-symbol switch_reset dedup map fresh inside
// runOnce (collector/cmd/collector/main.go:466) — i.e. per CONNECTION. If
// LiveSource carries that map across a reconnect it will suppress a
// SWITCH_RESET the collector would have written, so a live run and a replay
// of a capture of the same session disagree.
func TestLiveSource_SwitchResetDedupResetsPerConnection(t *testing.T) {
	reset := buildOrderbookFrame("AAAA", 1)
	dialer := &fakeDialer{fn: func(call int) (LiveConn, error) {
		switch call {
		case 0:
			return newFakeConn(
				fakeFrame{opcode: feed.OpBinary, data: reset},
				fakeFrame{err: errors.New("connection dropped")},
			), nil
		default:
			return newFakeConn(fakeFrame{opcode: feed.OpBinary, data: reset}), nil
		}
	}}

	tradingDay := time.Date(2026, 9, 15, 10, 0, 0, 0, time.UTC) // Tuesday
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	ch, err := liveSource(ctx, dialer.dial, loadCalendar(t),
		testOpts(func() time.Time { return tradingDay }, instantSleep))
	if err != nil {
		t.Fatalf("liveSource: %v", err)
	}

	var controls []string
	deadline := time.After(3 * time.Second)
	for len(controls) < 3 {
		select {
		case evt, ok := <-ch:
			if !ok {
				t.Fatalf("channel closed early; controls so far = %v", controls)
			}
			if evt.Kind == EventControl && evt.Control != nil {
				controls = append(controls, evt.Control.Event)
			}
		case <-deadline:
			t.Fatalf("timed out; controls = %v (want SWITCH_RESET, RECONNECT, SWITCH_RESET)", controls)
		}
	}
	want := []string{"SWITCH_RESET", "RECONNECT", "SWITCH_RESET"}
	for i := range want {
		if controls[i] != want[i] {
			t.Fatalf("controls = %v, want %v: the dedup map must be per-connection, as collector/cmd/collector's runOnce makes it", controls, want)
		}
	}
}
