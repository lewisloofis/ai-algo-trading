package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// liveConfirmPhrase must be set verbatim in ALGO_CONFIRM to arm live trading.
const liveConfirmPhrase = "I_UNDERSTAND_LIVE_TRADING"

// Config is assembled from environment variables. Secrets are NEVER hardcoded.
type Config struct {
	// Credentials (either password login or pre-obtained tokens).
	Username   string
	Password   string
	TradingPIN string

	AccessToken         string // optional: skip password login
	RefreshToken        string
	TradingAccessToken  string // optional: skip PIN login
	TradingRefreshToken string

	// Hosts.
	ExodusHost string
	CarinaHost string
	WSURL      string
	WSSubProto string

	// Strategy.
	Symbol          string
	ImbalanceThresh float64
	DepthLevels     int

	// Risk / execution gates.
	LotsPerOrder int
	CooldownSec  int
	MaxOrders    int
	MaxLots      int
	MaxNotional  float64 // IDR per order

	Live    bool
	Confirm string
}

// LiveArmed reports whether real orders may actually be sent.
func (c Config) LiveArmed() bool {
	return c.Live && c.Confirm == liveConfirmPhrase
}

func loadConfig() (Config, error) {
	c := Config{
		Username:            os.Getenv("SB_USERNAME"),
		Password:            os.Getenv("SB_PASSWORD"),
		TradingPIN:          os.Getenv("SB_TRADING_PIN"),
		AccessToken:         os.Getenv("SB_ACCESS_TOKEN"),
		RefreshToken:        os.Getenv("SB_REFRESH_TOKEN"),
		TradingAccessToken:  os.Getenv("SB_TRADING_ACCESS_TOKEN"),
		TradingRefreshToken: os.Getenv("SB_TRADING_REFRESH_TOKEN"),
		ExodusHost:          envDefault("SB_EXODUS_HOST", "https://exodus.stockbit.com"),
		CarinaHost:          envDefault("SB_CARINA_HOST", "https://carina.stockbit.com"),
		WSURL:               envDefault("SB_WS_URL", "wss://wss-trading.stockbit.com/ws"),
		WSSubProto:          envDefault("SB_WS_SUBPROTOCOL", "web"),
		Symbol:              strings.ToUpper(envDefault("SB_SYMBOL", "BBCA")),
		ImbalanceThresh:     envFloat("ALGO_IMBALANCE_THRESHOLD", 0.30),
		DepthLevels:         envInt("ALGO_DEPTH_LEVELS", 5),
		LotsPerOrder:        envInt("ALGO_LOTS_PER_ORDER", 1),
		CooldownSec:         envInt("ALGO_COOLDOWN_SEC", 30),
		MaxOrders:           envInt("ALGO_MAX_ORDERS", 5),
		MaxLots:             envInt("ALGO_MAX_LOTS", 5),
		MaxNotional:         envFloat("ALGO_MAX_NOTIONAL_IDR", 10_000_000),
		Live:                envBool("ALGO_LIVE", false),
		Confirm:             os.Getenv("ALGO_CONFIRM"),
	}

	haveMain := c.AccessToken != "" || (c.Username != "" && c.Password != "")
	if !haveMain {
		return c, fmt.Errorf("need SB_USERNAME+SB_PASSWORD or SB_ACCESS_TOKEN to authenticate the market-data feed")
	}
	if c.LotsPerOrder > c.MaxLots {
		return c, fmt.Errorf("ALGO_LOTS_PER_ORDER (%d) exceeds ALGO_MAX_LOTS (%d)", c.LotsPerOrder, c.MaxLots)
	}
	if c.Live {
		haveTrading := c.TradingAccessToken != "" || (haveMain && c.TradingPIN != "")
		if !haveTrading {
			return c, fmt.Errorf("ALGO_LIVE set but no trading session: provide SB_TRADING_PIN or SB_TRADING_ACCESS_TOKEN")
		}
	}
	return c, nil
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v := os.Getenv(key); v != "" {
		b, err := strconv.ParseBool(v)
		if err == nil {
			return b
		}
	}
	return def
}
