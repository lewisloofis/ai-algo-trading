package tokenacct

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// ErrUnknown is wrapped by every TokenAccount failure: the
// account a trading token routes to could not be determined.
var ErrUnknown = errors.New("tokenacct: trading token account unknown")

// Account returns the brokerage account (the JWT payload's data.acn
// claim) that orders placed with this trading token route to. Carina order
// requests carry no account field; routing follows this claim (observed
// 2026-10-14, see doc.go).
//
// This is a routing-safety check only: the signature is NOT verified and the
// result must never be used for authentication or authorization. Error
// messages never include the token or any payload contents.
func Account(token string) (string, error) {
	t := strings.TrimSpace(token)
	t = strings.Trim(t, `"`)
	t = strings.TrimSpace(t)
	t = strings.TrimPrefix(t, "Bearer ")
	t = strings.TrimSpace(t)

	parts := strings.Split(t, ".")
	if len(parts) != 3 {
		return "", fmt.Errorf("%w: token is not a three-part JWT", ErrUnknown)
	}
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return "", fmt.Errorf("%w: payload is not valid base64url", ErrUnknown)
	}
	var payload struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return "", fmt.Errorf("%w: payload is not valid JSON", ErrUnknown)
	}
	if len(payload.Data) == 0 || string(payload.Data) == "null" {
		return "", fmt.Errorf("%w: payload has no data claim", ErrUnknown)
	}
	var data struct {
		Acn string `json:"acn"`
	}
	if err := json.Unmarshal(payload.Data, &data); err != nil {
		return "", fmt.Errorf("%w: data claim is not an object with a string acn", ErrUnknown)
	}
	acn := strings.TrimSpace(data.Acn)
	if acn == "" {
		return "", fmt.Errorf("%w: data.acn is missing or empty", ErrUnknown)
	}
	return acn, nil
}
