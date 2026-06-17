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
