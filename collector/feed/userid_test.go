package feed

import (
	"encoding/base64"
	"encoding/json"
	"testing"
)

func makeJWT(payload interface{}) string {
	b, _ := json.Marshal(payload)
	return "header." + base64.RawURLEncoding.EncodeToString(b) + ".sig"
}

func TestUserIDFromJWT(t *testing.T) {
	tests := []struct {
		name  string
		token string
		want  string
	}{
		{
			name:  "uid as number",
			token: makeJWT(map[string]interface{}{"data": map[string]interface{}{"uid": 17301}}),
			want:  "17301",
		},
		{
			name:  "uid as string",
			token: makeJWT(map[string]interface{}{"data": map[string]interface{}{"uid": "42"}}),
			want:  "42",
		},
		{
			name:  "malformed — not three segments",
			token: "onlyone",
			want:  "",
		},
		{
			name:  "payload without data.uid",
			token: makeJWT(map[string]interface{}{"sub": "user"}),
			want:  "",
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := userIDFromJWT(tc.token)
			if got != tc.want {
				t.Errorf("userIDFromJWT(%q) = %q, want %q", tc.token, got, tc.want)
			}
		})
	}
}
