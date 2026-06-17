package main

import (
	"fmt"
	"os"
	"path/filepath"

	"sb-algo-prototype/internal/feed"
)

// collectorVersion is embedded in every manifest sidecar.
const collectorVersion = "0.1.0"

// collectorConfig holds all runtime configuration for the collector.
// All fields are derived from environment variables; no secrets are hardcoded.
type collectorConfig struct {
	// Feed credentials — same env names as prototype/config.go.
	feedCfg feed.Config

	// Path to the FileTokenStore JSON file.
	tokenFile string

	// Capture-specific settings.
	captureDir  string // segment output root (CAPTURE_DIR)
	watchlistID string // short stable id for the symbol set (CAPTURE_WATCHLIST_ID)

	// Watchlist is resolved by loadWatchlist; it is not set during loadConfig.
	symbols []string
}

// loadConfig reads environment variables and returns a collectorConfig.
// It does not validate the watchlist — call loadWatchlist separately.
func loadConfig() (collectorConfig, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		home = "."
	}

	cfg := collectorConfig{
		feedCfg: feed.Config{
			Username:     os.Getenv("SB_USERNAME"),
			Password:     os.Getenv("SB_PASSWORD"),
			AccessToken:  os.Getenv("SB_ACCESS_TOKEN"),
			RefreshToken: os.Getenv("SB_REFRESH_TOKEN"),
			ExodusHost:   envDefault("SB_EXODUS_HOST", "https://exodus.stockbit.com"),
			WSURL:        envDefault("SB_WS_URL", "wss://wss-trading.stockbit.com/ws"),
			WSSubProto:   envDefault("SB_WS_SUBPROTOCOL", "web"),
		},
		tokenFile:   envDefault("SB_TOKEN_FILE", filepath.Join(home, ".sb-tokens.json")),
		captureDir:  envDefault("CAPTURE_DIR", "./segments"),
		watchlistID: envDefault("CAPTURE_WATCHLIST_ID", "default"),
	}

	// At startup we need either pre-obtained tokens or credentials for login.
	haveTokens := cfg.feedCfg.AccessToken != ""
	haveCreds := cfg.feedCfg.Username != "" && cfg.feedCfg.Password != ""
	if !haveTokens && !haveCreds {
		return collectorConfig{}, fmt.Errorf(
			"need SB_USERNAME+SB_PASSWORD or SB_ACCESS_TOKEN to authenticate the market-data feed",
		)
	}
	return cfg, nil
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
