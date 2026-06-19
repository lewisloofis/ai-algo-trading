# infra — OpenTofu stack for the market-data collector

Provisions all AWS resources described in
[docs/plans/01-market-data-capture.md §12](../docs/plans/01-market-data-capture.md#12-infrastructure-as-code-opentofu).

## What gets created

| Resource | Purpose |
|----------|---------|
| S3 bucket (`sb-marketdata-<accountid>`) | Raw segment storage with 7d→Standard-IA, 30d→Deep Archive lifecycle |
| SSM parameters (`/sb-collector/{access,refresh,watchlist}`) | Token storage + watchlist; access/refresh seeded out-of-band |
| IAM role + instance profile | Least-privilege: `s3:PutObject` on `raw/*`, `ssm:Get/PutParameter` on `/sb-collector/*`, `kms:Decrypt/Encrypt` via `aws/ssm` |
| EC2 `t4g.nano` | AL2023 arm64, gp3 8 GB encrypted, IMDSv2, egress-only SG |
| EventBridge Scheduler ×2 | Start 08:30 WIB / stop 16:25 WIB, weekdays only |
| AWS Budgets | Monthly COST budget scoped to `Project` tag; alerts at 80% actual and 100% forecasted |
| Cost-allocation tag activation ×3 | Makes `Project`, `Component`, `CostCenter` groupable in Cost Explorer |

Estimated cost: **~$1.40/month** (see §8 of the plan for the full breakdown).

## Prerequisites

- [OpenTofu](https://opentofu.org/docs/intro/install/) >= 1.8.0
- AWS credentials with sufficient permissions in `ap-southeast-3`
- A Stockbit account logged in from a trusted device (to obtain initial tokens)

## Deploy

```bash
cd infra

# 1. Copy and edit variable values
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 2. Initialise providers
tofu init

# 3. Review the plan (no changes are applied)
tofu plan

# 4. Apply
tofu apply
```

## Out-of-band token seeding (mandatory after first apply)

Tokens are **never stored in TF state**. After `tofu apply`, seed the real
tokens from your developer laptop (which is a trusted Stockbit device):

```bash
# Seed refresh token FIRST — it is the restart-critical token.
# See docs/plans/01-market-data-capture.md §4.5 for why order matters.
aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/refresh \
  --type SecureString \
  --overwrite \
  --value "<YOUR_REFRESH_TOKEN>"

aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/access \
  --type SecureString \
  --overwrite \
  --value "<YOUR_ACCESS_TOKEN>"
```

The `token_seed_commands` output from `tofu apply` shows these commands with
the correct region pre-filled.

After seeding, the collector will read the tokens at next startup and enter the
refresh loop. As long as `RefreshMain()` runs before the refresh token expires
(~30 days), no further manual login is needed.

## Deploy the collector binary

Either:

1. Set `collector_artifact_s3_uri` in `terraform.tfvars` to an S3 URI of the
   pre-built `GOARCH=arm64 GOOS=linux` binary — cloud-init fetches it on boot.
2. Or push the binary manually after the instance is running:
   ```bash
   # Build on your Mac
   GOARCH=arm64 GOOS=linux go build -o collector ./collector/cmd/collector
   # Copy via SSM Session Manager (no SSH needed)
   aws ssm start-session --target <instance-id>
   # Then from the session: install binary to /opt/collector/collector
   ```

## Operational notes

- **Shell access:** use AWS SSM Session Manager (`aws ssm start-session --target <instance-id>`).
  No inbound SSH port is open.
- **Logs:** `journalctl -u collector -f` on the instance, or CloudWatch Logs
  if you wire up the SSM agent log forwarding.
- **Token rotation:** the collector calls `SSMTokenStore.Save()` after every
  `RefreshMain()`, writing the new tokens back to SSM. Check
  `/sb-collector/refresh` has a recent `LastModifiedDate` after each trading day.
- **IDX holidays:** the collector still starts on IDX holidays; the feed simply
  has no updates. The daily segment will be nearly empty — trivial cost.

## Cost auditing

All resources are tagged with `Project`, `Component`, `Environment`,
`CostCenter`, `ManagedBy`, and `Repo` via provider `default_tags`. After the
cost-allocation tags activate (up to 24h after first apply):

- Cost Explorer → **Group by → Tag: `Project`** to see this stack's spend.
- Filter `Component = market-data-collector` to isolate from other workloads.
- The `sb-algo-trading-prod-monthly` budget sends email alerts at 80% actual
  and 100% forecasted spend.

## State management

State is stored locally in `terraform.tfstate` (gitignored). For a single
operator this is sufficient. To migrate to remote state (S3 backend), add a
`backend "s3"` block to `versions.tf` and run `tofu init -migrate-state`.

---

## Plan 03 — Token rotation & headless re-login

See [docs/plans/03-token-rotation.md](../docs/plans/03-token-rotation.md) for
full context. This section covers the operator procedures.

### Recovery hierarchy

| Failure | Recovery |
|---------|----------|
| Access token expires | Proactive refresh (≤20 min) / reactive on 401 — existing |
| Refresh token rotates, process stopped | Persisted to SSM on each refresh; boot re-reads — existing |
| Operator swaps token from laptop (live) | Adopted ≤20 min via SSM version reconcile, no clobber — Plan 03 |
| Operator swaps token (instance stopped) | Adopted at next scheduled boot — existing |
| **Refresh token dies** | Reconcile → **headless re-login (trusted player_id)** → loud-fail + laptop swap — Plan 03 |
| Stray `SB_ACCESS_TOKEN` env var | Ignored; SSM is authoritative — Plan 03 |

### One-time trust establishment (run once, on first deploy or after trust is revoked)

1. **Generate a stable random UUID** to use as the collector's `player_id`:
   ```bash
   python3 -c "import uuid; print(uuid.uuid4())"
   # e.g. a3f1e9c2-4b87-4d2e-b6f0-1234567890ab
   ```

2. **Seed the `player_id` into SSM** (treat it like a credential — never commit it):
   ```bash
   aws ssm put-parameter \
     --region ap-southeast-3 \
     --name /sb-collector/player_id \
     --type SecureString \
     --overwrite \
     --value "<YOUR_UUID>"
   ```

3. **Perform the first login from a trusted machine** (your laptop, which already
   has an established session) to register the `player_id` with the server.
   Run the Phase 0 probe or `collector --establish-trust` mode:
   ```bash
   # From the collector/ directory:
   go run ./cmd/authprobe  # or whichever probe binary Phase 0 produced
   ```
   The first call will return a `new_device` challenge.

4. **Complete the new-device challenge once** — approve it from your phone (Stockbit
   app notification or OTP). The `player_id` is now trusted server-side.

5. **Verify trust is established** — re-run the probe. It should return a token pair
   directly with no challenge.

6. **Seed the credentials into SSM** (refresh-first convention does not apply here,
   but seed both before starting the collector):
   ```bash
   aws ssm put-parameter \
     --region ap-southeast-3 \
     --name /sb-collector/username \
     --type SecureString \
     --overwrite \
     --value "<YOUR_STOCKBIT_USERNAME>"

   aws ssm put-parameter \
     --region ap-southeast-3 \
     --name /sb-collector/password \
     --type SecureString \
     --overwrite \
     --value "<YOUR_STOCKBIT_PASSWORD>"
   ```

After this one-time setup the EC2 collector self-heals on refresh-token death:
it re-logs in headlessly using the trusted `player_id` and re-seeds the token
params, with no human intervention required.

### Swap runbook (a) — instance is stopped

Use when you need to manually rotate tokens and the instance is not running
(e.g. outside market hours or after an `aws ec2 stop-instances`).

Seed refresh **first** — it is the restart-critical token (see §4.5 of
[docs/plans/01-market-data-capture.md](../docs/plans/01-market-data-capture.md)):

```bash
aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/refresh \
  --type SecureString \
  --overwrite \
  --value "<YOUR_REFRESH_TOKEN>"

aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/access \
  --type SecureString \
  --overwrite \
  --value "<YOUR_ACCESS_TOKEN>"
```

The next scheduled start (EventBridge 08:30 WIB) cold-reads SSM at boot and
adopts both tokens. No manual restart or further action required.

### Swap runbook (b) — instance is live (mid-session)

Use when you need to rotate tokens while the collector is running. The session
reconciles SSM before each 20-minute refresh tick.

Seed refresh **first** to avoid a window where access is new but refresh is stale:

```bash
aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/refresh \
  --type SecureString \
  --overwrite \
  --value "<YOUR_REFRESH_TOKEN>"

aws ssm put-parameter \
  --region ap-southeast-3 \
  --name /sb-collector/access \
  --type SecureString \
  --overwrite \
  --value "<YOUR_ACCESS_TOKEN>"
```

The running collector detects the higher SSM parameter version on its next
reconcile check (≤20 min) and adopts both tokens in-memory. **No restart
needed.** The collector will not overwrite your hand-seeded value because it
only writes back tokens it derived from its own refresh — an externally-bumped
version is the signal to adopt rather than clobber.
