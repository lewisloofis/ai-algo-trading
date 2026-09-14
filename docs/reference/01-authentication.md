# Stockbit API — Authentication

This document describes how to authenticate to Stockbit programmatically and how
to **keep the session alive** (token refresh) so a long-running streaming process
does not get logged out.

There are two independent sessions (see [README](../README.md)):

1. **Main account session** — username + password → access/refresh tokens.
2. **Trading (securities) session** — main session + trading PIN → separate
   trading access/refresh tokens.

---

## 1. Main account login

### Endpoint

```
POST https://exodus.stockbit.com/login/v6/username
Content-Type: application/json
```

(Development host: `https://exodus.lab.stockbit.com`)

> The mobile clients use `/login/v6/username`. The web client uses
> `/login/v3/username/browser` and additionally sends `verification_token` and
> `recaptcha_version` (because the browser is bot-checked). For a headless
> client, `v6` is the simplest and is the reference here.
> Source: `sb-ios/Features/.../Login/DataLayer/Endpoint/LoginEndpoint.swift`,
> `sb-android/data/remote/.../api/AuthApi.kt`,
> `sb-web/src/lib/api/auth.js:29`.

### Request body

```json
{
  "user": "your_username_or_email",
  "password": "your_password",
  "player_id": "optional_fcm_push_token"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `user` | yes | Username or email |
| `password` | yes | Account password |
| `player_id` | no | FCM/push token; safe to omit or send a stable UUID |
| `signature` | no | Android may send a request signature; not required for the call to succeed |

### Required headers

The clients send these on every request. Replicate them so the backend treats
you like a known client:

```
X-Platform: android            # or "iOS"
X-AppVersion: 7.x.x            # any plausible current app version string
X-DeviceType: <device-model>  # e.g. "Pixel 7" / "iPhone15,2"
Accept-Language: id            # "id" or "en"
Accept: application/json
Content-Type: application/json
```

Source: `sb-ios/Networking/.../ApiService.swift:45-52`,
`sb-android` `AuthInterceptor` headers.

### Response

```json
{
  "data": {
    "user": {
      "id": 123456,
      "username": "myuser",
      "email": "me@example.com",
      "fullname": "My Name",
      "is_verified": true,
      "is_phone_verified": true
    },
    "token_data": {
      "access":  { "token": "<JWT access token>",  "expired_at": "2026-06-16T10:30:00Z" },
      "refresh": { "token": "<JWT refresh token>", "expired_at": "2026-07-16T10:30:00Z" }
    }
  }
}
```

> The exact nesting differs slightly per client version — some return
> `data.access`/`data.refresh` directly, the v6 mobile DTO wraps it under
> `data.login.token_data`. Parse defensively: locate `access.token`,
> `access.expired_at`, `refresh.token`, `refresh.expired_at`.
> Source: `sb-web/src/typings/mfa.ts:42-49`,
> `sb-android/data/dto/.../auth/LoginV6DTO.kt`.

**Save all four values:** `access.token`, `access.expired_at`,
`refresh.token`, `refresh.expired_at`. You need the access token for API calls
and the WebSocket; you need the refresh token + expiry to keep the session alive.

### Possible "extra step" responses

The v6 flow can return a challenge instead of tokens when logging in from a new
device or a frozen account:

- **New device** → `data.new_device.multi_factor.{login_token, verification_token}`.
  Stockbit pushes a prompt to a trusted device; verify via
  `POST /login/v6/new-device/verify`.
- **Frozen account** → `data.unfreeze.{login_token, verification_token}`; verify
  via `POST /login/v6/unfreeze/verify`.

For an automation account, **log in once interactively from the machine that will
run the bot** so the device becomes trusted, then subsequent logins return tokens
directly. Source: `sb-android/data/remote/.../api/LoginApi.kt:24-32`.

> **Canonical contract** (now in-repo): the v6 login surface — `LoginByUsername`
> (`/login/v6/username`), `LoginBySocial` (`/login/v6/social`), `LoginByBiometric`
> (`/login/v6/biometric`), `VerifyNewDeviceLogin` (`/login/v6/new-device/verify`),
> `VerifyUnfreeze` (`/login/v6/unfreeze/verify`) — is defined in
> `sb-protos/proto/social/auth/v6/service.proto`. Use it to confirm exact
> request/response field names.

### Social login (alternative)

If you use Google/Facebook sign-in instead of a password:

```
POST /login/v6/social        # mobile
POST /login/v4/google        # web: body { google_user_id, google_id_token, player_id }
POST /login/v4/facebook      # web: body { fb_userid, fb_token, player_id }
```

---

## 2. Authenticated requests

Once you have an access token, send it as a Bearer token:

```
Authorization: Bearer <access.token>
X-Platform: android
X-AppVersion: 7.x.x
X-DeviceType: <device-model>
Accept-Language: id
```

Source: `sb-ios/Networking/.../ApiService.swift:45-52`,
`sb-android` `AuthInterceptorHelper`.

---

## 3. Keeping the session alive — token refresh ⭐

This is the key to a stream that does not break mid-session. The **access token
expires** (its `expired_at` is typically minutes–hours out). When it expires,
both REST calls and the WebSocket auth will start returning `401`. You must
refresh it **before** it expires.

### Refresh endpoint

```
POST https://exodus.stockbit.com/login/refresh
Authorization: Bearer <refresh.token>
```

- The **refresh token** (not the access token) goes in the `Authorization`
  header. The mobile clients send the raw refresh token; sending it as
  `Bearer <refresh.token>` also works (the web client does this). If one form
  returns 401, try the other.
- No request body is required.

Source: `sb-ios/Networking/.../BaseAPIRouter.swift:14-43` &
`RefreshTokenManager.swift:70-74`, `sb-android/.../api/AuthApi.kt:72-75`,
`sb-web/src/config/refreshToken/exodus.ts:36-60`.

### Refresh response

```json
{
  "data": {
    "access":  { "token": "<new access token>",  "expired_at": "..." },
    "refresh": { "token": "<new refresh token>", "expired_at": "..." }
  }
}
```

**Important:** the refresh call returns a *new refresh token too*. Replace both
the access and refresh tokens you have stored — the old refresh token may be
rotated/invalidated.

### Refresh strategy for a long-running bot

The official clients implement this carefully (see Android's
`AuthInterceptorHelper.kt:76-146`); replicate the same logic:

1. **Proactive refresh.** Track `access.expired_at`. Refresh when you are within
   a safety margin of expiry (e.g. 60 seconds before, or at ~80% of the token's
   lifetime). This avoids ever hitting a 401 mid-stream.
2. **Reactive refresh on 401.** If any request (or the WebSocket auth) returns
   `401`, refresh immediately and retry the request once.
3. **Single-flight / mutex.** Only one refresh in flight at a time. Android
   guards this with a `Mutex` so concurrent 401s don't fire multiple refreshes
   and race. After a refresh completes, queued requests reuse the new token.
4. **Atomic snapshot.** Read access + refresh tokens together so you never pair
   a new access token with a stale refresh token.
5. **Retry budget.** Up to ~3 attempts on transient network errors. If the
   refresh itself returns `401`, the **refresh token has expired** → the session
   is dead and you must do a full login again.

```python
# Pseudocode — proactive + reactive refresh
def get_valid_access_token(state):
    if now() >= state.access_expired_at - timedelta(seconds=60):
        with state.refresh_lock:                      # single-flight
            if now() >= state.access_expired_at - timedelta(seconds=60):
                refresh_session(state)                # POST /login/refresh
    return state.access_token

def refresh_session(state):
    r = post("https://exodus.stockbit.com/login/refresh",
             headers={"Authorization": f"Bearer {state.refresh_token}", **base_headers})
    if r.status_code == 401:
        raise SessionExpired("refresh token expired — full login required")
    d = r.json()["data"]
    state.access_token       = d["access"]["token"]
    state.access_expired_at  = parse(d["access"]["expired_at"])
    state.refresh_token      = d["refresh"]["token"]
    state.refresh_expired_at = parse(d["refresh"]["expired_at"])
```

> See [02-websocket-streaming.md](./02-websocket-streaming.md#5-keeping-the-stream-alive)
> for how this ties into re-authenticating the WebSocket after a refresh.

---

## 4. Trading (securities) account login

Needed **only** to place orders or stream your portfolio/orders — not for
market-data streaming. It is a second, PIN-gated session layered on the main
session.

### Step 1 — get a trading login token

```
GET https://exodus.stockbit.com/sekuritas/auth/token
Authorization: Bearer <main access token>
```

Response — **wrapped in the standard `data` envelope**, confirmed live
(HTTP 200); an earlier draft of this doc showed it bare:

```json
{ "message": "OK", "data": { "token": "<login_token>", "target": "2" } }
```

`target` selects which trading backend to use:
- `"1"` = **LEGACY**
- `"2"` = **NEW_CORE** (current)

Source: `sb-web/src/lib/api/securities.ts:55-78`. **Canonical contract:**
`GetLoginToken` → `GET /sekuritas/auth/token` in
`sb-protos/proto/securities/non_transactional/auth/public/v1/auth.proto`.

### Step 2 — verify the trading PIN

**NEW_CORE (`target == "2"`):**

```
POST https://carina.stockbit.com/auth/v2/login
Content-Type: application/json

{ "login_token": "<login_token>", "pin": "123456" }
```

**LEGACY (`target == "1"`):**

```
POST <trading-legacy-host>/auth/pin
Content-Type: application/x-www-form-urlencoded

loginToken=<login_token>&pin=123456
```

Response (both):

```json
{
  "access_token":  "<trading access token>",
  "refresh_token": "<trading refresh token>",
  "account":      { "type": "ACCOUNT_TYPE_EQUITY", "number": "<account no>" },
  "main_account": { "number": "<main account no>" }
}
```

`account.type` ∈ `ACCOUNT_TYPE_EQUITY | ACCOUNT_TYPE_SYARIAH | ACCOUNT_TYPE_BOND
| ACCOUNT_TYPE_DAY_TRADING`.

Source: `sb-web/src/global/Securities/types/api/auth.ts:13-34`,
`sb-ios/.../ValidateTradingPINWorker.swift`.

### Trading token storage & use

- Store the trading access/refresh tokens **separately** from the main session
  tokens (the apps keep `AccessTokenStockbitSecurities` distinct).
- Send `Authorization: Bearer <trading access token>` on Carina/trading
  endpoints.

### Trading token refresh

```
POST https://carina.stockbit.com/auth/refresh
Authorization: Bearer <trading refresh token>
Content-Type: application/x-www-form-urlencoded

refresh_token=<trading refresh token>
```

Same proactive + reactive strategy as the main session. Source:
`sb-web/src/lib/api/authSecurities.ts:14-24`,
`sb-ios/.../SBSekuritasBaseAPIRouter.swift:14-46`.

> **Canonical contract / newer surface.** The clients call `POST /auth/v2/login`
> (above), but the trading auth core is also defined as a proto service —
> `sb-protos/proto/securities/trm/auth_core/public/v1/authentication.proto` (and
> `/v2`): `Login` (`/auth/login`), `LoginToken` (`/auth/login/token`), `Refresh`
> (`/auth/refresh`), `Logout` (`/auth/logout`), plus PIN ops in
> `.../auth_core/public/v1/pin.proto` (`/auth/pin/validate`, `/auth/pin/change`,
> `/auth/pin/reset`). If `/auth/v2/login` behaves unexpectedly, check these for
> the current field/PIN-validation shape.

---

## 5. Token storage notes (how the apps do it)

- **iOS:** Keychain (`KeychainSession`), `accessibleAfterFirstUnlockThisDeviceOnly`.
- **Android:** EncryptedSharedPreferences / DataStore behind `AuthRepository`.
- **Web:** Zustand store persisted to a cookie (`sb-credential-store`, 30-day).

For a bot, store tokens in an OS keychain or an encrypted file — **never commit
them**. Treat the refresh token like a password.

---

## Quick reference

| Action | Method & URL | Auth header |
|--------|--------------|-------------|
| Main login | `POST exodus.stockbit.com/login/v6/username` | — |
| Refresh main | `POST exodus.stockbit.com/login/refresh` | `Bearer <refresh>` |
| Authed REST | any | `Bearer <access>` |
| WS key | `GET exodus.stockbit.com/auth/websocket/key` | `Bearer <access>` |
| Trading token | `GET exodus.stockbit.com/sekuritas/auth/token` | `Bearer <access>` |
| Trading PIN | `POST carina.stockbit.com/auth/v2/login` | — (uses `login_token`) |
| Refresh trading | `POST carina.stockbit.com/auth/refresh` | `Bearer <trading refresh>` |

Next: [WebSocket streaming →](./02-websocket-streaming.md)
