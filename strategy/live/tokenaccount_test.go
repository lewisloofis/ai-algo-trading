package live

import (
	"encoding/base64"
	"errors"
	"strings"
	"testing"
)

func makeToken(payload string, pad bool) string {
	enc := base64.RawURLEncoding.EncodeToString([]byte(payload))
	if pad {
		for len(enc)%4 != 0 {
			enc += "="
		}
	}
	return "hdr." + enc + ".sig"
}

func TestTokenAccount(t *testing.T) {
	const ok = `{"aud":"x","data":{"acn":"TEST01","act":"a"},"exp":1}`
	tests := []struct {
		name    string
		token   string
		want    string
		wantErr bool
	}{
		{"valid unpadded", makeToken(ok, false), "TEST01", false},
		{"valid padded", makeToken(ok, true), "TEST01", false},
		{"bearer prefix", "Bearer " + makeToken(ok, false), "TEST01", false},
		{"quotes", `"` + makeToken(ok, false) + `"`, "TEST01", false},
		{"whitespace", "  \n" + makeToken(ok, false) + " \t", "TEST01", false},
		{"quoted bearer", `"Bearer ` + makeToken(ok, false) + `"`, "TEST01", false},
		{"empty", "", "", true},
		{"two parts", "a.b", "", true},
		{"four parts", "a.b.c.d", "", true},
		{"bad base64", "hdr.!!!notb64.sig", "", true},
		{"bad json", makeToken(`{not json`, false), "", true},
		{"data missing", makeToken(`{"aud":"x"}`, false), "", true},
		{"data null", makeToken(`{"data":null}`, false), "", true},
		{"data not object", makeToken(`{"data":"str"}`, false), "", true},
		{"acn missing", makeToken(`{"data":{"act":"a"}}`, false), "", true},
		{"acn empty", makeToken(`{"data":{"acn":""}}`, false), "", true},
		{"acn not string", makeToken(`{"data":{"acn":5}}`, false), "", true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := TokenAccount(tc.token)
			if tc.wantErr {
				if !errors.Is(err, ErrTokenAccountUnknown) {
					t.Fatalf("err = %v, want ErrTokenAccountUnknown", err)
				}
				if tc.token != "" && strings.Contains(err.Error(), tc.token) {
					t.Fatalf("error leaks token: %v", err)
				}
				return
			}
			if err != nil || got != tc.want {
				t.Fatalf("got (%q, %v), want %q", got, err, tc.want)
			}
		})
	}
}
