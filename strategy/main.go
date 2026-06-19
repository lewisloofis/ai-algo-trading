package main

import (
	"context"
	"errors"
	"io"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const (
	heartbeatInterval = 10 * time.Second // matches the official clients
	tokenRefreshEvery = 20 * time.Minute // proactive main-token refresh
	reconnectMin      = 750 * time.Millisecond
	reconnectMax      = 3 * time.Second
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	log.SetPrefix("")

	cfg, err := loadConfig()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	session := NewSession(cfg)
	if err := session.Login(); err != nil {
		log.Fatalf("login: %v", err)
	}
	log.Printf("[auth] main session ready (user_id=%q)", session.UserID())

	if cfg.Live {
		if err := session.TradingLogin(); err != nil {
			log.Fatalf("trading login: %v", err)
		}
		log.Printf("[auth] trading session ready")
	}

	banner(cfg)

	strat := NewImbalanceStrategy(cfg.DepthLevels, cfg.ImbalanceThresh)
	broker := NewBroker(cfg, session)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Graceful shutdown on Ctrl-C / SIGTERM.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Printf("shutting down…")
		cancel()
	}()

	// Background proactive token refresh (keeps the WS auth valid).
	go refreshLoop(ctx, session)

	runWithReconnect(ctx, cfg, session, strat, broker)
}

func banner(cfg Config) {
	mode := "DRY-RUN (no real orders)"
	if cfg.LiveArmed() {
		mode = "🔴 LIVE TRADING (real orders, real money)"
	} else if cfg.Live {
		mode = "LIVE requested but NOT armed — set ALGO_CONFIRM=" + liveConfirmPhrase + " to send real orders"
	}
	log.Printf("──────────────────────────────────────────────")
	log.Printf(" Stockbit algo prototype — orderbook imbalance")
	log.Printf(" symbol=%s depth=%d threshold=%.2f lots/order=%d", cfg.Symbol, cfg.DepthLevels, cfg.ImbalanceThresh, cfg.LotsPerOrder)
	log.Printf(" caps: maxOrders=%d maxLots=%d maxNotional=%.0f cooldown=%ds", cfg.MaxOrders, cfg.MaxLots, cfg.MaxNotional, cfg.CooldownSec)
	log.Printf(" mode: %s", mode)
	log.Printf("──────────────────────────────────────────────")
}

func refreshLoop(ctx context.Context, s *Session) {
	t := time.NewTicker(tokenRefreshEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := s.RefreshMain(); err != nil {
				log.Printf("[auth] proactive refresh failed: %v", err)
			} else {
				log.Printf("[auth] main token refreshed")
			}
		}
	}
}

// runWithReconnect keeps the feed alive across disconnects with capped backoff.
func runWithReconnect(ctx context.Context, cfg Config, s *Session, strat *ImbalanceStrategy, broker *Broker) {
	backoff := reconnectMin
	for ctx.Err() == nil {
		err := runOnce(ctx, cfg, s, strat, broker)
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			log.Printf("[feed] disconnected: %v — reconnecting in %s", err, backoff)
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
	}
}

// runOnce connects, authenticates, subscribes, and pumps messages until error.
func runOnce(ctx context.Context, cfg Config, s *Session, strat *ImbalanceStrategy, broker *Broker) error {
	key, err := s.GetWebsocketKey()
	if err != nil {
		return err
	}
	conn, err := dialWS(cfg.WSURL, cfg.WSSubProto)
	if err != nil {
		return err
	}
	defer conn.Close()
	log.Printf("[feed] connected to %s", cfg.WSURL)

	if err := conn.WriteBinary(buildSubscribe(s.UserID(), s.AccessToken(), key, cfg.Symbol)); err != nil {
		return err
	}
	log.Printf("[feed] subscribed order_book + liveprice for %s", cfg.Symbol)

	// Heartbeat.
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
				if err := conn.WriteBinary(buildPing(s.UserID(), s.AccessToken())); err != nil {
					return
				}
			}
		}
	}()

	for {
		if ctx.Err() != nil {
			return nil
		}
		conn.SetReadDeadline(time.Now().Add(60 * time.Second))
		op, data, err := conn.ReadMessage()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return errors.New("server closed connection")
			}
			return err
		}
		if op != opBinary {
			continue
		}
		msg, err := decodeWrap(data)
		if err != nil {
			continue // skip malformed frame
		}
		handleMessage(msg, cfg, strat, broker)
	}
}

func handleMessage(msg decoded, cfg Config, strat *ImbalanceStrategy, broker *Broker) {
	switch msg.kind {
	case "error":
		log.Printf("[feed] server error code=%d msg=%q", msg.errCode, msg.errMsg)
	case "orderbook":
		side, ok := parseOrderbookBody(msg.body)
		if !ok {
			return
		}
		sig, imb, ref, ready := strat.Update(side)
		if !ready {
			return
		}
		log.Printf("[book] %s | %s | signal=%s", side, strat.Describe(imb, ref), sig)
		if sig != SignalNone && !broker.Killed() {
			broker.OnSignal(sig, ref)
		}
	case "liveprice":
		// Available for richer strategies; not used by the imbalance demo.
	}
}
