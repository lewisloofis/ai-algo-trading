package feed

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

// ---------- helpers ----------

// makeTokenResponse returns a minimal JSON response that findTokenPair can parse.
func makeTokenResponse(access, refresh string) map[string]interface{} {
	return map[string]interface{}{
		"data": map[string]interface{}{
			"login": map[string]interface{}{
				"access": map[string]interface{}{
					"token": access,
				},
				"refresh": map[string]interface{}{
					"token": refresh,
				},
			},
		},
	}
}

// ---------- fakeVersionedStore ----------

// fakeVersionedStore is an in-memory VersionedTokenStore for session tests.
type fakeVersionedStore struct {
	access  string
	refresh string
	version int64
}

func (f *fakeVersionedStore) Load() (string, string, error) {
	a, r, _, err := f.LoadVersioned()
	return a, r, err
}

func (f *fakeVersionedStore) Save(access, refresh string) error {
	_, err := f.SaveVersioned(access, refresh)
	return err
}

func (f *fakeVersionedStore) LoadVersioned() (access, refresh string, version int64, err error) {
	return f.access, f.refresh, f.version, nil
}

func (f *fakeVersionedStore) SaveVersioned(access, refresh string) (int64, error) {
	f.access = access
	f.refresh = refresh
	f.version++
	return f.version, nil
}

// ---------- fakeSimpleStore ----------

// fakeSimpleStore implements only TokenStore (not VersionedTokenStore) for
// testing the non-versioned no-op path.
type fakeSimpleStore struct {
	access  string
	refresh string
}

func (f *fakeSimpleStore) Load() (string, string, error) { return f.access, f.refresh, nil }
func (f *fakeSimpleStore) Save(a, r string) error        { f.access = a; f.refresh = r; return nil }

// ---------- ReconcileFromStore tests ----------

func TestReconcileFromStoreNoopUnchangedVersion(t *testing.T) {
	t.Parallel()

	store := &fakeVersionedStore{access: "acc1", refresh: "ref1", version: 5}
	s := NewSession(Config{AccessToken: "acc1", RefreshToken: "ref1"})
	s.SetTokenStore(store)
	s.SetStoreVersion(5) // same as store version

	adopted, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if adopted {
		t.Error("expected no adoption when store version matches lastStoreVersion")
	}
}

func TestReconcileFromStoreAdoptsExternalBump(t *testing.T) {
	t.Parallel()

	store := &fakeVersionedStore{access: "new-acc", refresh: "new-ref", version: 7}
	s := NewSession(Config{AccessToken: "old-acc", RefreshToken: "old-ref"})
	s.SetTokenStore(store)
	s.SetStoreVersion(5) // older than store version

	adopted, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !adopted {
		t.Fatal("expected adoption when store version > lastStoreVersion and refresh differs")
	}
	if got := s.AccessToken(); got != "new-acc" {
		t.Errorf("AccessToken after reconcile: got %q want new-acc", got)
	}
	// Verify lastStoreVersion advanced: a second reconcile with the same store
	// version should be a no-op.
	store2 := &fakeVersionedStore{access: "new-acc", refresh: "new-ref", version: 7}
	s.SetTokenStore(store2)
	adopted2, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("second reconcile error: %v", err)
	}
	if adopted2 {
		t.Error("second reconcile with same version should be no-op")
	}
}

func TestReconcileFromStoreNoopNonVersionedStore(t *testing.T) {
	t.Parallel()

	store := &fakeSimpleStore{access: "acc", refresh: "ref"}
	s := NewSession(Config{AccessToken: "acc", RefreshToken: "ref"})
	s.SetTokenStore(store)

	adopted, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if adopted {
		t.Error("expected no adoption for non-versioned store")
	}
}

func TestReconcileFromStoreNoopSameRefresh(t *testing.T) {
	t.Parallel()

	// Version is higher but refresh is the same — should not adopt.
	store := &fakeVersionedStore{access: "acc", refresh: "same-ref", version: 10}
	s := NewSession(Config{AccessToken: "acc", RefreshToken: "same-ref"})
	s.SetTokenStore(store)
	s.SetStoreVersion(5)

	adopted, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if adopted {
		t.Error("expected no adoption when refresh token is unchanged")
	}
}

// ---------- RefreshMain ErrRefreshExpired test ----------

func TestRefreshMainReturnsErrRefreshExpiredOn401(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/refresh" {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"message":"refresh token expired"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	s := NewSession(Config{
		ExodusHost:   srv.URL,
		RefreshToken: "some-refresh-token",
	})

	err := s.RefreshMain()
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if !errors.Is(err, ErrRefreshExpired) {
		t.Errorf("expected ErrRefreshExpired, got: %v", err)
	}
}

// ---------- Login player_id test ----------

func TestLoginIncludesPlayerIDOmitsSignature(t *testing.T) {
	t.Parallel()

	var capturedBody map[string]interface{}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/v6/username" {
			_ = json.NewDecoder(r.Body).Decode(&capturedBody)
			resp := makeTokenResponse("the-access-token", "the-refresh-token")
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	s := NewSession(Config{
		ExodusHost: srv.URL,
		Username:   "testuser",
		Password:   "testpass",
		PlayerID:   "test-player-uuid",
	})

	if err := s.Login(); err != nil {
		t.Fatalf("Login error: %v", err)
	}

	if capturedBody == nil {
		t.Fatal("no request body captured")
	}
	pid, _ := capturedBody["player_id"].(string)
	if pid != "test-player-uuid" {
		t.Errorf("player_id: got %q want test-player-uuid", pid)
	}
	if _, hasSignature := capturedBody["signature"]; hasSignature {
		t.Error("signature must not be present in login request")
	}
}

// ---------- Relogin ErrLoginChallenge test ----------

func TestReloginSurfacesErrLoginChallengeOnNewDevice(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/v6/username" {
			resp := map[string]interface{}{
				"message": "New device detected",
				"data": map[string]interface{}{
					"new_device": map[string]interface{}{
						"multi_factor": map[string]interface{}{
							"login_token":        "lt-abc",
							"verification_token": "vt-xyz",
						},
					},
				},
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	s := NewSession(Config{
		ExodusHost:  srv.URL,
		Username:    "testuser",
		Password:    "testpass",
		PlayerID:    "untrusted-player-uuid",
		AccessToken: "old-token", // must be cleared by Relogin
	})

	err := s.Relogin()
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if !errors.Is(err, ErrLoginChallenge) {
		t.Errorf("expected ErrLoginChallenge, got: %v", err)
	}
}

// TestReloginPersistsTokensViaSaveVersioned verifies that a successful Relogin
// persists the freshly-issued token pair through the store using SaveVersioned
// and advances the recorded lastStoreVersion — so a cold boot after a headless
// re-login reflects the live tokens.
func TestReloginPersistsTokensViaSaveVersioned(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/v6/username" {
			resp := makeTokenResponse("relogin-access", "relogin-refresh")
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	store := &fakeVersionedStore{access: "old-acc", refresh: "old-ref", version: 3}
	s := NewSession(Config{
		ExodusHost:  srv.URL,
		Username:    "user",
		Password:    "pass",
		PlayerID:    "trusted-uuid",
		AccessToken: "stale-token", // must be cleared so doLogin runs
	})
	s.SetTokenStore(store)
	s.SetStoreVersion(3)

	if err := s.Relogin(); err != nil {
		t.Fatalf("Relogin error: %v", err)
	}

	// The new pair must be persisted through SaveVersioned.
	if store.access != "relogin-access" {
		t.Errorf("store access: got %q want relogin-access", store.access)
	}
	if store.refresh != "relogin-refresh" {
		t.Errorf("store refresh: got %q want relogin-refresh", store.refresh)
	}
	// SaveVersioned bumped the fake store from version 3 to 4.
	if store.version != 4 {
		t.Errorf("store version: got %d want 4 (SaveVersioned must bump)", store.version)
	}

	// lastStoreVersion must advance so a subsequent ReconcileFromStore at the
	// same version is a no-op (proves the version was recorded internally).
	adopted, err := s.ReconcileFromStore()
	if err != nil {
		t.Fatalf("ReconcileFromStore error: %v", err)
	}
	if adopted {
		t.Error("expected no adoption: lastStoreVersion should have advanced to the post-Relogin version")
	}
}

// TestLoginNoPlayerIDOmitsPlayerIDField verifies that when PlayerID is empty,
// the player_id field is not sent.
func TestLoginNoPlayerIDOmitsPlayerIDField(t *testing.T) {
	t.Parallel()

	var capturedBody map[string]interface{}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/v6/username" {
			_ = json.NewDecoder(r.Body).Decode(&capturedBody)
			resp := makeTokenResponse("acc", "ref")
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	s := NewSession(Config{
		ExodusHost: srv.URL,
		Username:   "user",
		Password:   "pass",
		// PlayerID intentionally empty
	})

	if err := s.Login(); err != nil {
		t.Fatalf("Login error: %v", err)
	}

	if _, has := capturedBody["player_id"]; has {
		t.Error("player_id should not be present when PlayerID is empty")
	}
	if _, has := capturedBody["signature"]; has {
		t.Error("signature must not be present")
	}
}

// TestRefreshMainSavesVersionedWhenSupported verifies that RefreshMain calls
// SaveVersioned (not Save) when the store implements VersionedTokenStore, and
// that it advances lastStoreVersion.
func TestRefreshMainSavesVersionedWhenSupported(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/login/refresh" {
			resp := makeTokenResponse("new-access", "new-refresh")
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()

	store := &fakeVersionedStore{}
	s := NewSession(Config{
		ExodusHost:   srv.URL,
		RefreshToken: "old-refresh",
	})
	s.SetTokenStore(store)

	if err := s.RefreshMain(); err != nil {
		t.Fatalf("RefreshMain error: %v", err)
	}

	if store.version == 0 {
		t.Error("expected SaveVersioned to be called (version still 0)")
	}
	if store.access != "new-access" {
		t.Errorf("store access: got %q want new-access", store.access)
	}
}

// TestLoginAlreadyHasToken verifies that Login is a no-op when an access token
// is already set.
func TestLoginAlreadyHasToken(t *testing.T) {
	t.Parallel()

	callCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		callCount++
		http.NotFound(w, r)
	}))
	defer srv.Close()

	s := NewSession(Config{
		ExodusHost:  srv.URL,
		AccessToken: "existing-token",
	})

	if err := s.Login(); err != nil {
		t.Fatalf("Login error: %v", err)
	}
	if callCount > 0 {
		t.Errorf("Login should be no-op when access token is set, but made %d HTTP call(s)", callCount)
	}
}

// ---------- hasLoginChallenge helpers ----------

func TestHasLoginChallengeDetectsNewDevice(t *testing.T) {
	t.Parallel()

	raw := map[string]interface{}{
		"data": map[string]interface{}{
			"new_device": map[string]interface{}{
				"multi_factor": map[string]interface{}{
					"login_token": "lt",
				},
			},
		},
	}
	if !hasLoginChallenge(raw) {
		t.Error("expected hasLoginChallenge=true for new_device response")
	}
}

func TestHasLoginChallengeDetectsUnfreeze(t *testing.T) {
	t.Parallel()

	raw := map[string]interface{}{
		"data": map[string]interface{}{
			"unfreeze": map[string]interface{}{
				"login_token": "lt",
			},
		},
	}
	if !hasLoginChallenge(raw) {
		t.Error("expected hasLoginChallenge=true for unfreeze response")
	}
}

func TestHasLoginChallengeNegativeForTokenPair(t *testing.T) {
	t.Parallel()

	raw := makeTokenResponse("acc", "ref")
	if hasLoginChallenge(raw) {
		t.Error("expected hasLoginChallenge=false for normal token-pair response")
	}
}
