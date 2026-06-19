package feed

import (
	"testing"
)

// TestSSMParamReaderGetSecurePresent verifies that GetSecure returns the stored
// value when the parameter exists under the expected key.
func TestSSMParamReaderGetSecurePresent(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	client.params["/sb-test/player_id"] = "uuid-abc-123"

	reader := newSSMParamReaderWithClient(client, "/sb-test")

	got, err := reader.GetSecure("player_id")
	if err != nil {
		t.Fatalf("GetSecure unexpected error: %v", err)
	}
	if got != "uuid-abc-123" {
		t.Errorf("GetSecure: got %q, want %q", got, "uuid-abc-123")
	}
}

// TestSSMParamReaderGetSecureNotFound verifies that GetSecure returns ("", nil)
// when the parameter does not exist.
func TestSSMParamReaderGetSecureNotFound(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	reader := newSSMParamReaderWithClient(client, "/sb-test")

	got, err := reader.GetSecure("player_id")
	if err != nil {
		t.Fatalf("GetSecure unexpected error: %v", err)
	}
	if got != "" {
		t.Errorf("GetSecure: got %q, want empty string", got)
	}
}

// TestSSMParamReaderGetPresent verifies that Get returns the stored value when
// the parameter exists under the expected key.
func TestSSMParamReaderGetPresent(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	client.params["/sb-test/watchlist"] = "ADRO,PTBA"

	reader := newSSMParamReaderWithClient(client, "/sb-test")

	got, err := reader.Get("watchlist")
	if err != nil {
		t.Fatalf("Get unexpected error: %v", err)
	}
	if got != "ADRO,PTBA" {
		t.Errorf("Get: got %q, want %q", got, "ADRO,PTBA")
	}
}

// TestSSMParamReaderGetNotFound verifies that Get returns ("", nil) when the
// parameter does not exist — the ParameterNotFound sentinel is converted to an
// empty fallback, not an error.
func TestSSMParamReaderGetNotFound(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	reader := newSSMParamReaderWithClient(client, "/sb-test")

	got, err := reader.Get("watchlist")
	if err != nil {
		t.Fatalf("Get unexpected error: %v", err)
	}
	if got != "" {
		t.Errorf("Get: got %q, want empty string", got)
	}
}

// TestSSMParamReaderKeyName verifies that Get constructs the full SSM key as
// "<prefix>/<name>" — no double-slash, no missing separator.
func TestSSMParamReaderKeyName(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	// Seed using the exact key that Get should construct.
	client.params["/my-prefix/watchlist_id"] = "bluechip"

	reader := newSSMParamReaderWithClient(client, "/my-prefix")

	got, err := reader.Get("watchlist_id")
	if err != nil {
		t.Fatalf("Get unexpected error: %v", err)
	}
	if got != "bluechip" {
		t.Errorf("Get: got %q, want %q", got, "bluechip")
	}

	// Also confirm the key absent under a wrong path returns empty, not the
	// seeded value — i.e. the prefix is actually used.
	reader2 := newSSMParamReaderWithClient(client, "/wrong-prefix")
	got2, err := reader2.Get("watchlist_id")
	if err != nil {
		t.Fatalf("Get (wrong prefix) unexpected error: %v", err)
	}
	if got2 != "" {
		t.Errorf("Get (wrong prefix): got %q, want empty string", got2)
	}
}
