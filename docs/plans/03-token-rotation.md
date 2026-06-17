# Seamless Token Rotation

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-17
> **Related:** [01-market-data-capture.md](./01-market-data-capture.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [Authentication](../01-authentication.md)

This plan makes Stockbit token rotation seamless and self-healing for the
headless EC2 collector:

1. **SSM is authoritative** — a stray `SB_ACCESS_TOKEN` env var can no longer
   shadow the live SSM token.
2. **Externally-rotated tokens are adopted, never clobbered** — a token seeded
   from a laptop mid-session is picked up within one refresh cycle (SSM version
   reconcile).
3. **Refresh-token death self-heals headlessly** — the collector re-logs-in with
   credentials + a trusted `player_id` and re-seeds SSM, with reconcile and a loud
   failure as fallbacks.

> ⚠️ **Disclaimer.** See [01-market-data-capture.md §1](./01-market-data-capture.md)
> for the full disclaimer on undocumented Stockbit endpoints and ToS.

---

## 1. Context — why this plan exists

**The worry that started this:** "if we put the token in SSM, once we rotate it
the env var won't be updated." Investigation shows the production path is already
SSM-direct, not SSM→env→app:

- On EC2 the systemd unit (`infra/user_data.sh.tftpl:94`) sets **only**
  `SSM_PARAM_PREFIX=/sb-collector`. `SB_ACCESS_TOKEN` / `SB_REFRESH_TOKEN` are
  never set in prod — they exist only for local dev.
- Boot (`collector/cmd/collector/main.go:82`) reads tokens straight from SSM;
  every successful refresh (`collector/internal/feed/session.go:204`) writes the
  rotated pair back to SSM. **SSM is already the single source of truth and stays
  current** as tokens rotate.

So the literal "stale env var" symptom does not bite prod. Three real gaps remain:

1. **Env-var-wins is a footgun** (`main.go:82`: `if cfg.feedCfg.AccessToken == ""`).
   If `SB_ACCESS_TOKEN` is ever set it silently shadows SSM and you run a stale token.
2. **Mid-session manual swap races and clobbers.** The running process only reads
   SSM at boot. A `put-parameter` from your laptop while it is live is ignored — and
   its next 20-min refresh **overwrites your hand-seeded value** with one derived
   from its stale in-memory token.
3. **Refresh-token death has no recovery.** `/login/refresh` 401 →
   `"full login required"` (`session.go:177`) and the collector dies.

**Auth facts** ([01-authentication.md](../01-authentication.md)): access token
lives hours, refresh token ~30 days and rotates on every `/login/refresh`;
concurrent sessions are allowed; `/login/refresh` carries **no** device identifier
so refresh never re-challenges. Because the EventBridge scheduler **stops/starts**
(never re-launches) the instance, each market-open boot cold-reads SSM — a free
daily pickup window. Gap 2's version-reconcile adds mid-session pickup on top.

### 1.1 Headless re-login is feasible — device-trust finding (2026-06-17)

A trace of all four clients (`sb-android`, `sb-ios`, `sb-web`) and the proto
contract (`sb-protos/proto/social/auth/v6/service.proto`) resolved the question
of whether a headless box can re-login after refresh-token death:

- **Device trust is keyed on a client-chosen `player_id` string. There is NO
  server-issued device identifier.** The new-device verify response returns only
  the normal token pair (`VerifyNewDeviceLoginResponse → social.auth.v1.LoginResponseData`
  = user + access + refresh) — nothing to capture or persist. Trust lives
  server-side, indexed by the `player_id` the client sends.
- **`player_id` need not be hardware-derived.** Android computes it from
  `ANDROID_ID + Build.BRAND + Build.MODEL`, but **web uses a pure random `uuidv4()`**
  persisted in localStorage, and iOS uses `identifierForVendor`-or-random-UUID. A
  stable random UUID is a valid `player_id`.
- **`signature` is optional.** In the proto it is `optional string signature = 6`.
  Android sends an ECDSA device-key signature as extra hardening; **web and iOS
  send none.** So a Go client can omit it.
- **The collector already uses the mobile `/login/v6/username` endpoint**, which —
  unlike web's `/v3/.../browser` — needs **no reCAPTCHA**.

**Therefore:** generate a stable random `player_id`, establish trust for it **once**
interactively (complete the new-device challenge from your phone), store
credentials + `player_id` in SSM, and the collector can re-login headlessly on
refresh-token death — the server sees a trusted `player_id` and returns tokens
directly, no challenge.

**Two unknowns remain that only an empirical test settles (Phase 0):**

- Does a v6 password login with a chosen `player_id` and **no `signature`** succeed
  against the server (the proto says optional; web/iOS corroborate, but untested by us)?
- Does trust survive an **IP change**? Trust is keyed on `player_id` not IP, and
  IP/geo is only recorded server-side (not a login field), so a scheduled EC2's
  changing public IP *should not* re-challenge — but server-side risk heuristics on
  a drastic geo jump are possible. A scheduled EC2 gets a new public IP per
  stop/start unless an Elastic IP is attached; Phase 0 determines whether one is needed.

## 2. Locked decisions

- **Scope:** harden env precedence + hot-reload from SSM + **headless re-login as
  the primary refresh-death recovery** (promoted from best-effort after the
  device-trust finding).
- **Pickup speed for external swaps:** next refresh cycle (**≤20 min**) — re-check
  SSM before each scheduled refresh and on the reactive 401 path. No SIGHUP.
- **`player_id`:** a stable random UUID, seeded once to SSM; trust established once
  interactively. No `signature` sent.
- **Credentials on EC2:** `SB_USERNAME` + `SB_PASSWORD` stored in SSM SecureString
  (accepted blast-radius increase; same KMS + scoped-IAM model as the tokens).
- **Trading tokens:** main feed pair only now; keep the design trading-extensible (§5).

---

## 3. Implementation

### Phase 0 — Trust + IP validation spike (GATING)

De-risk the two unknowns before building the in-collector path. A throwaway
standalone probe is enough; do not wire it into the collector yet.

- [ ] Build a small Go probe (or extend `sbcap`) that POSTs `/login/v6/username`
  with `{user, password, player_id}` and **no `signature`**, and prints whether the
  response is a token pair or a `new_device` challenge.
- [ ] Pick a fixed random UUID as `player_id`. Run the probe; expect a `new_device`
  challenge on first use.
- [ ] Complete the new-device verification **once** (multi_factor OTP or trusted-device
  approval from your phone) for that `player_id`. Record the exact verify call shape
  observed (the proto's `VerifyNewDeviceLoginRequest` multi_factor vs trusted_device).
- [ ] Re-run the probe with the same `player_id` from the **same** network → expect
  tokens directly (trust established, no signature needed). **Confirms unknown #1.**
- [ ] Re-run from a **different IP** (tether/VPN, or an EC2 box in `ap-southeast-3`)
  with the same `player_id` → check whether it returns tokens or re-challenges.
  **Confirms unknown #2** and decides whether an Elastic IP is required.

**Gate:** if both pass, proceed with headless re-login as primary. If the IP test
re-challenges, either attach an Elastic IP (`infra/ec2.tf`) or fall back to
reconcile + loud-fail + laptop-swap as the recovery path (and downgrade re-login).

### Phase 1 — Versioned token store (detect external rotation)

Expose SSM's monotonic version so the session can tell "I wrote this" from
"someone else rotated this." Detection keys on the **refresh** parameter's version.

- [ ] Add optional interface in `collector/internal/feed/tokenstore.go`:

  ```go
  type VersionedTokenStore interface {
      TokenStore
      LoadVersioned() (access, refresh string, version int64, err error)
      SaveVersioned(access, refresh string) (version int64, err error)
  }
  ```

- [ ] `ssmtokenstore.go`: implement both, capturing `GetParameterOutput.Parameter.Version`
  and `PutParameterOutput.Version`. Keep `Load`/`Save` as thin wrappers.
- [ ] `tokenstore.go` (FileTokenStore): add a `"version"` int to `tokenFile`,
  increment on `SaveVersioned` — lets hot-reload be exercised in local dev.

### Phase 2 — Session: reconcile + typed errors + headless re-login (`session.go`)

- [ ] Add `lastStoreVersion int64` + `SetStoreVersion(v int64)` (set once at boot).
- [ ] In `RefreshMain`, prefer `SaveVersioned` and record the returned version.
- [ ] Add `ErrRefreshExpired = errors.New("refresh token expired")`; return it
  (wrapped) from the HTTP 401 branch (`session.go:176`).
- [ ] Add `ReconcileFromStore() (adopted bool, err error)`: no-op unless the store
  is `VersionedTokenStore`. `LoadVersioned`; if `version > lastStoreVersion` **and**
  the stored refresh differs, adopt both tokens under the lock, advance
  `lastStoreVersion`, refresh `userID`, return `true`.
- [ ] Add a `PlayerID` field to `feed.Config` and **send `player_id` in `Login()`'s
  request body** (omit `signature`). Source it from SSM at boot (Phase 4).
- [ ] Add `Relogin() error`: clear the in-memory access token under the lock (so
  `Login` won't early-return at `session.go:124`) and re-run the password login with
  `player_id`. Surface a typed `ErrLoginChallenge` when the response carries
  `new_device`/`unfreeze` (via the existing JSON walkers) instead of a token pair —
  this is the "trust was revoked / player_id not trusted" signal.

### Phase 3 — Wire-up (`cmd/collector/main.go`)

- [ ] **Harden precedence:** when the SSM store is selected, treat it as
  authoritative. Use `LoadVersioned` at boot, call `SetStoreVersion(v)`, and when SSM
  returns a non-empty token use it **even if `SB_ACCESS_TOKEN` env is set** — log a
  `WARN` that the env token is ignored. File store keeps env-wins for dev.
- [ ] **Reconcile before refresh** in `refreshLoop` (`main.go:162`): call
  `ReconcileFromStore()` first; if it adopted, write `ReauthEvent("external-reload")`
  and `continue`.
- [ ] **Reactive 401 path** in `getKeyWithRefresh` (`main.go:249`): `ReconcileFromStore()`
  before the reactive `RefreshMain`.
- [ ] **Refresh-token-death recovery** (both loops) when `RefreshMain` returns
  `ErrRefreshExpired`, in order:
  1. `ReconcileFromStore()` — an external swap may already have fixed it; if adopted, continue.
  2. **`Relogin()`** (headless, primary) — on success persist via the store
     (`SaveVersioned`) and continue. Write a `ReauthEvent("headless-relogin")` record.
  3. Only if `Relogin()` returns `ErrLoginChallenge` (or creds/player_id missing): log
     `CRITICAL`, write a control marker, and exit non-zero so systemd
     `Restart=on-failure` and journald monitoring surface it. Recovery is then the
     laptop swap (gap 2) + a fresh one-time trust establishment.

### Phase 4 — Infra, credentials & operator runbook

- [ ] `infra/ssm.tf`: add three SecureString params under the prefix —
  `/sb-collector/username`, `/sb-collector/password`, `/sb-collector/player_id`
  (placeholders + `lifecycle { ignore_changes = [value] }`, seeded out-of-band like
  the tokens). The IAM wildcard `parameter/sb-collector/*` (`infra/iam.tf`) already
  covers them — no IAM change.
- [ ] `cmd/collector/main.go` / `config.go`: when using the SSM store, load
  `username`/`password`/`player_id` from SSM into `feed.Config` at boot (mirrors the
  token load). Do **not** put the password in the systemd unit.
- [ ] If Phase 0 showed IP sensitivity: add an `aws_eip` + association in
  `infra/ec2.tf` so the instance keeps a stable public IP across stop/start.
- [ ] Document the **one-time trust-establishment** procedure (in `infra/README.md`
  or `docs/`): generate the UUID → seed `/sb-collector/player_id` → run the Phase 0
  probe / a `collector --establish-trust` mode from a machine → complete the
  new-device challenge once → seed `username`/`password`. After this, EC2 self-heals.
- [ ] Document the two **swap runbooks**: (a) stopped instance — `put-parameter`
  refresh then access → next start adopts at boot; (b) live instance — `put-parameter`
  both → adopted within ≤20 min via version reconcile, no restart, no clobber.

---

## 4. Recovery hierarchy (end state)

| Failure | Recovery |
|---------|----------|
| Access token expires | Proactive refresh (20 min) / reactive on 401 — existing |
| Refresh token rotates, process stops | Persisted to SSM each refresh; boot re-reads — existing |
| Operator rotates token from laptop (live) | Adopted ≤20 min via SSM version reconcile, no clobber — Phase 1–3 |
| Operator rotates token (instance stopped) | Adopted at next scheduled boot — existing |
| **Refresh token dies** | reconcile → **headless re-login (trusted player_id)** → loud-fail + laptop swap — Phase 2–3 |
| Stray `SB_ACCESS_TOKEN` env | Ignored; SSM authoritative — Phase 3 |

---

## 5. Trading tokens — recommendation (no code now)

The deployed collector is market-data only; it never calls `TradingLogin` (no PIN,
`ALGO_LIVE` unset on EC2), so trading tokens are correctly out of scope. Keep the
design trading-ready with **zero infra rework**:

- `SSMTokenStore` is prefix-parameterized. A trading store is a second instance with
  prefix `/sb-collector/trading` → `/sb-collector/trading/{access,refresh}`. The IAM
  wildcard already covers it.
- When live-trading is built: add `Session.RefreshTrading()` against
  `carina.stockbit.com/auth/refresh`; attach the trading store; declare two
  placeholder SSM params. Same versioned reconcile applies unchanged.
- Cross-dependency: the trading session derives from the main session + PIN, so a
  main `Relogin()` must be followed by a fresh `TradingLogin()` (needs the PIN).
  Deferred until live-trading exists.

---

## 6. Files to modify

| File | Change |
|------|--------|
| *(Phase 0)* throwaway probe or `sbcap` subcommand | Validate no-signature v6 login + IP-change trust |
| `collector/internal/feed/tokenstore.go` | `VersionedTokenStore` interface; version FileTokenStore |
| `collector/internal/feed/ssmtokenstore.go` | `LoadVersioned`/`SaveVersioned` capturing SSM versions |
| `collector/internal/feed/session.go` | `lastStoreVersion`, `SetStoreVersion`, `ReconcileFromStore`, `ErrRefreshExpired`, `PlayerID` in `Login()`, `Relogin`/`ErrLoginChallenge`, versioned persist |
| `collector/cmd/collector/main.go`, `config.go` | authoritative SSM load, load creds+player_id from SSM, reconcile in loops, refresh-death → relogin escalation |
| `collector/internal/feed/ssmtokenstore_test.go` | extend fake SSM client with a version counter |
| `infra/ssm.tf` (+ maybe `ec2.tf` EIP), `infra/README.md` / docs | username/password/player_id params; trust-establishment + swap runbooks |

## 7. Verification

- [ ] **Phase 0 spike (gating):** no-signature v6 login with a chosen `player_id`
  returns a challenge first, then tokens after one-time verify; and the IP-change
  test returns tokens (or tells us an EIP is needed).
- [ ] **Unit (TDD, table-driven, `go test -race ./...`):**
  - Fake SSM client returns incrementing versions; `SaveVersioned` bumps, `LoadVersioned` reports.
  - `ReconcileFromStore`: unchanged version → no-op; external bump + new refresh → adopts + advances version; non-versioned store → no-op.
  - `RefreshMain` returns `ErrRefreshExpired` on a 401 stub.
  - `Login()` includes `player_id` and omits `signature` in the request body.
  - `Relogin()` surfaces `ErrLoginChallenge` on a `new_device` stub response.
  - **No clobber:** after an external version bump, a refresh cycle adopts (not overwrites).
- [ ] **Local manual (FileTokenStore):** while running, edit the token file with a
  new refresh + bumped version; confirm `adopted externally-rotated token` within one
  refresh tick and no overwrite.
- [ ] **EC2 e2e:** (a) live `put-parameter` swap adopted within ≤20 min, no restart,
  SSM retains the laptop-seeded value; (b) stopped-instance seed adopted at boot
  (`loaded tokens from store`); (c) **refresh-death self-heal** — invalidate/expire
  the refresh token (or simulate a 401) and confirm the collector re-logins headlessly
  and re-seeds SSM without human intervention.

---

Back to [Plans index](./README.md)
