package main

// Authentication against Exodus (main session) and Carina (trading session),
// following docs/01-authentication.md. Tokens are parsed defensively because
// the JSON nesting differs slightly between client/API versions.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

var httpClient = &http.Client{Timeout: 20 * time.Second}

// Session holds both token sets and serialises refreshes.
type Session struct {
	cfg Config

	mu             sync.RWMutex
	accessToken    string
	refreshToken   string
	tradingToken   string
	tradingRefresh string
	userID         string
}

func NewSession(cfg Config) *Session {
	return &Session{
		cfg:            cfg,
		accessToken:    cfg.AccessToken,
		refreshToken:   cfg.RefreshToken,
		tradingToken:   cfg.TradingAccessToken,
		tradingRefresh: cfg.TradingRefreshToken,
	}
}

func (s *Session) AccessToken() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.accessToken
}

func (s *Session) TradingToken() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.tradingToken
}

func (s *Session) UserID() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.userID
}

func (s *Session) baseHeaders(req *http.Request) {
	req.Header.Set("X-Platform", "android")
	req.Header.Set("X-AppVersion", "7.0.0")
	req.Header.Set("X-DeviceType", "algo-prototype")
	req.Header.Set("Accept-Language", "id")
	req.Header.Set("Accept", "application/json")
}

// Login performs the main username/password login (skips if a token is preset).
func (s *Session) Login() error {
	if s.accessToken != "" {
		return nil
	}
	body, _ := json.Marshal(map[string]string{
		"user":     s.cfg.Username,
		"password": s.cfg.Password,
	})
	req, _ := http.NewRequest("POST", s.cfg.ExodusHost+"/login/v6/username", bytes.NewReader(body))
	s.baseHeaders(req)
	req.Header.Set("Content-Type", "application/json")

	raw, status, err := doJSON(req)
	if err != nil {
		return fmt.Errorf("login request: %w", err)
	}
	if status != http.StatusOK {
		return fmt.Errorf("login failed (HTTP %d): %s", status, snippet(raw))
	}
	access, refresh, ok := findTokenPair(raw)
	if !ok {
		// Likely a new-device / challenge response, or unexpected shape.
		return fmt.Errorf("login returned no tokens (new-device challenge?): %s", snippet(raw))
	}
	s.mu.Lock()
	s.accessToken = access
	s.refreshToken = refresh
	s.userID = findUserID(raw)
	s.mu.Unlock()
	return nil
}

// RefreshMain refreshes the main access token.
func (s *Session) RefreshMain() error {
	s.mu.RLock()
	rt := s.refreshToken
	s.mu.RUnlock()
	if rt == "" {
		return fmt.Errorf("no refresh token available")
	}
	req, _ := http.NewRequest("POST", s.cfg.ExodusHost+"/login/refresh", nil)
	s.baseHeaders(req)
	req.Header.Set("Authorization", "Bearer "+rt)

	raw, status, err := doJSON(req)
	if err != nil {
		return err
	}
	if status == http.StatusUnauthorized {
		return fmt.Errorf("refresh token expired — full login required")
	}
	if status != http.StatusOK {
		return fmt.Errorf("refresh failed (HTTP %d): %s", status, snippet(raw))
	}
	access, refresh, ok := findTokenPair(raw)
	if !ok {
		return fmt.Errorf("refresh returned no tokens: %s", snippet(raw))
	}
	s.mu.Lock()
	s.accessToken = access
	if refresh != "" {
		s.refreshToken = refresh
	}
	s.mu.Unlock()
	return nil
}

// TradingLogin establishes the PIN-gated trading session (skips if preset).
func (s *Session) TradingLogin() error {
	if s.tradingToken != "" {
		return nil
	}
	if s.cfg.TradingPIN == "" {
		return fmt.Errorf("no trading PIN configured")
	}

	// Step 1: get a trading login token.
	req, _ := http.NewRequest("GET", s.cfg.ExodusHost+"/sekuritas/auth/token", nil)
	s.baseHeaders(req)
	req.Header.Set("Authorization", "Bearer "+s.AccessToken())
	raw, status, err := doJSON(req)
	if err != nil {
		return fmt.Errorf("sekuritas token: %w", err)
	}
	if status != http.StatusOK {
		return fmt.Errorf("sekuritas token failed (HTTP %d): %s", status, snippet(raw))
	}
	loginToken := findString(raw, "token", "login_token")
	if loginToken == "" {
		return fmt.Errorf("no login_token in sekuritas response: %s", snippet(raw))
	}

	// Step 2: verify the PIN against Carina NEW_CORE.
	body, _ := json.Marshal(map[string]string{"login_token": loginToken, "pin": s.cfg.TradingPIN})
	req2, _ := http.NewRequest("POST", s.cfg.CarinaHost+"/auth/v2/login", bytes.NewReader(body))
	s.baseHeaders(req2)
	req2.Header.Set("Content-Type", "application/json")
	raw2, status2, err := doJSON(req2)
	if err != nil {
		return fmt.Errorf("trading pin login: %w", err)
	}
	if status2 != http.StatusOK {
		return fmt.Errorf("trading pin login failed (HTTP %d): %s", status2, snippet(raw2))
	}
	at := findString(raw2, "access_token")
	rt := findString(raw2, "refresh_token")
	if at == "" {
		return fmt.Errorf("no trading access_token in response: %s", snippet(raw2))
	}
	s.mu.Lock()
	s.tradingToken = at
	s.tradingRefresh = rt
	s.mu.Unlock()
	return nil
}

// GetWebsocketKey fetches the per-connection key used to authenticate the
// market-data socket (GET /auth/websocket/key with the main access token).
func (s *Session) GetWebsocketKey() (string, error) {
	req, _ := http.NewRequest("GET", s.cfg.ExodusHost+"/auth/websocket/key", nil)
	s.baseHeaders(req)
	req.Header.Set("Authorization", "Bearer "+s.AccessToken())
	raw, status, err := doJSON(req)
	if err != nil {
		return "", err
	}
	if status != http.StatusOK {
		return "", fmt.Errorf("websocket key failed (HTTP %d): %s", status, snippet(raw))
	}
	key := findString(raw, "key", "websocket_key", "ws_key")
	if key == "" {
		return "", fmt.Errorf("no key in websocket-key response: %s", snippet(raw))
	}
	return key, nil
}

// ---------- helpers ----------

func doJSON(req *http.Request) (map[string]interface{}, int, error) {
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var m map[string]interface{}
	_ = json.Unmarshal(data, &m)
	return m, resp.StatusCode, nil
}

func snippet(m map[string]interface{}) string {
	b, _ := json.Marshal(m)
	s := string(b)
	if len(s) > 300 {
		s = s[:300] + "…"
	}
	return s
}

// findTokenPair walks the JSON looking for a node holding both `access` and
// `refresh` objects, each with a `token` string.
func findTokenPair(node interface{}) (access, refresh string, ok bool) {
	m, isMap := node.(map[string]interface{})
	if !isMap {
		return "", "", false
	}
	if a, ok1 := m["access"].(map[string]interface{}); ok1 {
		if r, ok2 := m["refresh"].(map[string]interface{}); ok2 {
			at, _ := a["token"].(string)
			rt, _ := r["token"].(string)
			if at != "" {
				return at, rt, true
			}
		}
	}
	for _, v := range m {
		if a, r, found := findTokenPair(v); found {
			return a, r, true
		}
	}
	return "", "", false
}

func findUserID(node interface{}) string {
	if m, ok := node.(map[string]interface{}); ok {
		if u, ok := m["user"].(map[string]interface{}); ok {
			switch id := u["id"].(type) {
			case float64:
				return trimFloat(id)
			case string:
				return id
			}
		}
		for _, v := range m {
			if id := findUserID(v); id != "" {
				return id
			}
		}
	}
	return ""
}

// findString searches the JSON tree for the first of the given keys whose value
// is a non-empty string.
func findString(node interface{}, keys ...string) string {
	m, ok := node.(map[string]interface{})
	if !ok {
		return ""
	}
	for _, k := range keys {
		if s, ok := m[k].(string); ok && s != "" {
			return s
		}
	}
	for _, v := range m {
		if s := findString(v, keys...); s != "" {
			return s
		}
	}
	return ""
}

func trimFloat(f float64) string {
	s := fmt.Sprintf("%.0f", f)
	return strings.TrimSpace(s)
}
