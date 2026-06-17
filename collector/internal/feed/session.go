package feed

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ErrUnauthorized is returned by GetWebsocketKey when the server responds with
// HTTP 401. Callers can detect it with errors.Is to trigger a token refresh.
var ErrUnauthorized = errors.New("unauthorized")

var httpClient = &http.Client{Timeout: 20 * time.Second}

// Config carries only the fields needed by the feed package.  It is a subset
// of the root prototype's Config — strategy-specific fields are omitted.
type Config struct {
	// Credentials (password login or pre-obtained tokens).
	Username string
	Password string
	TradingPIN string

	// Optional: pre-seeded tokens skip the password/PIN login flows.
	AccessToken         string
	RefreshToken        string
	TradingAccessToken  string
	TradingRefreshToken string

	// Service endpoints.
	ExodusHost string
	CarinaHost string
	WSURL      string
	WSSubProto string
}

// TokenStore persists the rotating token pair across process restarts.
// Implementations must be safe for concurrent use.
//
// Load returns empty strings and a nil error when no persisted tokens exist
// yet (first boot). Save must write atomically so a mid-write crash leaves
// either the old or new state intact.
//
// SECURITY: never include token values in log output.
type TokenStore interface {
	Load() (access, refresh string, err error)
	Save(access, refresh string) error
}

// Session holds both token sets and serialises refreshes with a mutex.
type Session struct {
	cfg   Config
	store TokenStore // may be nil

	mu             sync.RWMutex
	accessToken    string
	refreshToken   string
	tradingToken   string
	tradingRefresh string
	userID         string
}

// NewSession creates a Session from cfg, pre-loading any tokens that were
// already set in cfg.
func NewSession(cfg Config) *Session {
	return &Session{
		cfg:            cfg,
		accessToken:    cfg.AccessToken,
		refreshToken:   cfg.RefreshToken,
		tradingToken:   cfg.TradingAccessToken,
		tradingRefresh: cfg.TradingRefreshToken,
	}
}

// SetTokenStore attaches a store to the session.  After each successful
// RefreshMain call the new token pair is persisted via store.Save.
// It is safe to call this before or after Login.
func (s *Session) SetTokenStore(store TokenStore) {
	s.mu.Lock()
	s.store = store
	s.mu.Unlock()
}

// AccessToken returns the current main access token.
func (s *Session) AccessToken() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.accessToken
}

// TradingToken returns the current trading session access token.
func (s *Session) TradingToken() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.tradingToken
}

// UserID returns the authenticated user's numeric ID as a string.
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

// Login performs the main username/password login.  If an access token is
// already set (either from cfg or a prior call), Login is a no-op — the
// collector's headless boot path relies on this behaviour.
func (s *Session) Login() error {
	if s.accessToken != "" {
		s.ensureUserID()
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
		return fmt.Errorf("login returned no tokens (new-device challenge?): %s", snippet(raw))
	}
	s.mu.Lock()
	s.accessToken = access
	s.refreshToken = refresh
	s.userID = findUserID(raw)
	s.mu.Unlock()
	return nil
}

// RefreshMain refreshes the main access + refresh tokens.  After a
// successful refresh the rotated token pair is persisted via the TokenStore
// (if set).  A persist failure is logged as a warning but does NOT fail the
// refresh — the in-memory session remains valid.
//
// SECURITY: token values must never appear in log output.
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
	newAccess := s.accessToken
	newRefresh := s.refreshToken
	store := s.store
	s.mu.Unlock()

	// Derive userID from the new token if not yet known.
	s.ensureUserID()

	// Persist rotated tokens. A failure here is non-fatal: the in-memory
	// session is valid and the process can continue. Log a warning so the
	// operator knows the next cold boot may require re-login.
	// NOTE: do NOT include token values in the log message.
	if store != nil {
		if err := store.Save(newAccess, newRefresh); err != nil {
			log.Printf("WARN: token persist failed: %v — next restart may require re-login", err)
		}
	}
	return nil
}

// TradingLogin establishes the PIN-gated Carina trading session. Skips if a
// trading access token is already set.
func (s *Session) TradingLogin() error {
	if s.tradingToken != "" {
		return nil
	}
	if s.cfg.TradingPIN == "" {
		return fmt.Errorf("no trading PIN configured")
	}

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

// GetWebsocketKey fetches the per-connection WS key (short-lived; re-fetch
// on every connect).
func (s *Session) GetWebsocketKey() (string, error) {
	req, _ := http.NewRequest("GET", s.cfg.ExodusHost+"/auth/websocket/key", nil)
	s.baseHeaders(req)
	req.Header.Set("Authorization", "Bearer "+s.AccessToken())
	raw, status, err := doJSON(req)
	if err != nil {
		return "", err
	}
	if status == http.StatusUnauthorized {
		return "", fmt.Errorf("websocket key failed (HTTP %d): %s: %w", status, snippet(raw), ErrUnauthorized)
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

// ---------- HTTP helpers ----------

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

// ---------- JSON tree walkers (token extraction) ----------

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
	return strings.TrimSpace(fmt.Sprintf("%.0f", f))
}

// userIDFromJWT extracts the uid from the JWT payload's data.uid field.
// It never logs the token value.
func userIDFromJWT(token string) string {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return ""
	}
	// Try RawURLEncoding first (no padding), fall back to URLEncoding (padded).
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		payload, err = base64.URLEncoding.DecodeString(parts[1])
		if err != nil {
			return ""
		}
	}
	var claims map[string]interface{}
	if err := json.Unmarshal(payload, &claims); err != nil {
		return ""
	}
	data, ok := claims["data"].(map[string]interface{})
	if !ok {
		return ""
	}
	switch uid := data["uid"].(type) {
	case float64:
		return trimFloat(uid)
	case string:
		return uid
	}
	return ""
}

// ensureUserID sets s.userID from the current access token JWT if userID is
// not yet populated. Safe to call without holding s.mu.
func (s *Session) ensureUserID() {
	s.mu.RLock()
	needsSet := s.userID == "" && s.accessToken != ""
	token := s.accessToken
	s.mu.RUnlock()
	if !needsSet {
		return
	}
	uid := userIDFromJWT(token)
	if uid == "" {
		return
	}
	s.mu.Lock()
	if s.userID == "" {
		s.userID = uid
	}
	s.mu.Unlock()
}
