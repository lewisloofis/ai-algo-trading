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

	"collector/internal/feed"
	"collector/internal/s3upload"
	"collector/internal/segment"
)

const (
	heartbeatInterval = 10 * time.Second
	tokenRefreshEvery = 20 * time.Minute
	reconnectMin      = 750 * time.Millisecond
	reconnectMax      = 3 * time.Second
	readDeadline      = 60 * time.Second
	statsInterval     = 15 * time.Second
	// rollInterval bounds the data at risk from an unexpected crash: the active
	// segment is closed, compressed, and uploaded to S3 at every interval
	// boundary, so at most one interval of capture can be lost. The 00:00 UTC
	// boundary also advances the dt=<date> partition.
	rollInterval = time.Hour
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

	// Hoist SSM prefix once — used by both the param reader and the token store.
	ssmPrefix := os.Getenv("SSM_PARAM_PREFIX")

	// Build the param reader for plain (non-secret) SSM config when the prefix
	// is set. The collector reads these (watchlist, watchlist_id) but never
	// writes them — Terraform stays authoritative.
	var paramReader *feed.SSMParamReader
	if ssmPrefix != "" {
		paramReader, err = feed.NewSSMParamReader(ssmPrefix)
		if err != nil {
			log.Fatalf("ssm param reader init: %v", err)
		}
	}

	symbols, err := loadWatchlist(paramReader)
	if err != nil {
		log.Fatalf("watchlist: %v", err)
	}
	cfg.symbols = symbols

	// Override watchlist_id from SSM when available — authoritative over the
	// CAPTURE_WATCHLIST_ID env var (which remains a local-dev fallback).
	if paramReader != nil {
		if id, idErr := paramReader.Get("watchlist_id"); idErr != nil {
			log.Fatalf("ssm watchlist_id: %v", idErr)
		} else if id != "" {
			cfg.watchlistID = id
			log.Printf("[watchlist] id %q loaded from SSM", id)
		}
	}

	// Token store selection: use SSMTokenStore when SSM_PARAM_PREFIX is set;
	// otherwise fall back to the local FileTokenStore.
	var store feed.TokenStore
	if ssmPrefix != "" {
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

	// Recover any segments left uncompressed by a previous crash or hard kill
	// before opening a fresh one, so stranded capture still reaches S3.
	recoverOrphans(cfg.captureDir, sf.uploader, cfg.symbols, activeChannelNames)

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

	// Periodic roll: close, compress, and upload the active segment at every
	// rollInterval boundary so a crash loses at most one interval of data, not
	// the whole session. The 00:00 UTC boundary also rolls the date partition.
	go rollLoop(ctx, sf, cfg.symbols, rollInterval)

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

// nextRollBoundary returns the next wall-clock boundary aligned to interval, in
// UTC. With interval=1h this is the top of the next hour; the 00:00 boundary
// also advances the dt=<date> partition, subsuming the previous midnight-only
// roll.
func nextRollBoundary(now time.Time, interval time.Duration) time.Time {
	return now.UTC().Truncate(interval).Add(interval)
}

// rollLoop rolls the active segment at every interval boundary so completed
// segments are compressed and uploaded to S3 throughout the session, bounding
// the data at risk from an unexpected crash to a single interval.
func rollLoop(ctx context.Context, sf *segmentFile, symbols []string, interval time.Duration) {
	for {
		wait := time.Until(nextRollBoundary(time.Now(), interval))
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
			log.Printf("[segment] %s boundary — rolling segment", interval)
			sf.roll(symbols, activeChannelNames)
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
