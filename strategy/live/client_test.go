package live

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"

	"collector/feed"
)

func TestNewClient_UnarmedFailsAndSendsNothing(t *testing.T) {
	var requests int
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: countingHandler(&requests, jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`)),
	})

	c, err := NewClient(srv.URL, testAccNo, &fakeTokenSource{token: "t"}, srv.Client(), Arming{})
	if !errors.Is(err, ErrNotArmed) {
		t.Fatalf("NewClient (unarmed) error = %v, want ErrNotArmed", err)
	}
	if c != nil {
		t.Errorf("NewClient (unarmed) returned non-nil Client %+v, want nil", c)
	}

	// Even attempting to use a hypothetical unarmed client must be
	// impossible: there is no client to call Place on, but the assertion
	// that matters is that construction itself never touched the server.
	if requests != 0 {
		t.Errorf("requests to test server = %d, want 0 (an unarmed NewClient must send nothing)", requests)
	}
}

func TestNewClient_RequiresAccNo(t *testing.T) {
	c, err := NewClient("http://example.invalid", "", &fakeTokenSource{token: "t"}, nil, armedForTest())
	if err == nil {
		t.Fatal("NewClient with empty accNo: got nil error, want an error")
	}
	if c != nil {
		t.Errorf("NewClient with empty accNo: got non-nil Client, want nil")
	}
}

func TestNewClient_RequiresTokens(t *testing.T) {
	c, err := NewClient("http://example.invalid", testAccNo, nil, nil, armedForTest())
	if err == nil {
		t.Fatal("NewClient with nil tokens: got nil error, want an error")
	}
	if c != nil {
		t.Errorf("NewClient with nil tokens: got non-nil Client, want nil")
	}
}

func TestDo_SendsBearerAuthorizationHeader(t *testing.T) {
	var gotAuth string
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: func(w http.ResponseWriter, r *http.Request) {
			gotAuth = r.Header.Get("Authorization")
			jsonHandler(http.StatusOK, `{"data":[]}`)(w, r)
		},
	})
	tokens := &fakeTokenSource{token: "secret-trading-token"}
	c, err := NewClient(srv.URL, testAccNo, tokens, srv.Client(), armedForTest())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	if _, err := c.List(context.Background()); err != nil {
		t.Fatalf("List: %v", err)
	}
	if !tokens.asked.Load() {
		t.Fatal("client never asked TokenSource for a token")
	}
	if want := "Bearer secret-trading-token"; gotAuth != want {
		t.Errorf("Authorization header = %q, want %q", gotAuth, want)
	}
}

func TestDo_401And403ReturnErrUnauthorizedWithoutLeakingToken(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		srv := routeServer(t, map[string]http.HandlerFunc{
			pathList: jsonHandler(status, `{"message":"unauthorized"}`),
		})
		c := newTestClient(t, srv)

		_, err := c.List(context.Background())
		if !errors.Is(err, ErrUnauthorized) {
			t.Errorf("status %d: List error = %v, want errors.Is(err, ErrUnauthorized)", status, err)
		}
		if strings.Contains(err.Error(), "test-trading-token") {
			t.Errorf("status %d: error %q leaked the token", status, err.Error())
		}
	}
}

func TestClassifyOrderError(t *testing.T) {
	tests := []struct {
		name   string
		status int
		body   string
		want   error
	}{
		{"duplicate", http.StatusConflict, `{"message":"ErrorDuplicateOrder: ui_ref already exists"}`, ErrDuplicateOrder},
		{"duplicate lowercase", http.StatusConflict, `{"message":"duplicate ui_ref"}`, ErrDuplicateOrder},
		{"suspended", http.StatusBadRequest, `{"message":"ErrorUserSuspendedBackOffice"}`, ErrUserSuspended},
		{"invalid parameter", http.StatusBadRequest, `{"message":"ErrorInvalidParameter: ui_ref too long"}`, ErrInvalidParameter},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := classifyOrderError("/order/v2/buy", tt.status, []byte(tt.body))
			if !errors.Is(err, tt.want) {
				t.Errorf("classifyOrderError(%d, %q) = %v, want errors.Is(_, %v)", tt.status, tt.body, err, tt.want)
			}
		})
	}
}

func TestClassifyOrderError_UnrecognisedBodyIsAPIError(t *testing.T) {
	err := classifyOrderError("/order/v2/buy", http.StatusInternalServerError, []byte(`{"message":"boom"}`))
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("classifyOrderError = %v (%T), want *APIError", err, err)
	}
	if apiErr.Status != http.StatusInternalServerError {
		t.Errorf("APIError.Status = %d, want 500", apiErr.Status)
	}
}

func TestDo_SendsDefaultUserAgent(t *testing.T) {
	var gotUA string
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: func(w http.ResponseWriter, r *http.Request) {
			gotUA = r.Header.Get("User-Agent")
			w.WriteHeader(http.StatusUnauthorized)
		},
		pathBuy: func(w http.ResponseWriter, r *http.Request) {
			gotUA = r.Header.Get("User-Agent")
			w.WriteHeader(http.StatusUnauthorized)
		},
	})
	c := newTestClient(t, srv)

	for name, call := range map[string]func() error{
		"GET": func() error { _, err := c.List(context.Background()); return err },
		"POST": func() error {
			_, err := c.postJSON(context.Background(), pathBuy, map[string]string{"k": "v"})
			return err
		},
	} {
		gotUA = ""
		_ = call()
		if gotUA != feed.DefaultUserAgent {
			t.Errorf("%s: User-Agent = %q, want %q", name, gotUA, feed.DefaultUserAgent)
		}
	}
}
