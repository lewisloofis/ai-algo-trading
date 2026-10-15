package livesession

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func sampleAlert() Alert {
	return Alert{Kind: KindDivergence, Severity: SeverityCritical, Message: "GOTO expected 1 lot", Strategy: "calibration", Store: "x-a1.db", Attempt: 1, At: time.Unix(0, 0).UTC()}
}

func TestLogNotifier(t *testing.T) {
	var b bytes.Buffer
	if err := (LogNotifier{W: &b}).Notify(context.Background(), sampleAlert()); err != nil {
		t.Fatal(err)
	}
	if got := b.String(); !strings.Contains(got, "ALERT [critical] recon_divergence: GOTO expected 1 lot") {
		t.Errorf("got %q", got)
	}
}

func TestWebhookNotifier_PostsJSON(t *testing.T) {
	var gotBody []byte
	var gotType string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotBody, _ = io.ReadAll(r.Body)
		gotType = r.Header.Get("Content-Type")
	}))
	defer srv.Close()
	n, err := NewWebhookNotifier(srv.URL, srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	if err := n.Notify(context.Background(), sampleAlert()); err != nil {
		t.Fatal(err)
	}
	if gotType != "application/json" {
		t.Errorf("content-type %q", gotType)
	}
	var m map[string]any
	if err := json.Unmarshal(gotBody, &m); err != nil {
		t.Fatal(err)
	}
	if m["kind"] != "recon_divergence" || m["severity"] != "critical" || !strings.Contains(m["text"].(string), "recon_divergence") {
		t.Errorf("body = %s", gotBody)
	}
}

func TestWebhookNotifier_ErrorsNeverEchoTheURL(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
	url := srv.URL + "/services/SECRET-PATH"
	n, err := NewWebhookNotifier(url, srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	if err := n.Notify(context.Background(), sampleAlert()); err == nil || strings.Contains(err.Error(), "SECRET") {
		t.Fatalf("status error = %v", err)
	}
	srv.Close() // now the connection fails
	err = n.Notify(context.Background(), sampleAlert())
	if err == nil || strings.Contains(err.Error(), "SECRET") || strings.Contains(err.Error(), "127.0.0.1") {
		t.Fatalf("transport error leaks the URL: %v", err)
	}
}

func TestNewWebhookNotifier_RejectsBadURLWithoutEchoing(t *testing.T) {
	for _, u := range []string{"", "ftp://x/SECRET", "not a url", "https://"} {
		_, err := NewWebhookNotifier(u, nil)
		if err == nil {
			t.Errorf("%q accepted", u)
		} else if strings.Contains(err.Error(), "SECRET") {
			t.Errorf("error echoes the URL: %v", err)
		}
	}
}

func TestMulti_DeliversToAllAndJoinsErrors(t *testing.T) {
	a, b := &recNotifier{}, &recNotifier{}
	m := Multi{a, failing{}, b}
	if err := m.Notify(context.Background(), sampleAlert()); err == nil {
		t.Fatal("want the failing notifier's error")
	}
	if len(a.alerts) != 1 || len(b.alerts) != 1 {
		t.Fatal("a failing notifier must not stop the others")
	}
}

type failing struct{}

func (failing) Notify(context.Context, Alert) error { return io.ErrClosedPipe }

func TestRedact(t *testing.T) {
	tok := tokenFor("ACC-1")
	got := Redact("token "+tok+" and Bearer abc.def for ACC-1 on GOTO", "ACC-1")
	for _, bad := range []string{tok, "abc.def", "ACC-1"} {
		if strings.Contains(got, bad) {
			t.Errorf("%q survived: %s", bad, got)
		}
	}
	if !strings.Contains(got, "GOTO") {
		t.Errorf("over-redacted: %s", got)
	}
}
