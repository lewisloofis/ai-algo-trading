package feed

import (
	"context"
	"fmt"
	"sync"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ssm"
	"github.com/aws/aws-sdk-go-v2/service/ssm/types"
)

// fakeSSMClient is an in-memory fake that implements the ssmAPI interface.
// It never makes network calls.
type fakeSSMClient struct {
	mu       sync.Mutex
	params   map[string]string // name -> value
	versions map[string]int64  // name -> version counter (increments on each Put)
	puts     []string          // ordered record of PutParameter calls (key names)
}

func newFakeSSMClient() *fakeSSMClient {
	return &fakeSSMClient{
		params:   make(map[string]string),
		versions: make(map[string]int64),
	}
}

func (f *fakeSSMClient) GetParameter(ctx context.Context, in *ssm.GetParameterInput, opts ...func(*ssm.Options)) (*ssm.GetParameterOutput, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	name := aws.ToString(in.Name)
	val, ok := f.params[name]
	if !ok {
		return nil, &types.ParameterNotFound{Message: aws.String(fmt.Sprintf("parameter %s not found", name))}
	}
	return &ssm.GetParameterOutput{
		Parameter: &types.Parameter{
			Name:    aws.String(name),
			Value:   aws.String(val),
			Version: f.versions[name],
		},
	}, nil
}

func (f *fakeSSMClient) PutParameter(ctx context.Context, in *ssm.PutParameterInput, opts ...func(*ssm.Options)) (*ssm.PutParameterOutput, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	name := aws.ToString(in.Name)
	f.params[name] = aws.ToString(in.Value)
	f.versions[name]++
	f.puts = append(f.puts, name)
	return &ssm.PutParameterOutput{Version: f.versions[name]}, nil
}

// TestSSMTokenStoreLoadNotFound verifies that Load returns ("","",nil) when
// parameters do not exist yet.
func TestSSMTokenStoreLoadNotFound(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-test")

	access, refresh, err := store.Load()
	if err != nil {
		t.Fatalf("Load unexpected error: %v", err)
	}
	if access != "" || refresh != "" {
		t.Errorf("expected empty tokens, got access=%q refresh=%q", access, refresh)
	}
}

// TestSSMTokenStoreSaveLoad verifies that Save writes both tokens and Load
// reads them back.
func TestSSMTokenStoreSaveLoad(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-test")

	if err := store.Save("access-tok", "refresh-tok"); err != nil {
		t.Fatalf("Save error: %v", err)
	}

	access, refresh, err := store.Load()
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}
	if access != "access-tok" {
		t.Errorf("access: got %q want access-tok", access)
	}
	if refresh != "refresh-tok" {
		t.Errorf("refresh: got %q want refresh-tok", refresh)
	}
}

// TestSSMTokenStoreSaveOrdering verifies that Save writes refresh FIRST then
// access — critical so a crash between the two writes never strands a stale
// refresh token.
func TestSSMTokenStoreSaveOrdering(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-prod")

	if err := store.Save("acc", "ref"); err != nil {
		t.Fatalf("Save error: %v", err)
	}

	if len(client.puts) != 2 {
		t.Fatalf("expected 2 PutParameter calls, got %d", len(client.puts))
	}
	// refresh must come first
	if client.puts[0] != "/sb-prod/refresh" {
		t.Errorf("first PutParameter: got %q want /sb-prod/refresh", client.puts[0])
	}
	if client.puts[1] != "/sb-prod/access" {
		t.Errorf("second PutParameter: got %q want /sb-prod/access", client.puts[1])
	}
}

// TestSSMTokenStoreSaveSkipsEmpty verifies that an empty value is NOT written:
// SSM rejects zero-length values, and overwriting a valid token with "" is data
// loss. An empty refresh must leave the stored refresh untouched.
func TestSSMTokenStoreSaveSkipsEmpty(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-test")

	// Seed an existing refresh token.
	if err := store.Save("acc1", "ref1"); err != nil {
		t.Fatalf("seed Save error: %v", err)
	}

	// Persist only a new access token (empty refresh, as on initial login).
	client.puts = nil
	if err := store.Save("acc2", ""); err != nil {
		t.Fatalf("Save with empty refresh error: %v", err)
	}

	// Only the access parameter should have been written.
	if len(client.puts) != 1 || client.puts[0] != "/sb-test/access" {
		t.Errorf("expected only /sb-test/access written, got %v", client.puts)
	}

	access, refresh, err := store.Load()
	if err != nil {
		t.Fatalf("Load error: %v", err)
	}
	if access != "acc2" {
		t.Errorf("access: got %q want acc2", access)
	}
	if refresh != "ref1" {
		t.Errorf("refresh: got %q want ref1 (must be preserved)", refresh)
	}
}

// TestSSMTokenStoreKeyNames verifies the parameter names used for each token.
func TestSSMTokenStoreKeyNames(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/my-prefix")

	if err := store.Save("a", "r"); err != nil {
		t.Fatalf("Save: %v", err)
	}

	_, refOK := client.params["/my-prefix/refresh"]
	_, accOK := client.params["/my-prefix/access"]
	if !refOK {
		t.Error("expected /my-prefix/refresh in SSM params")
	}
	if !accOK {
		t.Error("expected /my-prefix/access in SSM params")
	}
}

// TestSSMTokenStoreSaveVersionedBumps verifies that each SaveVersioned call
// increments the returned version.
func TestSSMTokenStoreSaveVersionedBumps(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-test")

	v1, err := store.SaveVersioned("acc1", "ref1")
	if err != nil {
		t.Fatalf("SaveVersioned #1 error: %v", err)
	}
	if v1 != 1 {
		t.Errorf("first SaveVersioned: got version %d want 1", v1)
	}

	v2, err := store.SaveVersioned("acc2", "ref2")
	if err != nil {
		t.Fatalf("SaveVersioned #2 error: %v", err)
	}
	if v2 != 2 {
		t.Errorf("second SaveVersioned: got version %d want 2", v2)
	}
	if v2 <= v1 {
		t.Errorf("expected version to increase: v1=%d v2=%d", v1, v2)
	}
}

// TestSSMTokenStoreLoadVersionedReportsVersion verifies that LoadVersioned
// returns the current SSM version of the refresh parameter.
func TestSSMTokenStoreLoadVersionedReportsVersion(t *testing.T) {
	t.Parallel()

	client := newFakeSSMClient()
	store := newSSMTokenStoreWithClient(client, "/sb-test")

	// No parameters yet — version should be 0.
	_, _, v0, err := store.LoadVersioned()
	if err != nil {
		t.Fatalf("LoadVersioned (empty) error: %v", err)
	}
	if v0 != 0 {
		t.Errorf("empty store: got version %d want 0", v0)
	}

	// Save once — version should be 1.
	savedV, err := store.SaveVersioned("acc", "ref")
	if err != nil {
		t.Fatalf("SaveVersioned error: %v", err)
	}

	_, _, loadedV, err := store.LoadVersioned()
	if err != nil {
		t.Fatalf("LoadVersioned after save error: %v", err)
	}
	if loadedV != savedV {
		t.Errorf("LoadVersioned version: got %d want %d", loadedV, savedV)
	}

	// Save again — version should advance.
	savedV2, err := store.SaveVersioned("acc2", "ref2")
	if err != nil {
		t.Fatalf("SaveVersioned #2 error: %v", err)
	}
	_, _, loadedV2, err := store.LoadVersioned()
	if err != nil {
		t.Fatalf("LoadVersioned after save #2 error: %v", err)
	}
	if loadedV2 != savedV2 {
		t.Errorf("LoadVersioned version after #2: got %d want %d", loadedV2, savedV2)
	}
}
