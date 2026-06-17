package main

import (
	"testing"

	"sb-algo-prototype/internal/feed"
)

func TestConfigHasAuth(t *testing.T) {
	tests := []struct {
		name string
		cfg  feed.Config
		want bool
	}{
		{
			name: "access token only",
			cfg:  feed.Config{AccessToken: "tok"},
			want: true,
		},
		{
			name: "username and password",
			cfg:  feed.Config{Username: "u", Password: "p"},
			want: true,
		},
		{
			name: "token takes precedence over missing creds",
			cfg:  feed.Config{AccessToken: "tok", Username: "u"},
			want: true,
		},
		{
			name: "username without password is insufficient",
			cfg:  feed.Config{Username: "u"},
			want: false,
		},
		{
			name: "nothing set (headless before SSM bootstrap)",
			cfg:  feed.Config{},
			want: false,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c := collectorConfig{feedCfg: tc.cfg}
			if got := c.hasAuth(); got != tc.want {
				t.Errorf("hasAuth() = %v, want %v", got, tc.want)
			}
		})
	}
}
