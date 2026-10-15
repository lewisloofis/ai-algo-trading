// Package livesession supervises one live (or rehearsal) strategy-run session
// end to end: preflight, pre-trade position snapshot, a restart-safe child
// run with a fresh store per attempt, post-trade reconcile, a one-screen
// summary and alerts. It places no orders itself: the only order path is the
// strategy-run child process, which keeps its own ALGO_LIVE/ALGO_CONFIRM
// double-arm, token-account guard and position gate.
package livesession

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// Kind names what an alert is about.
type Kind string

const (
	KindRefused       Kind = "refused"
	KindCrash         Kind = "crash"
	KindRepeatedCrash Kind = "repeated_crash"
	KindKillSwitch    Kind = "kill_switch"
	KindDivergence    Kind = "recon_divergence"
	KindReject        Kind = "order_reject"
	KindBinaryHash    Kind = "binary_hash"
	KindZeroRecord    Kind = "zero_record"
)

// Severity is warn or critical.
type Severity string

const (
	SeverityWarn     Severity = "warn"
	SeverityCritical Severity = "critical"
)

// Alert is one notification. It carries no credentials and no account number:
// Message is built from counts, symbols and store base names only, and is
// passed through Redact before it is sent anywhere.
type Alert struct {
	Kind     Kind      `json:"kind"`
	Severity Severity  `json:"severity"`
	Message  string    `json:"message"`
	Strategy string    `json:"strategy"`
	Store    string    `json:"store,omitempty"` // base name
	Attempt  int       `json:"attempt,omitempty"`
	At       time.Time `json:"time"`
}

// Notifier delivers an alert. A failing notifier never changes a session's
// outcome: callers log the error and carry on.
type Notifier interface {
	Notify(ctx context.Context, a Alert) error
}

// LogNotifier writes one line per alert.
type LogNotifier struct{ W io.Writer }

func (n LogNotifier) Notify(_ context.Context, a Alert) error {
	_, err := fmt.Fprintf(n.W, "ALERT [%s] %s: %s\n", a.Severity, a.Kind, a.Message)
	return err
}

// Multi fans an alert out to every notifier, joining their errors.
type Multi []Notifier

func (m Multi) Notify(ctx context.Context, a Alert) error {
	var errs []error
	for _, n := range m {
		if err := n.Notify(ctx, a); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

// WebhookEnv is the environment variable holding the webhook URL. The URL
// itself is a secret (Slack-style incoming webhooks embed a token), so it is
// never a flag, never logged and never put in an error message.
const WebhookEnv = "ALGO_ALERT_WEBHOOK_URL"

// WebhookNotifier POSTs a JSON body to an operator-supplied internal URL.
type WebhookNotifier struct {
	url    string
	client *http.Client
}

// NewWebhookNotifier validates rawURL (http or https with a host). The error
// never echoes the URL.
func NewWebhookNotifier(rawURL string, client *http.Client) (*WebhookNotifier, error) {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("%s is not a valid http(s) URL", WebhookEnv)
	}
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	return &WebhookNotifier{url: u.String(), client: client}, nil
}

type webhookBody struct {
	Text string `json:"text"` // Slack-compatible
	Alert
}

func (w *WebhookNotifier) Notify(ctx context.Context, a Alert) error {
	body, err := json.Marshal(webhookBody{
		Text:  fmt.Sprintf("[live-session] %s %s: %s", a.Severity, a.Kind, a.Message),
		Alert: a,
	})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, w.url, bytes.NewReader(body))
	if err != nil {
		return errors.New("webhook: build request failed")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := w.client.Do(req)
	if err != nil {
		// Report only the category: a transport error carries the URL or its host.
		if errors.Is(err, context.DeadlineExceeded) {
			return errors.New("webhook: post timed out")
		}
		return errors.New("webhook: post failed (connection error)")
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("webhook: status %d", resp.StatusCode)
	}
	return nil
}

var secretShapes = regexp.MustCompile(`eyJ[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]*){1,2}|(?i:bearer)\s+\S+`)

// Redact removes anything token-shaped from s, and every non-empty literal in
// secrets (the account number, for one).
func Redact(s string, secrets ...string) string {
	s = secretShapes.ReplaceAllString(s, "[redacted]")
	for _, sec := range secrets {
		if sec = strings.TrimSpace(sec); sec != "" {
			s = strings.ReplaceAll(s, sec, "[account]")
		}
	}
	return s
}
