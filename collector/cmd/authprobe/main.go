// Package main is the Phase 0 gating probe for docs/plans/03-token-rotation.md.
//
// It validates two unknowns before wiring headless re-login into the collector:
//
//  1. Does a v6 password login with a chosen player_id and NO signature succeed
//     (return a token pair) or return a new_device / unfreeze challenge?
//
//  2. Does trust survive an IP change?
//
// When a multi_factor new-device challenge is detected and -verify=true (the
// default), the probe completes the OTP flow interactively:
//
//	Step 1 — POST /mfa/verification/v1/challenge/start  to learn the next
//	          challenge type and available OTP channels.
//	Step 2 — POST /mfa/verification/v1/challenge/otp/send  to dispatch the
//	          OTP to the chosen channel.
//	Step 3 — Prompt STDIN for the code; POST …/otp/verify (up to 5 attempts).
//	Step 4 — POST /login/v6/new-device/verify  to exchange the login_token for
//	          a real token pair, establishing device trust.
//
// When a trusted_device new-device challenge is detected and -verify=true, the
// probe drives the headless approval flow:
//
//	Step A — POST /login/v4/new-device/prompt/send  to push an approval
//	          notification to the user's already-trusted device.
//	Step B — Poll GET /mfa/v1/prompt/verified/result  until the user taps
//	          Approve (or Reject/Expire), up to 5 minutes.
//	Step C — POST /login/v4/new-device/prompt/verify  to exchange the
//	          login_token + acknowledge_token for a real token pair,
//	          establishing device trust headlessly.
//
// # How to run
//
//	export SB_USERNAME=your@email.com
//	export SB_PASSWORD=yourpassword          # never echoed
//	export SB_PLAYER_ID=<fixed-random-uuid>  # generate once: uuidgen
//	go run ./cmd/authprobe/
//
// Flags override env vars:
//
//	go run ./cmd/authprobe/ -user foo@bar.com -player-id <uuid>
//
// New flags:
//
//	-verify      bool   (default true)  complete OTP flow when multi_factor
//	                                    challenge is detected; set false to
//	                                    just classify and exit (rounds 2/3).
//	-channel     string (default "")    OTP channel override: CHANNEL_EMAIL,
//	                                    CHANNEL_SMS, CHANNEL_WHATSAPP.
//	                                    Also SB_OTP_CHANNEL env var.
//	-out         string (default "")    If set, persist the obtained token pair
//	                                    to this JSON file (mode 0600) on any
//	                                    successful exit.  Also SB_TOKEN_OUT env.
//	                                    Example: -out ~/.sb-tokens.json
//
// # IP-change A/B procedure
//
// Round 1 — first-ever use of this player_id from any network:
//
//	Expected result: NEW_DEVICE_CHALLENGE → OTP flow runs automatically.
//	On success: prints TRUST_ESTABLISHED and exits 0.
//
// Round 2 — same player_id, same network (-verify=false):
//
//	Expected result: TOKEN_PAIR (exit 0)
//	Confirms the player_id is now trusted.
//
// Round 3 — same player_id, different IP (-verify=false):
//
//	Result: TOKEN_PAIR → trust is IP-agnostic; headless rotation is safe.
//	Result: NEW_DEVICE_CHALLENGE → trust is IP-scoped; rotation needs care.
//
// Exit codes:
//
//	0 — TOKEN_PAIR or TRUST_ESTABLISHED (tokens obtained, no challenge)
//	1 — transport / configuration / OTP verification error
//	2 — challenge received that cannot be completed headlessly
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"collector/internal/feed"
)

const defaultHost = "https://exodus.stockbit.com"

var client = &http.Client{Timeout: 20 * time.Second}

// tokenOutPath is set once from the -out flag (or SB_TOKEN_OUT env) in main.
// It is process-global CLI config, analogous to the shared client above.
var tokenOutPath string

func main() {
	user := flag.String("user", "", "Stockbit username / email (or SB_USERNAME env)")
	password := flag.String("password", "", "Stockbit password (prefer SB_PASSWORD env; never echoed)")
	playerID := flag.String("player-id", "", "Fixed device UUID (or SB_PLAYER_ID env)")
	host := flag.String("host", "", "Exodus host (or SB_EXODUS_HOST env; default "+defaultHost+")")
	verify := flag.Bool("verify", true, "Complete OTP flow when multi_factor challenge is detected")
	channel := flag.String("channel", "", "OTP channel override: CHANNEL_EMAIL, CHANNEL_SMS, CHANNEL_WHATSAPP (or SB_OTP_CHANNEL env)")
	out := flag.String("out", "", "If set, persist the obtained token pair to this JSON file (mode 0600). Or SB_TOKEN_OUT env.")
	flag.Parse()

	resolveEnv(user, "SB_USERNAME")
	resolveEnv(password, "SB_PASSWORD")
	resolveEnv(playerID, "SB_PLAYER_ID")
	resolveEnv(host, "SB_EXODUS_HOST")
	if *host == "" {
		*host = defaultHost
	}
	resolveEnv(channel, "SB_OTP_CHANNEL")
	resolveEnv(out, "SB_TOKEN_OUT")
	tokenOutPath = *out

	if *user == "" || *password == "" || *playerID == "" {
		fmt.Fprintln(os.Stderr, "error: -user / SB_USERNAME, -password / SB_PASSWORD, and -player-id / SB_PLAYER_ID are all required")
		os.Exit(1)
	}

	fmt.Printf("authprobe: posting to %s/login/v6/username\n", *host)
	fmt.Printf("authprobe: user=%s  player_id=%s  signature=<omitted>\n", *user, *playerID)

	loginBody := map[string]string{
		"user":      *user,
		"password":  *password,
		"player_id": *playerID,
	}
	m, status, err := postJSON(*host, "/login/v6/username", loginBody, androidHeaders)
	if err != nil {
		fatalf("login request: %v", err)
	}

	fmt.Printf("authprobe: HTTP status %d\n", status)
	classify(m, status, *host, *verify, *channel, *playerID)
}

// resolveEnv sets *s from the named environment variable when *s is empty.
func resolveEnv(s *string, envKey string) {
	if *s == "" {
		*s = os.Getenv(envKey)
	}
}

// androidHeaders applies the headers the Stockbit Android client attaches to
// every Exodus call via its AuthExodusInterceptor — login AND the MFA
// verification / new-device/verify calls share the same interceptor. We mimic
// the mobile client (not the web app) consistently across the whole flow:
// X-Platform: android also skips the reCAPTCHA the web login path requires.
func androidHeaders(req *http.Request) {
	req.Header.Set("X-Platform", "android")
	req.Header.Set("X-AppVersion", "7.0.0")
	req.Header.Set("X-DeviceType", "algo-prototype")
	req.Header.Set("Accept-Language", "id")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
}

// postJSON POSTs body as JSON to host+path with the given header setter,
// returns the parsed response map, HTTP status code, and any transport error.
// The response body is consumed and closed inside this function.
func postJSON(host, path string, body any, setHdr func(*http.Request)) (map[string]interface{}, int, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, 0, fmt.Errorf("marshal: %w", err)
	}
	req, err := http.NewRequest("POST", host+path, bytes.NewReader(raw))
	if err != nil {
		return nil, 0, fmt.Errorf("build request: %w", err)
	}
	setHdr(req)

	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("HTTP: %w", err)
	}
	defer resp.Body.Close()

	respRaw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var m map[string]interface{}
	if err := json.Unmarshal(respRaw, &m); err != nil {
		return nil, resp.StatusCode, fmt.Errorf("parse response: %w (body: %s)", err, bodySnippet(respRaw))
	}
	return m, resp.StatusCode, nil
}

// getJSON issues a GET to host+path with the given header setter, returns the
// parsed response map, HTTP status code, and any transport error.
// The response body is consumed and closed inside this function.
func getJSON(host, path string, setHdr func(*http.Request)) (map[string]interface{}, int, error) {
	req, err := http.NewRequest("GET", host+path, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("build request: %w", err)
	}
	setHdr(req)

	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("HTTP: %w", err)
	}
	defer resp.Body.Close()

	respRaw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var m map[string]interface{}
	if err := json.Unmarshal(respRaw, &m); err != nil {
		return nil, resp.StatusCode, fmt.Errorf("parse response: %w (body: %s)", err, bodySnippet(respRaw))
	}
	return m, resp.StatusCode, nil
}

// classify prints a human-readable verdict and exits with the appropriate code.
// When verify is true and a multi_factor challenge is found, it calls
// runOTPVerification instead of exiting 2.
func classify(m map[string]interface{}, status int, host string, verify bool, channel, playerID string) {
	// Priority 1: check for a token pair (successful login).
	if access, refresh, ok := findTokenPair(m); ok {
		printTokenPair(access, refresh)
		saveTokens(access, refresh)
		os.Exit(0)
	}

	// Priority 2: new_device challenge.
	if nd := findMap(m, "new_device"); nd != nil {
		fmt.Println("result: NEW_DEVICE_CHALLENGE")
		classifyNewDevice(nd, host, verify, channel, playerID)
		os.Exit(2)
	}

	// Priority 3: unfreeze challenge.
	if uf := findMap(m, "unfreeze"); uf != nil {
		fmt.Println("result: UNFREEZE_CHALLENGE")
		if lt := findString(uf, "login_token"); lt != "" {
			fmt.Printf("  login_token: %s\n", lt)
		}
		os.Exit(2)
	}

	// Fallthrough: unrecognised shape.
	b, _ := json.Marshal(m)
	fmt.Println("result: OTHER")
	fmt.Printf("  body snippet: %s\n", bodySnippet(b))
	os.Exit(1)
}

// classifyNewDevice handles the sub-types of a new_device challenge. When a
// multi_factor sub-type is present and verify is true, it initiates the OTP
// flow (which exits 0 on success); otherwise it returns so classify can exit 2.
func classifyNewDevice(nd map[string]interface{}, host string, verify bool, channel, playerID string) {
	if mf := findMap(nd, "multi_factor"); mf != nil {
		fmt.Println("  verification_type: multi_factor")
		lt := findString(mf, "login_token")
		vt := findString(mf, "verification_token")
		if lt != "" {
			fmt.Printf("  login_token: %s\n", lt)
		}
		if vt != "" {
			fmt.Println("  verification_token: <redacted>")
		}
		if verify {
			if lt == "" || vt == "" {
				fatalf("multi_factor challenge missing login_token or verification_token")
			}
			runOTPVerification(host, lt, vt, channel, playerID)
		}
		return
	}
	if td := findMap(nd, "trusted_device"); td != nil {
		fmt.Println("  verification_type: trusted_device")
		lt := findString(td, "login_token")
		dn := findString(td, "device_name")
		if lt != "" {
			fmt.Printf("  login_token: %s\n", lt)
		}
		if dn != "" {
			fmt.Printf("  device_name: %s\n", dn)
		}
		if verify {
			if lt == "" {
				fatalf("trusted_device challenge missing login_token")
			}
			runTrustedDeviceApproval(host, lt, dn, playerID)
		}
		return
	}
	b, _ := json.Marshal(nd)
	fmt.Printf("  new_device data: %s\n", bodySnippet(b))
}

// runOTPVerification drives steps 1–2 of the MFA sequence (start + send),
// then delegates the interactive verify loop to runOTPVerifyLoop.
func runOTPVerification(host, loginToken, verificationToken, channelOverride, playerID string) {
	// Step 1: start challenge.
	startResp, startStatus, err := postJSON(host, "/mfa/verification/v1/challenge/start", map[string]string{
		"verification_token": verificationToken,
	}, androidHeaders)
	if err != nil {
		fatalf("challenge/start: %v", err)
	}
	startBody, _ := json.Marshal(startResp)
	fmt.Printf("authprobe: challenge/start HTTP %d → %s\n", startStatus, bodySnippet(startBody))
	if startStatus < 200 || startStatus >= 300 {
		fatalf("challenge/start failed (HTTP %d)", startStatus)
	}

	nextChallenge := findString(startResp, "next_challenge")
	if nextChallenge != "CHALLENGE_OTP" {
		fmt.Printf("result: UNSUPPORTED_CHALLENGE — next_challenge=%q\n", nextChallenge)
		fmt.Println("  headless completion is only possible for CHALLENGE_OTP.")
		fmt.Println("  This challenge type (e.g. CHALLENGE_FACE_MATCHING) requires a native SDK.")
		os.Exit(1)
	}

	channels, defaultChannel := parseOTPChannels(startResp)
	if len(channels) == 0 {
		fatalf("challenge/start: no OTP channels in response")
	}
	fmt.Printf("authprobe: available OTP channels=%v default=%s\n", channels, defaultChannel)

	// Step 2: choose and send OTP.
	chosenChannel := resolveChannel(channelOverride, defaultChannel, channels)
	sendResp, sendStatus, err := postJSON(host, "/mfa/verification/v1/challenge/otp/send", map[string]string{
		"verification_token": verificationToken,
		"channel":            chosenChannel,
	}, androidHeaders)
	if err != nil {
		fatalf("challenge/otp/send: %v", err)
	}
	sendBody, _ := json.Marshal(sendResp)
	fmt.Printf("authprobe: challenge/otp/send HTTP %d → %s\n", sendStatus, bodySnippet(sendBody))
	if sendStatus < 200 || sendStatus >= 300 {
		fatalf("challenge/otp/send failed (HTTP %d) — OTP was NOT dispatched", sendStatus)
	}

	target := findString(sendResp, "target")
	nextAttemptIn := findNumber(sendResp, "next_attempt_in")
	fmt.Printf("authprobe: OTP dispatched via %s → %s (retry in %.0fs)\n", chosenChannel, target, nextAttemptIn)

	// Steps 3–4: interactive verify loop then device trust finalization.
	runOTPVerifyLoop(host, verificationToken, loginToken, target, chosenChannel, playerID)
}

// runOTPVerifyLoop prompts stdin for an OTP code and calls otp/verify up to
// maxAttempts times. On CHALLENGE_FINISH it calls finalizeDeviceTrust.
func runOTPVerifyLoop(host, verificationToken, loginToken, target, chosenChannel, playerID string) {
	const maxAttempts = 5
	reader := bufio.NewReader(os.Stdin)

	for attempt := 1; attempt <= maxAttempts; attempt++ {
		fmt.Printf("Enter the OTP sent to %s (channel %s): ", target, chosenChannel)
		line, err := reader.ReadString('\n')
		if err != nil {
			fatalf("reading OTP from stdin: %v", err)
		}
		otp := strings.TrimSpace(line)

		verifyResp, verifyStatus, err := postJSON(host, "/mfa/verification/v1/challenge/otp/verify", map[string]string{
			"verification_token": verificationToken,
			"otp":                otp,
		}, androidHeaders)
		if err != nil {
			fmt.Printf("  verify error: %v (attempt %d/%d)\n", err, attempt, maxAttempts)
			continue
		}
		if verifyStatus < 200 || verifyStatus >= 300 {
			msg := findString(verifyResp, "message", "error_message", "error")
			fmt.Printf("  OTP rejected (HTTP %d): %s (attempt %d/%d)\n", verifyStatus, msg, attempt, maxAttempts)
			continue
		}

		next := findString(verifyResp, "next_challenge")
		switch next {
		case "CHALLENGE_OTP":
			fmt.Printf("  incorrect OTP — try again (attempt %d/%d)\n", attempt, maxAttempts)
		case "CHALLENGE_FINISH":
			finalizeDeviceTrust(host, loginToken, playerID) // exits 0 on success
		default:
			fmt.Printf("result: UNSUPPORTED_CHALLENGE — next_challenge=%q after OTP verify\n", next)
			os.Exit(1)
		}
	}

	fatalf("OTP verification failed after %d attempts", maxAttempts)
}

// finalizeDeviceTrust calls /login/v6/new-device/verify, prints the result and
// exits the process.
func finalizeDeviceTrust(host, loginToken, playerID string) {
	resp, _, err := postJSON(host, "/login/v6/new-device/verify", map[string]interface{}{
		"multi_factor": map[string]string{
			"login_token": loginToken,
		},
	}, androidHeaders)
	if err != nil {
		fatalf("new-device/verify: %v", err)
	}

	access, refresh, ok := findTokenPair(resp)
	if !ok {
		b, _ := json.Marshal(resp)
		fmt.Println("result: TRUST_FAILED — no token pair in response")
		fmt.Printf("  body snippet: %s\n", bodySnippet(b))
		os.Exit(1)
	}

	fmt.Println("result: TRUST_ESTABLISHED")
	fmt.Printf("  access token:  <len=%d chars>\n", len(access))
	if refresh != "" {
		fmt.Printf("  refresh token: <len=%d chars, present>\n", len(refresh))
	} else {
		fmt.Println("  refresh token: <absent>")
	}
	fmt.Printf("player_id %s is now trusted — re-run the probe (without -verify) to confirm a direct TOKEN_PAIR.\n", playerID)
	saveTokens(access, refresh)
	os.Exit(0)
}

// runTrustedDeviceApproval implements the headless trusted-device approval flow:
//
//	Step A — POST /login/v4/new-device/prompt/send  (trigger push to trusted device)
//	Step B — Poll GET /mfa/v1/prompt/verified/result  (wait for user approval)
//	Step C — POST /login/v4/new-device/prompt/verify  (exchange tokens, establish trust)
func runTrustedDeviceApproval(host, loginToken, deviceName, playerID string) {
	// Step A: trigger the approval push.
	sendResp, sendStatus, err := postJSON(host, "/login/v4/new-device/prompt/send", map[string]string{
		"token": loginToken,
	}, androidHeaders)
	if err != nil {
		fatalf("prompt/send: %v", err)
	}
	sendBody, _ := json.Marshal(sendResp)
	fmt.Printf("authprobe: prompt/send HTTP %d → %s\n", sendStatus, bodySnippet(sendBody))
	if sendStatus < 200 || sendStatus >= 300 {
		fatalf("prompt/send failed (HTTP %d)", sendStatus)
	}

	promptToken := findString(sendResp, "prompt_token")
	if promptToken == "" {
		fatalf("prompt/send: no prompt_token in response")
	}
	target := findString(sendResp, "target")
	// next_attempt_time is informational; just surface it if present.
	nextAttemptTime := findString(sendResp, "next_attempt_time")
	if nextAttemptTime != "" {
		fmt.Printf("authprobe: next_attempt_time=%s\n", nextAttemptTime)
	}
	fmt.Printf("authprobe: approval push sent to %s (%s) — open Stockbit on that device and tap Approve (waiting up to 5m)...\n", deviceName, target)

	// Step B: poll for approval.
	const (
		pollInterval   = 2 * time.Second
		heartbeatEvery = 5 // print heartbeat every Nth poll
		maxConsecErrs  = 5
		timeout        = 5 * time.Minute
	)
	deadline := time.Now().Add(timeout)
	consecErrs := 0
	pollCount := 0

	pollPath := "/mfa/v1/prompt/verified/result?token=" + url.QueryEscape(promptToken)

	var ackToken string
	for time.Now().Before(deadline) {
		time.Sleep(pollInterval)
		pollCount++

		pollResp, pollStatus, err := getJSON(host, pollPath, androidHeaders)
		if err != nil || pollStatus < 200 || pollStatus >= 300 {
			consecErrs++
			if err != nil {
				fmt.Printf("authprobe: poll error (consecutive %d/%d): %v\n", consecErrs, maxConsecErrs, err)
			} else {
				fmt.Printf("authprobe: poll HTTP %d (consecutive %d/%d)\n", pollStatus, consecErrs, maxConsecErrs)
			}
			if consecErrs >= maxConsecErrs {
				fatalf("too many consecutive poll errors (%d) — giving up", maxConsecErrs)
			}
			continue
		}
		consecErrs = 0 // reset on good response

		status := findString(pollResp, "status")
		switch status {
		case "PROMPT_STATUS_APPROVED":
			ackToken = findString(pollResp, "acknowledge_token")
			if ackToken == "" {
				fatalf("approved but no acknowledge_token in response")
			}
			fmt.Println("authprobe: approval received — completing trust establishment")
			goto approved
		case "PROMPT_STATUS_REJECTED":
			fatalf("approval was rejected on the trusted device")
		case "PROMPT_STATUS_EXPIRED":
			fatalf("approval request expired — re-run the probe")
		default:
			// PROMPT_STATUS_UNSPECIFIED or empty — still waiting
			if pollCount%heartbeatEvery == 0 {
				fmt.Println("authprobe: still waiting for approval...")
			}
		}
	}
	fatalf("timed out after 5m waiting for approval")

approved:
	// Step C: complete login with acknowledge_token.
	verifyResp, _, err := postJSON(host, "/login/v4/new-device/prompt/verify", map[string]string{
		"token":             loginToken,
		"acknowledge_token": ackToken,
	}, androidHeaders)
	if err != nil {
		fatalf("prompt/verify: %v", err)
	}

	access, refresh, ok := findTokenPair(verifyResp)
	if !ok {
		b, _ := json.Marshal(verifyResp)
		fmt.Println("result: TRUST_FAILED — no token pair in response")
		fmt.Printf("  body snippet: %s\n", bodySnippet(b))
		os.Exit(1)
	}

	fmt.Println("result: TRUST_ESTABLISHED")
	fmt.Printf("  access token:  <len=%d chars>\n", len(access))
	if refresh != "" {
		fmt.Printf("  refresh token: <len=%d chars, present>\n", len(refresh))
	} else {
		fmt.Println("  refresh token: <absent>")
	}
	fmt.Printf("player_id %s is now trusted — re-run the probe (without -verify) to confirm a direct TOKEN_PAIR.\n", playerID)
	saveTokens(access, refresh)
	os.Exit(0)
}

// ---------- channel helpers ----------

// channelInfo holds one entry from supporting_data.otp.channels.
type channelInfo struct {
	Channel string
	Target  string
}

// parseOTPChannels extracts the channels array and default_channel from a
// challenge/start response.
func parseOTPChannels(m map[string]interface{}) (channels []channelInfo, defaultChannel string) {
	sd := findMap(m, "supporting_data")
	if sd == nil {
		return nil, ""
	}
	otp := findMap(sd, "otp")
	if otp == nil {
		return nil, ""
	}
	defaultChannel, _ = otp["default_channel"].(string)

	raw, ok := otp["channels"]
	if !ok {
		return nil, defaultChannel
	}
	arr, ok := raw.([]interface{})
	if !ok {
		return nil, defaultChannel
	}
	for _, item := range arr {
		entry, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		ch, _ := entry["channel"].(string)
		tgt, _ := entry["target"].(string)
		if ch != "" {
			channels = append(channels, channelInfo{Channel: ch, Target: tgt})
		}
	}
	return channels, defaultChannel
}

// resolveChannel picks the OTP channel to use: override > default > first.
// It validates the chosen value is in the available list and fatalf on mismatch.
func resolveChannel(override, defaultChannel string, available []channelInfo) string {
	chosen := override
	if chosen == "" {
		chosen = defaultChannel
	}
	if chosen == "" && len(available) > 0 {
		chosen = available[0].Channel
	}
	for _, c := range available {
		if c.Channel == chosen {
			return chosen
		}
	}
	var names []string
	for _, c := range available {
		names = append(names, c.Channel)
	}
	fatalf("channel %q is not in the available list: %v", chosen, names)
	return "" // unreachable
}

// ---------- JSON tree walkers ----------
// These are local reimplementations of the helpers in internal/feed/session.go.
// The probe is intentionally standalone; nothing is imported from the feed package.

// findTokenPair walks node recursively looking for {access:{token:...}, refresh:{token:...}}.
func findTokenPair(node interface{}) (access, refresh string, ok bool) {
	m, isMap := node.(map[string]interface{})
	if !isMap {
		return "", "", false
	}
	if a, ok1 := m["access"].(map[string]interface{}); ok1 {
		if r, ok2 := m["refresh"].(map[string]interface{}); ok2 {
			at, _ := a["token"].(string)
			rt, _ := r["token"].(string)
			if at != "" {
				return at, rt, true
			}
		}
	}
	for _, v := range m {
		if a, r, found := findTokenPair(v); found {
			return a, r, found
		}
	}
	return "", "", false
}

// findMap walks node recursively for the first map value at the given key.
func findMap(node interface{}, key string) map[string]interface{} {
	m, ok := node.(map[string]interface{})
	if !ok {
		return nil
	}
	if v, exists := m[key]; exists {
		if sub, ok := v.(map[string]interface{}); ok {
			return sub
		}
	}
	for _, v := range m {
		if found := findMap(v, key); found != nil {
			return found
		}
	}
	return nil
}

// findString returns the first non-empty string value for any of the given keys
// found by recursively walking node.
func findString(node interface{}, keys ...string) string {
	m, ok := node.(map[string]interface{})
	if !ok {
		return ""
	}
	for _, k := range keys {
		if s, ok := m[k].(string); ok && s != "" {
			return s
		}
	}
	for _, v := range m {
		if s := findString(v, keys...); s != "" {
			return s
		}
	}
	return ""
}

// findNumber returns the float64 value for key at any depth in node, or 0.
func findNumber(node interface{}, key string) float64 {
	m, ok := node.(map[string]interface{})
	if !ok {
		return 0
	}
	if v, exists := m[key]; exists {
		if f, ok := v.(float64); ok {
			return f
		}
	}
	for _, v := range m {
		if f := findNumber(v, key); f != 0 {
			return f
		}
	}
	return 0
}

// ---------- helpers ----------

func printTokenPair(access, refresh string) {
	fmt.Println("result: TOKEN_PAIR")
	fmt.Printf("  access token:  <len=%d chars>\n", len(access))
	if refresh != "" {
		fmt.Printf("  refresh token: <len=%d chars, present>\n", len(refresh))
	} else {
		fmt.Println("  refresh token: <absent>")
	}
}

// saveTokens persists the token pair to tokenOutPath when it is non-empty.
// Persistence failure is non-fatal: a warning is printed to stderr but the
// exit code is not changed so a successful login is never reported as failure.
// SECURITY: token values are never logged or printed.
func saveTokens(access, refresh string) {
	if tokenOutPath == "" {
		return
	}
	if err := (&feed.FileTokenStore{Path: tokenOutPath}).Save(access, refresh); err != nil {
		fmt.Fprintf(os.Stderr, "warning: could not persist tokens to %s: %v\n", tokenOutPath, err)
		return
	}
	fmt.Printf("  tokens persisted → %s\n", tokenOutPath)
}

func bodySnippet(b []byte) string {
	s := string(b)
	if len(s) > 300 {
		return s[:300] + "…"
	}
	return s
}

func fatalf(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "error: "+format+"\n", args...)
	os.Exit(1)
}
