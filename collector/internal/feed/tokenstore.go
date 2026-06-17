package feed

import (
	"encoding/json"
	"errors"
	"os"
)

// tokenFile is the JSON schema for FileTokenStore.
type tokenFile struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
}

// FileTokenStore persists the rotating token pair to a local JSON file.
// The file is written with mode 0600 so only the owning user can read it.
// Treat the refresh token like a password — never commit the file.
//
// Example path: filepath.Join(os.UserHomeDir(), ".sb-tokens.json")
type FileTokenStore struct {
	Path string
}

// Load reads the token pair from the file.  If the file does not exist, Load
// returns empty strings and a nil error so the first-boot path is clean.
func (f *FileTokenStore) Load() (access, refresh string, err error) {
	data, err := os.ReadFile(f.Path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", "", nil
		}
		return "", "", err
	}
	var tf tokenFile
	if err := json.Unmarshal(data, &tf); err != nil {
		return "", "", err
	}
	return tf.AccessToken, tf.RefreshToken, nil
}

// Save writes the token pair to the file with mode 0600.  It also calls
// os.Chmod after writing to ensure the permissions are enforced even if the
// file was created with a different umask.
//
// SECURITY: never log the token values.
func (f *FileTokenStore) Save(access, refresh string) error {
	tf := tokenFile{AccessToken: access, RefreshToken: refresh}
	data, err := json.Marshal(tf)
	if err != nil {
		return err
	}
	if err := os.WriteFile(f.Path, data, 0600); err != nil {
		return err
	}
	return os.Chmod(f.Path, 0600)
}
