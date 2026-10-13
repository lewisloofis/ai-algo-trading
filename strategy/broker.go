package main

// Broker turns strategy signals into orders on the Carina trading backend,
// per docs/03-order-placement.md. It defaults to DRY-RUN: orders are logged but
// not sent. Real orders require Config.LiveArmed() (ALGO_LIVE=true AND the exact
// ALGO_CONFIRM phrase) and still pass per-order caps, a session order limit,
// a cooldown, and a kill switch.

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"time"
)

type Broker struct {
	cfg     Config
	session *Session

	mu          sync.Mutex
	orderCount  int
	lastOrderAt time.Time
	killed      bool
	position    int // net lots held (by this bot), for flat/long/short awareness
}

func NewBroker(cfg Config, s *Session) *Broker {
	return &Broker{cfg: cfg, session: s}
}

// Killed reports whether the kill switch has tripped.
func (b *Broker) Killed() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.killed
}

func (b *Broker) kill(reason string) {
	b.killed = true
	log.Printf("[broker] 🛑 KILL SWITCH: %s — no further orders this session", reason)
}

// OnSignal is called by the runner with the current signal and reference price.
func (b *Broker) OnSignal(sig Signal, refPrice float64) {
	if sig == SignalNone {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.killed {
		return
	}
	// Don't stack same-direction exposure.
	if sig == SignalBuy && b.position > 0 {
		return
	}
	if sig == SignalSell && b.position <= 0 {
		return // nothing to sell (this demo doesn't short)
	}
	if b.orderCount >= b.cfg.MaxOrders {
		b.kill(fmt.Sprintf("reached ALGO_MAX_ORDERS=%d", b.cfg.MaxOrders))
		return
	}
	if d := time.Since(b.lastOrderAt); b.lastOrderAt.After(time.Time{}) && d < time.Duration(b.cfg.CooldownSec)*time.Second {
		return // in cooldown
	}

	lots := b.cfg.LotsPerOrder
	price := roundTick(refPrice)
	if err := b.checkCaps(lots, price); err != nil {
		b.kill(err.Error())
		return
	}

	if err := b.place(sig, lots, price); err != nil {
		log.Printf("[broker] order error: %v", err)
		b.kill("order request failed")
		return
	}

	b.orderCount++
	b.lastOrderAt = time.Now()
	if sig == SignalBuy {
		b.position += lots
	} else {
		b.position -= lots
	}
}

func (b *Broker) checkCaps(lots int, price float64) error {
	if lots > b.cfg.MaxLots {
		return fmt.Errorf("lots %d exceeds ALGO_MAX_LOTS %d", lots, b.cfg.MaxLots)
	}
	notional := price * float64(lots) * 100 // shares = lots * 100
	if notional > b.cfg.MaxNotional {
		return fmt.Errorf("notional %.0f exceeds ALGO_MAX_NOTIONAL_IDR %.0f", notional, b.cfg.MaxNotional)
	}
	return nil
}

// place either logs (dry-run) or sends a real limit order to Carina.
func (b *Broker) place(sig Signal, lots int, price float64) error {
	shares := lots * 100
	path := "/order/v2/buy"
	if sig == SignalSell {
		path = "/order/v2/sell"
	}
	payload := map[string]interface{}{
		"ui_ref":              genUIRef(),
		"symbol":              b.cfg.Symbol,
		"price":               int(price),
		"shares":              fmt.Sprintf("%d", shares),
		"board_type":          "RG",
		"is_gtc":              false,
		"platform_order_type": "PLATFORM_ORDER_TYPE_LIMIT_DAY",
	}

	if !b.cfg.LiveArmed() {
		log.Printf("[broker] DRY-RUN %s %d lot(s) %s @ %.0f  (POST %s %v)",
			sig, lots, b.cfg.Symbol, price, path, compact(payload))
		return nil
	}

	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", b.cfg.CarinaHost+path, bytes.NewReader(body))
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Authorization", "Bearer "+b.session.TradingToken())
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Platform", "android")
	req.Header.Set("Accept", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(data))
	}
	log.Printf("[broker] 🔴 LIVE %s %d lot(s) %s @ %.0f → %s", sig, lots, b.cfg.Symbol, price, string(data))
	return nil
}

// roundTick snaps a price to a coarse IDX-like tick so demo orders are on-tick.
// (Real tick rules are price-banded; this is a safe approximation for a limit
// order placed near the mid — Carina rejects off-tick prices with 4xx anyway.)
func roundTick(p float64) float64 {
	var tick float64
	switch {
	case p < 200:
		tick = 1
	case p < 500:
		tick = 2
	case p < 2000:
		tick = 5
	case p < 5000:
		tick = 10
	default:
		tick = 25
	}
	return float64(int(p/tick)) * tick
}

func genUIRef() string {
	var r [4]byte
	_, _ = rand.Read(r[:])
	return fmt.Sprintf("A%d%s", time.Now().UnixMilli(), hex.EncodeToString(r[:]))
}

func compact(m map[string]interface{}) string {
	b, _ := json.Marshal(m)
	return string(b)
}
