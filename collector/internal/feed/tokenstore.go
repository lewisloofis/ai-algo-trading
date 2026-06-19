package feed

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

// tokenFile is the JSON schema for FileTokenStore.
type tokenFile struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	Version      int64  `json:"version"`
}

// VersionedTokenStore extends TokenStore with monotonic version tracking so
// the session can detect externally-rotated tokens (e.g. seeded from a laptop
// while the collector is live). Detection keys on the refresh parameter version.
//
// Implementations must be safe for concurrent use.
type VersionedTokenStore interface {
	TokenStore
	// LoadVersioned returns the token pair together with the store's current
	// version counter. Returns ("", "", 0, nil) when no tokens exist yet.
	LoadVersioned() (access, refresh string, version int64, err error)
	// SaveVersioned persists the token pair and returns the new version.
	SaveVersioned(access, refresh string) (version int64, err error)
}

// FileTokenStore persists the rotating token pair to a local JSON file.
// The file is written with mode 0600 so only the owning user can read it.
// Treat the refresh token like a password — never commit the file.
//
// Example path: filepath.Join(os.UserHomeDir(), ".sb-tokens.json")
type FileTokenStore struct {
	Path string
}

// Load reads the token pair from the file. If the file does not exist, Load
// returns empty strings and a nil error so the first-boot path is clean.
func (f *FileTokenStore) Load() (access, refresh string, err error) {
	access, refresh, _, err = f.LoadVersioned()
	return
}

// Save writes the token pair to the file with mode 0600. It calls
// os.Chmod after writing to ensure permissions are enforced even if the file
// was created with a different umask.
//
// SECURITY: never log the token values.
func (f *FileTokenStore) Save(access, refresh string) error {
	_, err := f.SaveVersioned(access, refresh)
	return err
}

// LoadVersioned reads the token pair and the persisted version counter.
func (f *FileTokenStore) LoadVersioned() (access, refresh string, version int64, err error) {
	data, err := os.ReadFile(f.Path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", "", 0, nil
		}
		return "", "", 0, err
	}
	var tf tokenFile
	if err := json.Unmarshal(data, &tf); err != nil {
		return "", "", 0, err
	}
	return tf.AccessToken, tf.RefreshToken, tf.Version, nil
}

// SaveVersioned increments the version counter, writes the token pair and new
// version to the file with mode 0600, and returns the new version.
func (f *FileTokenStore) SaveVersioned(access, refresh string) (int64, error) {
	_, _, oldVersion, err := f.LoadVersioned()
	if err != nil {
		return 0, fmt.Errorf("file token store: read before save: %w", err)
	}
	newVersion := oldVersion + 1
	tf := tokenFile{AccessToken: access, RefreshToken: refresh, Version: newVersion}
	data, err := json.Marshal(tf)
	if err != nil {
		return 0, err
	}
	if err := os.WriteFile(f.Path, data, 0600); err != nil {
		return 0, err
	}
	return newVersion, os.Chmod(f.Path, 0600)
}
