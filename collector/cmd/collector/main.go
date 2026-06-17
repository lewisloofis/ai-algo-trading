// collector captures raw WebSocket frames from the Stockbit market-data feed
// and writes them to local segment files for offline replay and backtesting.
//
// Phase 2: subscribes to RunningTradeBatch + OrderBook + LivePrice for a
// configurable watchlist, writes raw binary segments to CAPTURE_DIR,
// compresses closed segments with zstd, optionally uploads to S3 (S3_BUCKET),
// and detects switch_reset events on the hot path. Tokens can be persisted in
// AWS SSM Parameter Store (SSM_PARAM_PREFIX) for headless EC2 operation.
//
// See docs/plans/01-market-data-capture.md for the full architecture.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/s3upload"
	"sb-algo-prototype/internal/segment"
)

const (
	heartbeatInterval = 10 * time.Second
	tokenRefreshEvery = 20 * time.Minute
	reconnectMin      = 750 * time.Millisecond
	reconnectMax      = 3 * time.Second
	readDeadline      = 60 * time.Second
	statsInterval     = 15 * time.Second
)

// activeChannels enables OrderBook, LivePrice, RunningTradeBatch, and IEPIEV
// for full microstructure capture.
var activeChannels = feed.Channels{
	OrderBook:         true,
	RunningTradeBatch: true,
	LivePrice:         true,
	IEPIEV:            true,
}

// activeChannelNames is the channels list recorded in the manifest.
var activeChannelNames = []string{"runningTradeBatch", "orderBook", "liveprice", "iepiev"}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	log.SetPrefix("")

	cfg, err := loadConfig()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	symbols, err := loadWatchlist()
	if err != nil {
		log.Fatalf("watchlist: %v", err)
	}
	cfg.symbols = symbols

	// Token store selection: use SSMTokenStore when SSM_PARAM_PREFIX is set;
	// otherwise fall back to the local FileTokenStore.
	var store feed.TokenStore
	if ssmPrefix := os.Getenv("SSM_PARAM_PREFIX"); ssmPrefix != "" {
		s, err := feed.NewSSMTokenStore(ssmPrefix)
		if err != nil {
			log.Fatalf("ssm token store init: %v", err)
		}
		store = s
		log.Printf("[auth] using SSM token store (prefix=%s)", ssmPrefix)
	} else {
		store = &feed.FileTokenStore{Path: cfg.tokenFile}
		log.Printf("[auth] using file token store (%s)", cfg.tokenFile)
	}

	// Token bootstrap: if no access token in env, try loading from the store.
	if cfg.feedCfg.AccessToken == "" {
		acc, ref, err := store.Load()
		if err != nil {
			log.Fatalf("token store load: %v", err)
		}
		if acc != "" {
			cfg.feedCfg.AccessToken = acc
			cfg.feedCfg.RefreshToken = ref
			log.Printf("[auth] loaded tokens from store")
		}
	}

	// Validate authentication only after env vars AND the token store have been
	// consulted — headless EC2 has no token env vars; it relies on SSM-seeded tokens.
	if !cfg.hasAuth() {
		log.Fatalf("config: need SB_USERNAME+SB_PASSWORD, SB_ACCESS_TOKEN, or tokens seeded in the token store")
	}

	session := feed.NewSession(cfg.feedCfg)
	session.SetTokenStore(store)

	if err := session.Login(); err != nil {
		log.Fatalf("login: %v", err)
	}
	log.Printf("[auth] main session ready (user_id=%q)", session.UserID())

	// Persist tokens immediately after login so the store reflects the live
	// access token before the first proactive refresh. Pass the refresh token we
	// currently hold (from env or the store) rather than "" — an empty value is
	// rejected by SSM and would clobber a good token.
	if err := store.Save(session.AccessToken(), cfg.feedCfg.RefreshToken); err != nil {
		// Non-fatal: we can still capture; warn the operator.
		log.Printf("WARN: initial token persist failed: %v", err)
	}

	sf := newSegmentFile(cfg.captureDir, cfg.watchlistID)

	// S3 upload: configure when S3_BUCKET is set and non-empty.
	if bucket := os.Getenv("S3_BUCKET"); bucket != "" {
		up, err := s3upload.NewUploader(bucket)
		if err != nil {
			log.Fatalf("s3 uploader init: %v", err)
		}
		sf.uploader = up
		log.Printf("[s3] upload enabled (bucket=%s)", bucket)
	}

	if err := sf.open(time.Now()); err != nil {
		log.Fatalf("segment open: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Graceful shutdown on Ctrl-C / SIGTERM.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-sigCh
		log.Printf("[main] signal %v received — shutting down", sig)
		cancel()
	}()

	// Proactive token refresh loop (mirrors prototype/main.go).
	go refreshLoop(ctx, session, sf)

	// UTC midnight roll: when the date changes, close and compress the current
	// segment, then open a new one for the new day.
	go midnightRollLoop(ctx, sf, cfg.symbols)

	runWithReconnect(ctx, cfg, session, sf)

	// Shutdown: close and compress the open segment before exiting.
	log.Printf("[main] flushing and compressing final segment…")
	sf.closeAndCompress(cfg.symbols, activeChannelNames)
	log.Printf("[main] done")
}

// refreshLoop proactively refreshes the main access token on a fixed interval.
// After a successful refresh it writes a REAUTH control record to the segment.
func refreshLoop(ctx context.Context, s *feed.Session, sf *segmentFile) {
	t := time.NewTicker(tokenRefreshEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := s.RefreshMain(); err != nil {
				log.Printf("[auth] proactive refresh failed: %v", err)
				continue
			}
			log.Printf("[auth] main token refreshed")
			payload, err := segment.ReauthEvent("proactive")
			if err != nil {
				continue
			}
			if err := sf.WriteControl(time.Now().UnixNano(), payload); err != nil {
				log.Printf("[segment] reauth control record: %v", err)
			}
		}
	}
}

// midnightRollLoop watches for UTC midnight and rolls the active segment.
func midnightRollLoop(ctx context.Context, sf *segmentFile, symbols []string) {
	for {
		now := time.Now().UTC()
		// Next UTC midnight.
		next := time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 0, 0, time.UTC)
		wait := time.Until(next)

		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
			log.Printf("[segment] UTC midnight — rolling segment")
			// Close and compress in a goroutine so the capture loop is not blocked.
			go sf.closeAndCompress(symbols, activeChannelNames)
			// Small delay to ensure closeAndCompress clears sf.sw before open.
			time.Sleep(100 * time.Millisecond)
			if err := sf.open(time.Now()); err != nil {
				log.Printf("[segment] open new segment after roll: %v", err)
			}
		}
	}
}

// runWithReconnect keeps the feed alive across disconnects with capped
// exponential backoff, mirroring prototype/main.go.
func runWithReconnect(ctx context.Context, cfg collectorConfig, s *feed.Session, sf *segmentFile) {
	backoff := reconnectMin
	attempt := 0
	for ctx.Err() == nil {
		err := runOnce(ctx, cfg, s, sf)
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			log.Printf("[feed] disconnected: %v — reconnecting in %s", err, backoff)
		}

		backoffMs := int(backoff.Milliseconds())
		payload, perr := segment.ReconnectEvent(attempt, backoffMs)
		if perr == nil {
			if werr := sf.WriteControl(time.Now().UnixNano(), payload); werr != nil {
				log.Printf("[segment] reconnect control record: %v", werr)
			}
		}

		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}

		backoff *= 2
		if backoff > reconnectMax {
			backoff = reconnectMax
		}
		attempt++
	}
}

// getKeyWithRefresh fetches a websocket key, performing one reactive token
// refresh on a 401 before retrying. It writes a REAUTH control record on
// success so the segment captures the auth event.
func getKeyWithRefresh(s *feed.Session, sf *segmentFile) (string, error) {
	key, err := s.GetWebsocketKey()
	if err == nil {
		return key, nil
	}
	if !errors.Is(err, feed.ErrUnauthorized) {
		return "", err
	}

	log.Printf("[auth] websocket key rejected (401) — refreshing token")
	if refreshErr := s.RefreshMain(); refreshErr != nil {
		return "", fmt.Errorf("reactive refresh after 401 failed: %w", refreshErr)
	}
	log.Printf("[auth] token refreshed after 401")

	payload, err := segment.ReauthEvent("reactive-401")
	if err == nil {
		if werr := sf.WriteControl(time.Now().UnixNano(), payload); werr != nil {
			log.Printf("[segment] reauth control record: %v", werr)
		}
	}

	return s.GetWebsocketKey()
}

// runOnce connects, authenticates, subscribes, and pumps raw frames to disk
// until an error or context cancellation.
func runOnce(ctx context.Context, cfg collectorConfig, s *feed.Session, sf *segmentFile) error {
	key, err := getKeyWithRefresh(s, sf)
	if err != nil {
		return err
	}

	conn, err := feed.DialWS(cfg.feedCfg.WSURL, cfg.feedCfg.WSSubProto)
	if err != nil {
		return err
	}
	defer conn.Close()
	log.Printf("[feed] connected to %s", cfg.feedCfg.WSURL)

	// Per-symbol switch_reset dedup: track whether the previous frame for a
	// symbol already had switch_reset set, so sustained-reset frames don't
	// spam SWITCH_RESET control records.
	inReset := make(map[string]bool)

	// CONNECT control record.
	connectPayload, err := segment.ConnectEvent(cfg.feedCfg.WSURL)
	if err != nil {
		return err
	}
	if err := sf.WriteControl(time.Now().UnixNano(), connectPayload); err != nil {
		return err
	}

	// Send the subscribe frame (auth + channels).
	subFrame := feed.BuildSubscribe(s.UserID(), s.AccessToken(), key, cfg.symbols, activeChannels)
	if err := conn.WriteBinary(subFrame); err != nil {
		return err
	}
	log.Printf("[feed] subscribed runningTradeBatch+orderBook+liveprice+iepiev for %d symbol(s)", len(cfg.symbols))

	// SUBSCRIBE control record.
	subscribePayload, err := segment.SubscribeEvent(cfg.symbols)
	if err != nil {
		return err
	}
	if err := sf.WriteControl(time.Now().UnixNano(), subscribePayload); err != nil {
		return err
	}

	// Heartbeat goroutine.
	pingCtx, stopPing := context.WithCancel(ctx)
	defer stopPing()
	go func() {
		t := time.NewTicker(heartbeatInterval)
		defer t.Stop()
		for {
			select {
			case <-pingCtx.Done():
				return
			case <-t.C:
				if err := conn.WriteBinary(feed.BuildPing(s.UserID(), s.AccessToken())); err != nil {
					return
				}
			}
		}
	}()

	// Capture counters — written by read loop, read by stats goroutine.
	var frames, bytesWritten int64

	// Stats goroutine: logs throughput every statsInterval.
	statsCtx, stopStats := context.WithCancel(ctx)
	defer stopStats()
	go func() {
		t := time.NewTicker(statsInterval)
		defer t.Stop()
		var prevFrames, prevBytes int64
		for {
			select {
			case <-statsCtx.Done():
				return
			case <-t.C:
				curFrames := atomic.LoadInt64(&frames)
				curBytes := atomic.LoadInt64(&bytesWritten)
				deltaFrames := curFrames - prevFrames
				deltaBytes := curBytes - prevBytes
				prevFrames = curFrames
				prevBytes = curBytes
				if deltaFrames == 0 {
					log.Printf("[capture] no frames in last 15s (market closed or quiet) — %d frames total", curFrames)
				} else {
					log.Printf("[capture] %d frames / %.1f KB in last 15s (%d frames total)",
						deltaFrames, float64(deltaBytes)/1024, curFrames)
				}
			}
		}
	}()

	// Hot path: read loop — capture recv_ns immediately, write raw frame.
	firstFrame := true
	for {
		if ctx.Err() != nil {
			return nil
		}
		conn.SetReadDeadline(time.Now().Add(readDeadline))
		op, data, err := conn.ReadMessage()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return errors.New("server closed connection")
			}
			return err
		}
		if op != feed.OpBinary {
			continue
		}
		// Capture arrival time immediately — no decode.
		recvNs := time.Now().UnixNano()
		if err := sf.WriteData(recvNs, data); err != nil {
			log.Printf("[segment] write data record: %v", err)
		}
		atomic.AddInt64(&frames, 1)
		atomic.AddInt64(&bytesWritten, int64(len(data)))
		if firstFrame {
			firstFrame = false
			log.Printf("[capture] first frame received (%d bytes)", len(data))
		}

		// switch_reset detection: cheap partial decode, only on orderbook frames.
		// Emit at most one SWITCH_RESET control record per symbol per reset
		// transition (not once per sustained-reset frame).
		if sym, isReset := feed.DetectSwitchReset(data); sym != "" {
			if isReset && !inReset[sym] {
				if payload, perr := segment.SwitchResetEvent(sym); perr == nil {
					if werr := sf.WriteControl(recvNs, payload); werr != nil {
						log.Printf("[segment] switch_reset control record: %v", werr)
					}
				}
			}
			inReset[sym] = isReset
		}
	}
}
