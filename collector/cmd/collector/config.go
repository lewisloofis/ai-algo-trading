package main

import (
	"os"
	"path/filepath"

	"collector/internal/feed"
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

	// Note: authentication is validated by hasAuth() AFTER the token store is
	// consulted (env vars alone are not required — headless EC2 seeds tokens via
	// SSM, and dev runs may load them from the file store).
	return cfg, nil
}

// hasAuth reports whether the config can authenticate the feed: either a
// pre-obtained access token (from env OR a token store) or username+password
// for a fresh login.
func (c collectorConfig) hasAuth() bool {
	if c.feedCfg.AccessToken != "" {
		return true
	}
	return c.feedCfg.Username != "" && c.feedCfg.Password != ""
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
