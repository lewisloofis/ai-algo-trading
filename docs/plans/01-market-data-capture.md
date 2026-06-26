# Market-Data Capture — Architecture & AWS Deployment

> **Status:** ✅ Complete · **Owner:** @lewis · **Updated:** 2026-06-26
> **Related:** [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md)

> ✅ **Result: Complete.** EC2 + EventBridge scheduler running on AWS account `047915756052`.
> Five full trading weeks soak confirmed (Jun 22–26 — 45 segments, ~1.1 GiB `plan10-survivors`).
> Replay verified against downloaded S3 day: all 9 segments decode cleanly, event content and
> seq-number continuity confirmed. Phase 3 (Parquet analytics tier) deferred — optional.

This document covers the end-to-end design for capturing Stockbit market-data
to a replayable archive: the capture loop, segment rolling and compression,
AWS deployment (compute, storage, scheduling), cost breakdown, failure modes,
and a phased implementation plan.

> ⚠️ **Disclaimer.** The Stockbit market-data WebSocket (`wss://wss-trading.stockbit.com/ws`)
> is a private, undocumented endpoint used by Stockbit's own apps. It can change
> without notice. Automated/programmatic access may be subject to Stockbit's Terms
> of Service. Use your own account, respect rate limits, and confirm you are
> permitted to automate before running this in production.

---

## 1. Goals and non-goals

### Goals

- Record every binary Protobuf frame from the market-data WebSocket to a local
  append-only log in real time, with zero frame loss under normal conditions.
- Capture **full microstructure** per symbol: `runningTradeBatch`, `orderBook`,
  and `liveprice`.
- Compress and upload finished daily segments to S3 in `ap-southeast-3`
  (Jakarta) automatically.
- Enable a replayer to feed recorded data to a strategy through the same
  interface as the live feed — no strategy code changes required for backtesting.
- Keep total AWS cost (compute + storage + transfer) under $10/month for a
  ~30-symbol watchlist.

### Non-goals

- Real-time decoding or alerting (capture is write-only; analysis is offline).
- Full-market capture (all IDX symbols) — the design is for a watchlist subset.
- Tick-by-tick order execution during replay (replay is read-only market data).
- A managed streaming platform (Kafka, Kinesis, etc.) — overkill at this scale.

---

## 2. Locked design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Symbol scope | Configurable watchlist, ~10–50 symbols | Bounds bandwidth, storage, and subscription slots |
| Channels subscribed | `runningTradeBatch` + `orderBook` + `liveprice` | Full microstructure; batch variant is cheaper for multi-symbol |
| AWS region | `ap-southeast-3` (Jakarta) | Shortest network path to IDX/Stockbit servers |
| Capture fidelity | Raw binary frames, decode deferred | Zero CPU on hot path; perfect replay fidelity |
| Compression | zstd | Best ratio on repetitive Protobuf + orderbook pipe-delimited strings |
| Compute form | EC2 `t4g.nano` / `t4g.micro` (Graviton, ARM) | Go + ARM = smallest instance class; scheduled on/off |
| Cold archive | S3 Glacier Deep Archive | Effectively free at this data volume |

---

## 3. End-to-end architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          EC2 t4g.nano (ap-southeast-3)              │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  collector (Go binary)                                       │   │
│  │                                                              │   │
│  │  ┌─────────┐   WS frames   ┌──────────────┐   raw bytes     │   │
│  │  │ wsConn  │──────────────►│ capture loop │──────────────►  │   │
│  │  │(websocket│  (binary,    │              │  append-only    │   │
│  │  │ .go)    │  protobuf)   │  no decode   │  segment file   │   │
│  │  └────┬────┘               └──────┬───────┘                 │   │
│  │       │                          │ control records          │   │
│  │       │ heartbeat (10s)          │ (connect/auth/           │   │
│  │       │ auth/subscribe           │  reconnect/401/          │   │
│  │       │ reconnect (backoff       │  switch_reset)           │   │
│  │       │  0.75s→3s)              │                           │   │
│  │       │ token refresh            ▼                           │   │
│  │       │ (proactive 60s margin)  ┌──────────────┐            │   │
│  │  ┌────┴────┐                   │ segment roller│            │   │
│  │  │  auth   │                   │ (UTC day roll)│            │   │
│  │  │  .go    │                   └──────┬───────┘            │   │
│  │  └─────────┘                          │ on roll             │   │
│  │                                       ▼                     │   │
│  │                               ┌───────────────┐             │   │
│  │                               │ zstd compress │             │   │
│  │                               │ + manifest    │             │   │
│  │                               └──────┬────────┘             │   │
│  └──────────────────────────────────────┼─────────────────────┘   │
│                                         │ aws s3 cp               │
└─────────────────────────────────────────┼─────────────────────────┘
                                          │
                         ┌────────────────▼──────────────────────┐
                         │   S3 bucket (ap-southeast-3)          │
                         │   raw/dt=YYYY-MM-DD/<watchlist-id>/   │
                         │     seg-HHMMSS.binpb.zst              │
                         │     seg-HHMMSS.manifest.json          │
                         │                                       │
                         │   Lifecycle:                          │
                         │     0–7 days   → Standard             │
                         │     8–30 days  → Standard-IA          │
                         │     31+ days   → Glacier Deep Archive │
                         └───────────────────────────────────────┘
                                          │
                              (offline, on demand)
                                          │
                         ┌────────────────▼──────────────────────┐
                         │   replayer / backtester               │
                         │                                       │
                         │   read segments → decodeWrap()        │
                         │   (reuses collector/datafeed.go)      │
                         │   → strategy event stream             │
                         └───────────────────────────────────────┘
```

---

## 4. Authentication & token bootstrap

Market-data capture is not anonymous. The WebSocket server gates access in two
places: (a) the REST call that fetches a per-connection key requires a valid
main account bearer token, and (b) the first frame sent over the socket must
carry that key alongside the access token. No token → no stream. The Carina
trading session (PIN, session #2) is **not** needed — market-data capture uses
only session #1 (main account).

### 4.1 Auth endpoints used by the collector

| Step | Method & URL | Auth header | Impl |
|------|-------------|-------------|------|
| Password login | `POST exodus.stockbit.com/login/v6/username` | — | `Session.Login()`, `auth.go:69` |
| Per-connection WS key | `GET exodus.stockbit.com/auth/websocket/key` | `Bearer <access>` | `GetWebsocketKey()`, `auth.go:187` |
| Token refresh | `POST exodus.stockbit.com/login/refresh` | `Bearer <refresh>` | `RefreshMain()`, `auth.go:102` |

After `GetWebsocketKey()` returns, the collector sends the first binary frame:

```
WebsocketRequest { key, userId, accessToken }
```

as described in [02-websocket-streaming.md §3](../reference/02-websocket-streaming.md).
The WS key is **short-lived and per-connection** — it is re-fetched on every
(re)connect, never cached across connections.

Token refresh follows the proactive + reactive strategy in
[01-authentication.md §3](../reference/01-authentication.md): refresh when
`access.expired_at − now() < 60s`; also refresh reactively on any 401. After
refresh, re-fetch the WS key, re-send auth, re-send subscribe, write a
`REAUTH` control record.

### 4.2 Headless bootstrap problem on EC2

When `Session.Login()` is called from an **unrecognised device**, the server
does not return tokens — it returns a `new_device.multi_factor` challenge and
pushes a verification prompt to an already-trusted device
([01-authentication.md §1](../reference/01-authentication.md), lines 100–113). A
headless EC2 instance cannot answer that push.

Therefore the collector **must not** attempt a password login on the server.
The intended boot sequence is:

1. **Log in once on a trusted machine** (developer laptop, already trusted via
   the Stockbit mobile app). The laptop is a trusted device, so
   `Session.Login()` returns tokens immediately.
2. **Seed durable storage** with the resulting access + refresh tokens (see
   §4.4 — inject via `SB_ACCESS_TOKEN` / `SB_REFRESH_TOKEN` into SSM Parameter
   Store, or run a one-off seed script).
3. **On EC2**, `Session.Login()` skips the password flow when `accessToken !=
   ""` (`auth.go:70`); the `Config` struct pre-populates both tokens from
   env/store at startup (`auth.go:36–37`). The collector then lives entirely
   off `RefreshMain()`.

As long as `RefreshMain()` runs before the refresh token's own `expired_at`
(~30 days), the EC2 instance never needs another password login.

> **Local development** is unaffected. The developer's laptop is a trusted
> device, so `SB_USERNAME` + `SB_PASSWORD` work directly, or the tokens can
> be set in the environment. The headless complexity applies only to the
> unattended AWS box.

### 4.3 One-time bootstrap checklist

> ✔️ **Done (2026-06-17).** Bootstrap performed and verified end-to-end on the
> live instance (user_id 17301).

- [x] On the developer laptop: set `SB_USERNAME` / `SB_PASSWORD` and run
      `go run ./collector` (or a seed command) to trigger `Session.Login()`.
- [x] Capture the printed or logged `access.token` and `refresh.token`.
- [x] Store each as an SSM `SecureString` parameter under the `/sb-collector/`
      prefix: `/sb-collector/access` and `/sb-collector/refresh`, alongside the
      existing `/sb-collector/watchlist`. Standard tier (free); encrypted with
      the AWS-managed `aws/ssm` KMS key (free).
- [x] Deploy the collector binary. At startup it reads those fields, bypasses
      `Login()`, and enters the refresh loop.
- [x] Verify: first 60 seconds of logs should show `GetWebsocketKey` success
      and `SUBSCRIBE` control record written, with no login-challenge errors.

### 4.4 Refresh-token rotation and persistence

> ⚠️ **Critical design constraint — do not skip this.**
>
> `POST /login/refresh` **rotates** the refresh token: the response contains a
> new one and the old one may be invalidated ([01-authentication.md §3](../reference/01-authentication.md),
> handled in `auth.go:128–132`). The in-memory `Session` keeps the new token,
> but **only in memory**. Because EventBridge stops the EC2 instance every
> evening and restarts it each morning, the process exits each night. On the
> next boot, if the collector reads the original (now invalidated) refresh
> token from the parameter store it will get a `401` on the first refresh attempt
> and be locked out — requiring a manual re-login from a trusted device.
>
> **Every successful `RefreshMain()` call must persist the new tokens back to
> durable storage before the process exits.**

### 4.5 `TokenStore` interface

Define a small Go interface that the `Session` calls after each successful
refresh:

```go
// TokenStore persists the rotating token pair across restarts.
type TokenStore interface {
    Load() (access, refresh string, err error)
    Save(access, refresh string) error
}
```

Two implementations are needed:

**`FileTokenStore`** (dev / local):

```go
// Reads and writes ~/.sb-tokens.json (chmod 0600).
// Never commit this file; add it to .gitignore.
// Treat the refresh token like a password — see docs/reference/01-authentication.md §5.
type FileTokenStore struct{ Path string }

func (f *FileTokenStore) Load() (string, string, error) { /* read + decrypt */ }
func (f *FileTokenStore) Save(access, refresh string) error { /* write + chmod 0600 */ }
```

**`SSMTokenStore`** (prod / AWS — default):

```go
// Reads and writes two SSM SecureString parameters under a prefix
// (e.g. /sb-collector/access and /sb-collector/refresh). Standard tier is
// free and encrypts with the AWS-managed aws/ssm key (also free). Each
// parameter must stay under the 4 KB Standard-tier limit — storing the two
// tokens as separate parameters (not one JSON blob) keeps each well under it.
// Requires ssm:GetParameter + ssm:PutParameter + kms:Decrypt/kms:Encrypt on
// the aws/ssm key, scoped to the /sb-collector/ prefix.
type SSMTokenStore struct {
    Client *ssm.Client
    Prefix string // e.g. "/sb-collector"
}

func (s *SSMTokenStore) Load() (string, string, error) { /* GetParameter ×2, WithDecryption */ }
func (s *SSMTokenStore) Save(access, refresh string) error {
    // Write refresh FIRST (the restart-critical token), then access. PutParameter
    // is per-parameter, so the pair is not updated atomically; ordering refresh
    // first means a crash between the two writes never strands a stale refresh token.
}
```

> **Optional alternative — `SecretsManagerTokenStore`.** If you later want AWS-
> managed rotation (Lambda) or need to store tokens as one >4 KB JSON blob, swap
> in a Secrets Manager implementation (`GetSecretValue` / `PutSecretValue` on a
> single secret ARN, $0.40/secret/month). For this collector — which rotates its
> own tokens in `RefreshMain()` — it adds cost without any feature you use, so
> SSM Parameter Store is the default.

Wire `TokenStore.Save()` into `RefreshMain()` immediately after the mutex
write at `auth.go:128–132`, so rotation is never lost even if the process is
killed shortly after refresh:

```go
// Inside RefreshMain(), after s.mu.Unlock():
if s.store != nil {
    if err := s.store.Save(access, refresh); err != nil {
        log.Printf("WARN: token persist failed: %v — next restart may require re-login", err)
    }
}
```

---

## 5. Capture loop design

### 5.1 Reusing the prototype

The capture binary is **not** a rewrite. It reuses:

- `collector/websocket.go` — `dialWS`, `wsConn.ReadMessage`, `WriteBinary`,
  `SetReadDeadline`, close handling.
- `collector/auth.go` — Exodus login, token refresh (proactive + reactive),
  WebSocket key fetch.
- `collector/datafeed.go` — `buildSubscribe`, `buildPing` for outbound messages.
  The `decodeWrap` / `decodeOrderbookMsg` / `decodeLivePriceMsg` functions are
  **not** called on the capture hot path (frames go straight to disk); they are
  reused unchanged in the replayer.
- `collector/protobuf.go` — the hand-rolled Protobuf codec underlying all of the above.

The collector adds one new responsibility on top: writing raw frames to disk.
Everything else (auth, heartbeat, reconnect, re-auth on 401) is the existing
logic from `collector/main.go`.

### 5.2 Subscribe for full microstructure

Subscribe `runningTradeBatch` instead of `runningTrade` for a multi-symbol
watchlist. Per [02-websocket-streaming.md §4](../reference/02-websocket-streaming.md),
`runningTradeBatch` delivers the same `RunningTrade` records but batched,
reducing per-frame overhead when many symbols are active. Combine with
`orderBook` and `liveprice`:

```go
// WebsocketChannel field numbers (from collector/datafeed.go)
//   2: order_book
//   6: liveprice
// runningTradeBatch = field 11 in WebsocketChannel (websocket.proto)
//   add field 11 to buildSubscribe for each watchlist symbol

var ch pbWriter
for _, sym := range watchlist {
    ch.string(2, sym)   // order_book
    ch.string(6, sym)   // liveprice
    ch.string(11, sym)  // runningTradeBatch (field 11 in WebsocketChannel)
}
```

The subscription is **replace-mode** (not incremental) — the channel sent
replaces the current set. Keep the full desired watchlist in memory and resend
it verbatim on every reconnect and re-auth, as documented in
[02-websocket-streaming.md §4](../reference/02-websocket-streaming.md).

### 5.3 Watchlist configuration

The watchlist is read from a plain text file or environment variable at startup.
One ticker per line, uppercase, no exchange suffix (e.g. `BBCA`, `BMRI`).
Design the config so editing the list requires only a file edit and process
restart — no code change.

```
# watchlist.txt  (one symbol per line; # lines are comments)
BBCA
BMRI
TLKM
ASII
BBRI
```

Or via environment variable for simpler deployments:

```bash
export CAPTURE_WATCHLIST="BBCA,BMRI,TLKM,ASII,BBRI"
```

**SSM Parameter Store (production, authoritative):** when `SSM_PARAM_PREFIX` is
set, the collector reads `/sb-collector/watchlist` and `/sb-collector/watchlist_id`
from SSM at boot — these take priority over the `CAPTURE_WATCHLIST` /
`CAPTURE_WATCHLIST_ID` env vars, which remain a local-dev fallback. Terraform
(OpenTofu) stays authoritative for both parameters. Consequently, changing the
production watchlist requires only `tofu apply` (updates the SSM params) followed
by a collector restart or the next scheduled boot — **no instance replacement**,
even though `ignore_changes = [user_data]` is set on the `aws_instance`.

### 5.4 Raw frame appending (the hot path)

On every binary frame received from `ReadMessage`:

1. Record `recv_ns = time.Now().UnixNano()` immediately after the read returns.
2. Write a **data record** to the open segment file: see
   [02-storage-format-and-replay.md §2](./02-storage-format-and-replay.md)
   for the exact byte layout.
3. Do **not** call `decodeWrap`. The raw bytes go directly to disk.

This is the cheapest possible hot path: one `time.Now()` call and one buffered
`os.File.Write`. CPU is essentially zero.

### 5.5 Control records

Alongside data records, write **control records** at connection lifecycle
events. The replayer needs these to understand discontinuities and avoid
generating false signals across gaps. Write a control record for:

| Event | Control type | When to write |
|-------|-------------|---------------|
| Socket connected + auth sent | `CONNECT` | After `dialWS` + auth frame sent |
| Subscription sent | `SUBSCRIBE` | After subscribe frame sent (include watchlist snapshot) |
| Token refreshed + re-auth sent | `REAUTH` | After 401 handling or proactive refresh |
| Reconnect started | `RECONNECT` | Before backoff sleep |
| `switch_reset` observed in a frame | `SWITCH_RESET` | When any decoded frame carries `switch_reset != 0` |

For `switch_reset` detection, only a minimal field-presence check is needed on
the hot path — check for field 6 in the outer `Orderbook` message (varint,
non-zero). The full decode is deferred to replay. See
[02-storage-format-and-replay.md §2.2](./02-storage-format-and-replay.md)
for the control record byte layout.

### 5.6 Heartbeat, reconnect, and token refresh

Replicate the existing `collector/main.go` logic exactly:

- **Heartbeat:** `buildPing(userID, accessToken)` every 10 seconds.
- **Reconnect:** on any `ReadMessage` error (except close code 4000), wait
  `min(3s, 0.75s * 2^attempt)` then reconnect, re-auth, and re-subscribe.
  Write a `RECONNECT` control record before the backoff sleep.
- **Token refresh:** proactively when `access.expired_at - now() < 60s`. On
  401 error from the server, refresh reactively. After refresh, re-fetch the
  WS key, re-send auth, re-send subscribe, write a `REAUTH` control record.
  See [01-authentication.md §3](../reference/01-authentication.md) for the full refresh
  strategy (single-flight mutex, retry budget).

---

## 6. Segment rolling and compression

### 6.1 Roll trigger

Roll (close the current segment and open a new one) at UTC midnight. This
aligns segments to trading days. If the process starts mid-day, the first
segment covers the remainder of that UTC day.

Also roll if the collector restarts (i.e. on reconnect after a significant
gap, though the normal market-hours scheduling means the process stops cleanly
at end-of-day and a new segment starts at next open).

### 6.2 Compression

Compress the closed segment with **zstd**. Rationale: Protobuf fields are
highly repetitive (same symbol names, similar price values, repeated pipe-
delimited orderbook strings with the same structure), so zstd's LZ77 + entropy
coding achieves very high ratios on this data — typically 5–10x on orderbook
streams. zstd is also faster at both compression and decompression than gzip at
equivalent ratios, and the Go standard library includes a pure-Go zstd decoder
(`golang.org/x/mod` + `github.com/klauspost/compress/zstd` or the `valyala`
variant). For offline compression of closed segments (not the hot write path),
CPU cost is irrelevant.

Compress synchronously in a goroutine after rolling:
```go
// pseudo
go func(closedPath string) {
    compressZstd(closedPath, closedPath+".zst")
    os.Remove(closedPath)
    writeManifest(closedPath + ".zst")
    uploadToS3(closedPath + ".zst")
    uploadToS3(closedPath + ".manifest.json")
}(closedSegmentPath)
```

### 6.3 S3 upload

Use the AWS SDK for Go (`aws-sdk-go-v2`) to upload compressed segments to S3.
Prefer multipart upload for segments over ~100 MB. The IAM role on the EC2
instance (instance profile) should grant only `s3:PutObject` on the target
bucket prefix — no `s3:GetObject` or `s3:DeleteObject` needed from the
collector.

---

## 7. AWS deployment

### 7.1 Region

All resources in **`ap-southeast-3` (Jakarta)**. This minimises round-trip
latency to `wss://wss-trading.stockbit.com/ws` (Stockbit is an Indonesian
company; their servers are Jakarta-hosted).

### 7.2 Compute — options and costs

The collector is a Go binary. It runs continuously during market hours (a long-
lived WebSocket loop). The data throughput at ~30 symbols is small: typically
tens of kilobytes per second at peak (orderbook + trade updates). Any instance
with a network connection is sufficient; the bottleneck is never CPU or memory.

IDX market hours (Mon–Fri, excluding holidays):
- Pre-open: 08:30–08:59 WIB (optional — needed for IEP/IEV, not for this phase)
- Session 1: 09:00–11:59 WIB
- Session 2: 13:30–15:59 WIB (15:49 for some boards)
- Total: ~5 hours per day, ~20–22 trading days/month = ~100–110 hours/month

With EventBridge start/stop scheduling (start at 08:30 WIB, stop at
16:25 WIB — see §7.3), actual runtime is ~7h55m/trading day → ~170 hours/month.
Start is 15 min before IDX pre-opening (08:45) to absorb instance boot + auth +
WS-connect latency; stop is ~10 min after the post-trading close (16:15) to
leave room for the SIGTERM segment flush/compress/upload.

| Option | vCPU | RAM | On-Demand price (ap-se-3) | ~170 hrs/month | Notes |
|--------|------|-----|--------------------------|----------------|-------|
| **`t4g.nano`** (Graviton) | 2 | 0.5 GB | ~$0.0042/hr | **~$0.72/mo** | Recommended — Go ARM binary, easily sufficient |
| `t4g.micro` (Graviton) | 2 | 1 GB | ~$0.0084/hr | ~$1.44/mo | Headroom if watchlist grows to 100+ symbols |
| Lightsail nano | 1 | 0.5 GB | **$3.50/mo flat** | $3.50/mo | Simpler: no EventBridge, no scheduling; always-on |
| Fargate (0.25 vCPU, 0.5 GB) | 0.25 | 0.5 GB | ~$0.04048/vCPU-hr | ~$9.10/mo | More complex; priced always-on; not recommended |

**Recommendation: EC2 `t4g.nano` with EventBridge scheduling.**

Rationale: Go is natively ARM-friendly (`GOARCH=arm64`) — the prototype builds
and runs identically on Graviton. The `t4g.nano` is the smallest Graviton
instance. At 150 hours/month it costs under $1. EventBridge start/stop is a
one-time configuration. The EBS root volume (gp3, 8 GB minimum) adds ~$0.64/mo.

Lightsail is a valid simpler alternative if you want a flat bill and no
scheduling setup — $3.50–5/month regardless of whether it runs 24/7 or not.
Choose it if EventBridge setup feels like unnecessary complexity for a prototype.

Fargate is genuinely more complex for a long-lived socket (tasks re-create on
each invocation, cold-start overhead, NAT Gateway costs for S3 access unless
using a VPC endpoint) and costs more. Skip it.

### 7.3 EventBridge market-hours scheduling

Create two EventBridge Scheduler rules targeting the EC2 `StartInstances` and
`StopInstances` API:

```
Start:  cron(30 1 * * ? *)    # 01:30 UTC = 08:30 WIB
Stop:   cron(25 9 * * ? *)    # 09:25 UTC = 16:25 WIB
```

Both rules should have a condition to only fire on weekdays. EventBridge
Scheduler supports day-of-week in cron expressions (`MON-FRI`):

```
Start:  cron(30 1 ? * MON-FRI *)
Stop:   cron(25 9 ? * MON-FRI *)
```

Start at 08:30 WIB is 15 minutes before IDX **pre-opening** (08:45) so the
instance boot, token refresh, WS key fetch, and subscribe all complete before
the first auction frames — and the full pre-opening auction is captured. Stop at
16:25 WIB is ~10 minutes after the **post-trading** close (16:15) so the last
frames land and the open segment flushes cleanly on `SIGTERM`. The collector
stays connected through the 12:00–13:30 lunch break (idle but cheap — far
simpler than a stop/restart and a second daily auth bootstrap).

> IDX holiday calendar is not automatically excluded. For extended accuracy,
> run the collector even on IDX holidays (the feed simply has no updates, and
> the daily segment will be nearly empty — trivial cost).

The collector process should detect that no data is flowing (e.g. no frames
for 30 seconds outside the heartbeat reply) and log the idle state. The
EventBridge stop at 16:15 WIB terminates the instance cleanly; the collector
must flush and close the open segment on `SIGTERM` before exit.

### 7.4 S3 bucket layout and lifecycle

```
s3://sb-marketdata-<accountid>/
  raw/
    dt=2026-01-15/
      watchlist-default/
        seg-084500.binpb.zst
        seg-084500.manifest.json
    dt=2026-01-16/
      watchlist-default/
        seg-084501.binpb.zst
        seg-084501.manifest.json
```

S3 lifecycle policy on the `raw/` prefix:

| Age | Storage class | Rationale |
|-----|--------------|-----------|
| 0–7 days | S3 Standard | Fast access for recent backtests |
| 8–30 days | S3 Standard-IA | Saves ~45% vs Standard; still millisecond access |
| 31+ days | Glacier Deep Archive | ~$0.00099/GB-month; cheapest cold archive |

Glacier retrieval (Standard tier) takes 12 hours and costs $0.02/GB. For a
backtesting workflow operating on recent data, Standard-IA is usually sufficient.
Glacier is for the long-term raw archive.

### 7.5 IAM least-privilege

The EC2 instance should run with an **instance profile** (no access keys in
code or environment). The instance profile's IAM role needs only:

```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject"],
  "Resource": "arn:aws:s3:::sb-marketdata-<accountid>/raw/*"
},
{
  "Effect": "Allow",
  "Action": [
    "ssm:GetParameter",
    "ssm:GetParameters",
    "ssm:PutParameter"
  ],
  "Resource": "arn:aws:ssm:ap-southeast-3:<accountid>:parameter/sb-collector/*"
},
{
  "Effect": "Allow",
  "Action": ["kms:Decrypt", "kms:Encrypt"],
  "Resource": "*",
  "Condition": { "StringEquals": { "kms:ViaService": "ssm.ap-southeast-3.amazonaws.com" } }
}
```

The collector never reads from S3 (upload only). Reading for replay is done
from a separate workstation or analysis instance with `s3:GetObject`.

Stockbit credentials (watchlist, and the rotating access + refresh tokens) are
stored as **SSM Parameter Store `SecureString` parameters** under the
`/sb-collector/` prefix (`/sb-collector/watchlist`, `/sb-collector/access`,
`/sb-collector/refresh`). Both `ssm:GetParameter` (boot-time read) and
`ssm:PutParameter` (post-refresh write via `SSMTokenStore.Save()`) are required
— see §4.4 on why write access is mandatory. `kms:Decrypt`/`kms:Encrypt` (via
the AWS-managed `aws/ssm` key) is needed for SecureString encryption. Scope all
actions to the `/sb-collector/` prefix. Standard-tier parameters are free.

---

## 8. Monthly cost breakdown

Assumptions: 30-symbol watchlist, ~22 trading days/month, ~7h50m/trading
day (~170 hours/month EC2 runtime, 08:30–16:25 WIB), ~50 MB/day compressed data
(full microstructure, 30 symbols, zstd — could be lower for quiet days).

| Resource | Unit cost | Monthly usage | Monthly cost |
|----------|-----------|---------------|--------------|
| EC2 `t4g.nano` compute | $0.0042/hr | 170 hrs | ~$0.72 |
| EBS gp3 8 GB (root) | $0.08/GB-mo | 8 GB | ~$0.64 |
| S3 Standard (0–7 days, ~350 MB) | $0.025/GB-mo | 0.35 GB | ~$0.01 |
| S3 Standard-IA (8–30 days, ~1.1 GB) | $0.0138/GB-mo | 1.1 GB | ~$0.02 |
| S3 Glacier Deep Archive (older) | $0.00099/GB-mo | grows slowly | ~$0.00 initially |
| S3 PUT requests | $0.005/1000 | ~60/month | negligible |
| Data transfer (EC2→S3, same region) | $0.00 | — | $0.00 |
| SSM Parameter Store (Standard SecureString) | $0.00 | 3 params | $0.00 |
| EventBridge Scheduler | $1.00/million invocations | ~44/month | ~$0.00 |
| **Total** | | | **~$1.40/month** |

> Data transfer between EC2 and S3 within the same region is free. Token storage
> uses SSM Parameter Store Standard-tier SecureStrings (free). Secrets Manager
> ($0.40/secret/month) is an optional swap only if you later want AWS-managed
> rotation — not needed here, since the collector rotates its own tokens (§4.5).

---

## 9. Failure modes and data integrity

### 9.1 Frame gaps — `sequence_number`

The consumer `Orderbook` message carries a `sequence_number` (int64). This is
a **global feed counter shared across all symbols on the exchange feed** — it
is NOT monotonically increasing per symbol. Additionally, BID and OFFER frames
carry separate interleaved sub-sequences, so consecutive `sequence_number`
values seen for a single symbol can even decrease (an OFFER frame at seq N may
be followed by a BID frame at seq N-1). Per-symbol sequence-gap detection is
therefore **not a reliable frame-loss signal** and should not be used.

Real capture discontinuities are instead detected via **RECONNECT control
records** written by the collector whenever the WebSocket connection is lost and
re-established (see §5.5 and §9.3). After a reconnect the server re-snapshots
the current state of subscribed symbols, so strategies should treat the first
orderbook frames after a `RECONNECT` record as full snapshots rather than
incremental updates.

During capture, sequence numbers are preserved in the raw frames — no special
handling is needed. The `sequence_number` is visible in the raw protobuf bytes
(field 3 of the consumer `Orderbook` message, wire type varint) without a full
decode. It can be useful for observing the global counter range across a
segment, but not for per-symbol gap counting.

### 9.2 `switch_reset` — feed-source switches

The `switch_reset` field in the consumer `Orderbook` message (`SwitchReset`
enum, field 6) signals that the feed source has switched (e.g. ITCH to a
different source). When non-zero, the entire book state should be treated as
reset — strategies should re-build their book from the next set of BID/OFFER
messages rather than diffing from the previous state.

The collector writes a `SWITCH_RESET` control record when it observes this
(only requires checking field 6 presence in the outer message — a minimal
partial decode). The replayer surfaces this to strategies as a `RESET` event.

### 9.3 Reconnect duplicate frames

After a reconnect, the server replays the current state of subscribed symbols
(a snapshot of the latest orderbook, current liveprice). These are not
duplicates in the sense of repeated trade ticks — they are re-snapshots. The
replayer uses the `RECONNECT` control record to mark the boundary: strategies
should treat the first orderbook frames after a `RECONNECT` record as full
snapshots, not incremental updates.

### 9.4 Token refresh continuity

The proactive refresh strategy (60-second margin before `access.expired_at`)
means the WS stream stays alive through refresh — the socket is not dropped.
Only the auth/subscribe messages are resent with new credentials. The `REAUTH`
control record marks this boundary; the frame stream is continuous across it.
Replay is unaffected since the raw frames before and after a `REAUTH` are
identical in structure.

### 9.5 Process crash or ungraceful stop

If the collector crashes mid-segment, the last segment file may be incomplete
(missing the trailing bytes of the last record). The replayer must tolerate a
truncated final record: read until `io.EOF` or a short read, discard the
partial record, and treat it as end-of-segment. The manifest will be absent
for a crashed segment; the replayer should handle missing manifests gracefully
(treat missing manifest = unknown gaps, process with caution).

On `SIGTERM` (EventBridge stop at end of day), the collector performs a clean
shutdown: flush the write buffer, close the segment, compress it, write the
manifest, upload to S3, then exit. Implement a `signal.Notify(sigCh, syscall.SIGTERM)`
goroutine in `main.go`.

### 9.6 S3 upload failure

If the S3 upload fails after compression (network error, permission issue), the
compressed segment remains on the EBS root volume. Implement a retry loop with
exponential backoff (3 attempts). On persistent failure, log the error and
leave the file on disk — the next scheduled run will have a fresh EBS volume
if the instance was stopped and restarted. For safety, keep the last N compressed
segments on disk (e.g. 3 days) before deleting after successful upload, so
transient upload failures do not cause data loss.

---

## 10. Operational checklist

> ✔️ **Provisioned & verified 2026-06-17** (account `047915756052`). Boxes reflect
> the live deployment; deviations from the spec are noted inline.

- [x] EC2 `t4g.nano` launched in `ap-southeast-3`, Amazon Linux 2023 ARM AMI
- [x] Collector binary cross-compiled for `GOARCH=arm64 GOOS=linux` *(built with Homebrew go 1.25.1; go.mod requires 1.21)* and delivered via S3 + every-boot `update.sh` (`ExecStartPre`), not baked into the AMI
- [x] Systemd service unit: `collector.service` (start on boot, restart on failure)
- [x] EventBridge Scheduler rules for start (08:30 WIB) and stop (16:25 WIB), weekdays
- [x] IAM instance profile: `s3:PutObject` on `raw/*`, `s3:GetObject` on `artifacts/*`, `ssm:GetParameter` + `ssm:PutParameter` on `/sb-collector/*`, and `kms:Decrypt`/`kms:Encrypt` via `aws/ssm` (see §7.5)
- [x] S3 bucket in `ap-southeast-3`, lifecycle policy configured *(bucket `marketdata-<accountid>`; versioning **Suspended** for cost; lifecycle 30d→IA, 90d→Deep Archive)*
- [x] Bootstrap performed: logged in from developer laptop, access + refresh tokens seeded into SSM SecureString params `/sb-collector/access` + `/sb-collector/refresh` — see §4.3
- [x] Segment directory on EBS: `/var/lib/collector/segments/`
- [x] Log rotation configured (systemd journal, `SyslogIdentifier=collector`)
- [x] At least one test capture run validated *(live capture → S3 confirmed 2026-06-17; replayer validated locally on Phase 1 segments — replay of a downloaded S3 day still pending, see §11 Phase 2)*

---

## 11. Phased implementation

### Phase 1 — local, single-symbol (proof of pipeline)

Goal: prove the capture → compress → replay loop works end-to-end before
touching AWS.

> ✔️ **Done** (2026-06-17) — capture → replay verified end-to-end; the price
> graph reconstructed from a recorded segment matched the live feed.

- [x] Add a `cmd/collector/` directory to the prototype. Reuse `websocket.go`,
   `auth.go`, `datafeed.go`, `protobuf.go` from `collector/` (either as a
   shared internal package or via direct copy initially).
- [x] Define the `TokenStore` interface and implement `FileTokenStore` (writes to
   `~/.sb-tokens.json`, chmod 0600). Wire `TokenStore.Save()` into `RefreshMain()`
   so the rotated refresh token is persisted after every refresh. Use
   `SB_USERNAME`/`SB_PASSWORD` or pre-set env tokens on the developer laptop.
- [x] Implement the binary segment writer (data + control records) per the spec in
   [02-storage-format-and-replay.md](./02-storage-format-and-replay.md).
- [x] Subscribe to a single symbol (e.g. `BBCA`) for 5 minutes. Verify the
   segment file grows and contains valid records.
- [x] Implement the replayer (as-fast-as-possible mode). Feed recorded frames
   through `decodeWrap` and verify decoded output matches what was seen live.
- [x] Add zstd compression of the closed segment. Verify the replayer can read
   compressed segments (decompress-then-replay pipeline).
- [x] Implement `sbcap stat` and `sbcap dump` (the inspector shares the replayer's
   `decodeWrap` path and the `verify` health-check logic — they can be the same
   binary). Use `sbcap stat` to confirm record counts, symbol coverage, and that
   the final record is not truncated. Use `sbcap dump --symbol BBCA` to eyeball
   decoded events from the freshly captured segment. See
   [02-storage-format-and-replay.md §7](./02-storage-format-and-replay.md)
   for the full subcommand reference.

**Done when:** a 5-minute recording of `BBCA` can be replayed offline and
produces the same stream of decoded orderbook/liveprice/trade events that
were logged during capture, and `sbcap stat` reports no gaps or truncation.

### Phase 2 — watchlist + S3 + scheduling

Goal: production-grade multi-symbol capture running on AWS.

> **Status (2026-06-17):** **deployed and verified end-to-end on AWS.** The
> OpenTofu stack was applied to account `047915756052` (`ap-southeast-3`, 26
> resources), tokens were seeded into SSM, the arm64 binary was uploaded to
> `s3://marketdata-047915756052/artifacts/collector`, and a live EC2 run
> confirmed the full path: SSM token load → WS auth → subscribe
> (runningTradeBatch+orderBook+liveprice+iepiev, 5 symbols) → capture → SIGTERM
> flush → zstd → S3 upload to `raw/`. The instance is currently **stopped**; the
> EventBridge schedulers start/stop it on the WIB market window. Only the
> week-long soak is now complete (2026-06-26 — 5 days, 45 segments, ~1.1 GiB confirmed). Only a replay of a downloaded day remains open.
>
> **Deployment deviations from the spec above** (intentional, see [§12](#12-infrastructure-as-code-opentofu)):
> bucket is `marketdata-<accountid>` (the `sb-` prefix was dropped); S3 lifecycle
> is Standard → STANDARD_IA @30d → DEEP_ARCHIVE @90d (AWS requires IA ≥ 30 days,
> so not the 7/30 in §7.4); bucket versioning is **Suspended** (cost cap, with
> noncurrent-version expiry). The binary is pulled from S3 on **every** boot via a
> systemd `ExecStartPre` updater (`update.sh`), since EventBridge stop/start never
> re-runs cloud-init.

> The AWS-side of steps 4–8 (S3 bucket + lifecycle, SSM token parameters, IAM
> instance profile, EC2 instance, and EventBridge start/stop scheduling) is
> codified as OpenTofu — see [§12](#12-infrastructure-as-code-opentofu). The Go
> work (steps 1–3, 5–6, 10) is unchanged.

- [x] Extend `buildSubscribe` to support the full watchlist (all three channels:
   `runningTradeBatch`, `orderBook`, `liveprice`). *(Also fixed a latent bug: the
   `running_trade_batch` subscribe field was `11`; the proto defines it as `5`.
   Added running-trade decode (inbound oneof fields 1 and 8) to `DecodeWrap`,
   with batch frames expanded to one event per trade in the replayer.)*
- [x] Add `switch_reset` detection to the hot path (write `SWITCH_RESET` control
   record). *(Cheap partial decode `feed.DetectSwitchReset`; per-symbol dedup so a
   sustained reset flag emits one record, not thousands.)*
- [x] Implement `SSMTokenStore` (read at boot via `ssm:GetParameter`, write after
   every refresh via `ssm:PutParameter`). Swap out `FileTokenStore` in the
   collector's `main.go` when `SSM_PARAM_PREFIX` is set. *(Client behind an
   injectable interface; unit-tested with a fake — no live AWS.)*
- [x] Follow the one-time bootstrap checklist (§4.3): log in from the developer
   laptop, seed the `/sb-collector/access` and `/sb-collector/refresh`
   SecureString parameters, then deploy. *(Done 2026-06-17 — tokens seeded
   out-of-band, user_id 17301; SSM load verified on the live instance.)*
- [x] Implement UTC-midnight segment rolling and S3 upload with retry. *(Rolling
   landed in Phase 1; `internal/s3upload` adds upload of the `.binpb.zst` +
   `.manifest.json` with 3-attempt exponential backoff, gated on `S3_BUCKET`,
   leaving local files on persistent failure per §9.6.)*
- [x] Package as a `systemd` service unit with `SIGTERM` handler for clean shutdown.
   *(Unit at `infra/collector.service` with `KillSignal=SIGTERM` +
   `TimeoutStopSec=120`; the `main.go` SIGTERM handler flushes/compresses/uploads
   the open segment before exit.)*
- [x] Deploy to EC2 `t4g.nano` in `ap-southeast-3` with the IAM instance profile
   (`s3:PutObject` + `ssm:GetParameter` + `ssm:PutParameter` + `kms:Decrypt`/`kms:Encrypt`
   scoped to the `/sb-collector/` prefix — see §7.5). Provisioned via OpenTofu (§12).
   *(Done 2026-06-17 — `tofu apply`, 26 resources; live instance verified, then
   stopped. The IAM role also grants `s3:GetObject` on `artifacts/*` for the
   every-boot binary fetch.)*
- [x] Configure EventBridge Scheduler for weekday market-hours start/stop (§12).
   *(Done 2026-06-17 — schedulers applied; auto-start 08:30 / stop 16:25 WIB Mon–Fri.)*
- [x] Run for one full trading week. Verify: segments appear in S3, manifest files
   are present, segment byte counts are non-zero for each trading day, and
   the `/sb-collector/refresh` parameter shows a fresh value after each daily run.
   *(Done 2026-06-26 — 5 trading days Mon–Fri Jun 22–26, 45 segments × plan10-survivors
   stream, ~1.1 GiB total; all days show 9 segments each covering full session.)*
- [x] Run the replayer against a downloaded day's segments. *(Done 2026-06-26 —
   downloaded all 9 `plan10-survivors` segments for 2026-06-25 from S3; all decoded
   with `truncated: no`; event content verified (PRICE/BOOK/TRADE with correct fields);
   TPIA seq monotonic across all 9 boundaries; lunch-break RECONNECTs and closing-auction
   orderbook-absent behaviour both confirmed correct.)*

**Done when:** a full week of data is in S3, the lifecycle policy is active,
and the replayer successfully reads a downloaded day.

### Phase 3 (optional) — Parquet analytics tier

Goal: enable fast vectorized backtests and research against a columnar store,
and unlock cross-day inspection without downloading raw segments.

A separate offline job (run weekly or on-demand) reads raw segments from S3,
decodes every frame via `decodeWrap`, and writes Parquet files partitioned by
symbol and date. See
[02-storage-format-and-replay.md §9](./02-storage-format-and-replay.md)
for the Parquet schema.

Once the Parquet tier is populated, **DuckDB reads directly from S3** — no
download required. This is the recommended path for querying or aggregating
data across many days (e.g. large-trade counts, VWAP series, spread
distributions). Polars is the alternative for Python DataFrame workflows. See
[02-storage-format-and-replay.md §7.2](./02-storage-format-and-replay.md)
for example queries and the "which tool to use" summary.

The raw log remains the source of truth and the only store the backtester
replays. Parquet is a derived convenience layer — if a query looks wrong,
re-decode from raw via the decoder job and re-check with `sbcap dump`.

This phase adds no changes to the collector or the raw archive.

---

## 12. Infrastructure as Code (OpenTofu)

All AWS resources in §7 are provisioned with **OpenTofu**, kept under `infra/`.
State is a **local `terraform.tfstate`** file (gitignored) — sufficient for a
single operator; migrate to an S3 backend if more than one machine/person ever
manages the stack. Cost auditing is built in through three mechanisms: provider
`default_tags`, cost-allocation-tag activation, and a budget alarm (§12.2).

> This section is the design reference for the OpenTofu config. The `.tf` files
> are implemented under [`infra/`](../../infra/) and were **applied 2026-06-17**
> to account `047915756052` (26 resources). Nothing here changes the collector or
> replayer. See the §11 Phase 2 status note for the as-built deviations (bucket
> name, lifecycle days, suspended versioning, every-boot binary fetch).

### 12.1 Layout

`infra/` is a flat root config (no modules — KISS for a single deployment):

```
infra/
  versions.tf            # required_version + AWS provider pin (~> 5.60)
  providers.tf           # ap-southeast-3 + aliased us-east-1, both with default_tags
  locals.tf              # common_tags (the cost-audit tag set)
  variables.tf           # inputs (§12.4)
  data.tf                # caller identity, region, latest AL2023 arm64 AMI
  s3.tf                  # bucket + versioning + SSE + lifecycle + public-access-block
  ssm.tf                 # /sb-collector/{access,refresh,watchlist} parameters
  iam.tf                 # collector role + instance profile
  ec2.tf                 # t4g.nano + egress-only SG + user_data
  scheduler.tf           # EventBridge start/stop schedules + scheduler role
  budget.tf              # AWS Budgets monthly cost alarm
  cost.tf                # cost-allocation-tag activation
  outputs.tf             # bucket name, instance id, token-seed commands
  user_data.sh.tftpl     # cloud-init → systemd unit
  terraform.tfvars.example
  .gitignore             # ignores state + *.tfvars; commits .terraform.lock.hcl
  README.md
```

### 12.2 Cost-audit tagging (the primary requirement)

Three layers make every dollar attributable to this project:

**1. `default_tags` — every taggable resource is tagged automatically.** Defined
once on the provider so no resource can be created untagged:

```hcl
# providers.tf
provider "aws" {
  region = var.aws_region
  default_tags { tags = local.common_tags }
}

# locals.tf
locals {
  common_tags = merge({
    Project     = var.project              # primary cost-allocation dimension
    Component   = "market-data-collector"  # isolates this workload
    Environment = var.environment
    CostCenter  = var.cost_center
    ManagedBy   = "opentofu"
    Repo        = "sb-algo-trading-explore"
  }, var.extra_tags)
}
```

**2. `aws_ce_cost_allocation_tag` — make the tags groupable in billing.** A tag
only becomes a Cost Explorer / CUR dimension after activation. The standalone
account can self-activate (via a `us-east-1`-aliased provider, since Cost Explorer
is only reachable there):

```hcl
# cost.tf
resource "aws_ce_cost_allocation_tag" "project" {
  count    = var.activate_cost_allocation_tags ? 1 : 0
  provider = aws.us_east_1
  tag_key  = "Project"
  status   = "Active"
}
# ... same for Component and CostCenter
```

> Newly activated tags take up to ~24h to appear as a grouping dimension, and
> only attribute costs incurred **after** activation.

**3. `aws_budgets_budget` — a spend guardrail.** Monthly COST budget scoped to
this project's tag, with email alerts:

```hcl
# budget.tf
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_limit_usd)  # default 5
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter { name = "TagKeyValue"
    values = [format("user:Project$%s", var.project)] }
  # notifications at 80% ACTUAL and 100% FORECASTED → var.budget_alert_emails
}
```

**How to audit later:** Cost Explorer → *Group by → Tag: `Project`* (or
`Component`), or filter `Component = market-data-collector` to isolate this
workload from anything else in the account.

### 12.3 Resources provisioned

| Resource | Notes | Plan ref |
|----------|-------|----------|
| `aws_s3_bucket` (+ versioning, SSE-S3, public-access-block, lifecycle) | `raw/` rule: 7d→Standard-IA, 30d→Deep Archive, abort-incomplete-MPU 7d, noncurrent-version expiry 30d | §6.2, §7.4 |
| `aws_ssm_parameter` ×3 | `/sb-collector/access` + `/refresh` are SecureString with `lifecycle { ignore_changes = [value] }` so the collector's token rotation is never reverted **and the real tokens stay out of TF state** (only placeholders are applied); `/sb-collector/watchlist` is a plain String | §4.5, §7.5 |
| `aws_iam_role` + `aws_iam_instance_profile` | `s3:PutObject` on `raw/*`; `ssm:GetParameter`/`GetParameters`/`PutParameter` on the prefix; `kms:Decrypt`/`Encrypt` via `kms:ViaService = ssm`; plus `AmazonSSMManagedInstanceCore` so you shell in through Session Manager (no inbound SSH) | §7.5 |
| `aws_instance` (`t4g.nano`) | AL2023 arm64 from the SSM public AMI parameter; gp3 8 GB encrypted; IMDSv2 required; egress-only SG; `ignore_changes = [ami]` to avoid surprise replacement on a new AL2023 release; `user_data` writes the systemd unit with `KillSignal=SIGTERM` + `TimeoutStopSec=120` so the open segment flushes/compresses/uploads before a scheduled stop | §7.2, §9.5, §10 |
| `aws_scheduler_schedule` ×2 + scheduler IAM role | EventBridge Scheduler universal targets (`ec2:startInstances` / `ec2:stopInstances`), `schedule_expression_timezone = "Asia/Jakarta"`, `MON-FRI` | §7.3 |
| `aws_budgets_budget`, `aws_ce_cost_allocation_tag` ×3 | Cost governance | §8, §12.2 |

### 12.4 Key variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `aws_region` | `ap-southeast-3` | All resources (Jakarta) |
| `project` / `environment` / `cost_center` | `sb-algo-trading` / `prod` / `market-data` | Cost-audit tag values |
| `watchlist` / `watchlist_id` | `["BBCA","BMRI","TLKM","ASII","BBRI"]` / `default` | Symbols + S3 key prefix |
| `instance_type` / `root_volume_size` | `t4g.nano` / `8` | Compute |
| `market_open_schedule` / `market_close_schedule` / `schedule_timezone` | `cron(30 8 ? * MON-FRI *)` / `cron(25 16 ? * MON-FRI *)` / `Asia/Jakarta` | 08:30–16:25 WIB (15 min pre-open margin / 10 min post-close flush) |
| `monthly_budget_limit_usd` / `budget_alert_emails` | `5` / `[]` | Budget guardrail |
| `collector_artifact_s3_uri` | `""` | Optional: binary fetched by cloud-init |
| `activate_cost_allocation_tags` | `true` | Self-activate tags (standalone account) |

### 12.5 Deploy workflow

1. `tofu init` → `tofu apply` — creates everything; the two token parameters are
   applied with placeholder values.
2. Seed the real tokens once, out of band (they never enter TF state):
   ```bash
   aws ssm put-parameter --name /sb-collector/refresh --type SecureString --overwrite --value "<REFRESH>"
   aws ssm put-parameter --name /sb-collector/access  --type SecureString --overwrite --value "<ACCESS>"
   ```
   (refresh first — it's the restart-critical token; see §4.5.)
3. Deliver the collector binary: set `collector_artifact_s3_uri`, or bake it into a
   custom AMI.
4. EventBridge Scheduler then starts/stops the instance automatically on the WIB
   window; no always-on cost.

> The scheduler uses `Asia/Jakarta` directly (08:30 / 16:25 local), which produces
> the same wall-clock behaviour as the raw-UTC cron in §7.3 without manual
> conversion. Both are equivalent; the Tofu config prefers the timezone-aware form.

### 12.6 Decisions & caveats

- **Local state, standalone account** (your choices). Lock file `.terraform.lock.hcl`
  is committed; `*.tfstate` and `*.tfvars` are gitignored.
- **Secrets never live in TF** — placeholder values + `ignore_changes` keep the real
  tokens in SSM only.
- **AMI is pinned via `ignore_changes`** — rebuild deliberately, never on a silent
  upstream AL2023 release.
- **Default VPC/subnet** is used. If `ap-southeast-3` has no default VPC (e.g. it was
  deleted), create one or add a `subnet_id` variable before `apply`.

---

Back to [Plans Index](./README.md) · [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) · [docs/README.md](../README.md)
