# ADR 0006 — Source watchlist and watchlist_id from SSM at boot

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** config, infra, operations

> Backfilled to record a decision already implemented in commits `0c2bff4`
> (feat(collector): read watchlist and watchlist_id from SSM at boot) and
> `fbbc5cb` (feat(infra): SSM-drive watchlist and switch to LQ45 mining set),
> touching `collector/cmd/collector/main.go`,
> `collector/internal/feed/ssmparam.go`, and `infra/ssm.tf`.

## Context

The collector needs two pieces of config at startup: the **symbol list** to
subscribe (e.g. "ADRO,PTBA,ITMG,…") and the **watchlist_id** that labels the
capture set and becomes the middle path segment in every S3 key (see
[ADR-0002](./0002-segment-storage-and-compression.md) — partition layout
`dt=<date>/<watchlistID>/seg-<HHMMSS>.binpb.zst`).

The initial approach placed both values in environment variables baked into
the instance's `user_data` at creation time (`CAPTURE_WATCHLIST`,
`CAPTURE_WATCHLIST_ID`). This had a practical blocker: `aws_instance` carries
`lifecycle { ignore_changes = [user_data] }` to prevent accidental instance
replacement, so `tofu apply` never propagates a watchlist change to the running
box. Changing the mined symbol set required either replacing the instance
(downtime, loss of the current segment) or SSHing in to edit the systemd unit
and restart — both are manual and neither is auditable as infrastructure.

The existing SSM path (`/sb-collector/`) was already in use for rotating auth
tokens, the IAM role already granted `ssm:GetParameter` / `ssm:GetParameters`
on `arn:aws:ssm:…:parameter/sb-collector/*`, and the collector's boot sequence
already resolved `SSM_PARAM_PREFIX` for the token store. Adding watchlist reads
to the same boot-time SSM call was a natural extension.

## Decision

The collector reads `/sb-collector/watchlist` (comma-separated symbols) and
`/sb-collector/watchlist_id` from SSM Parameter Store at boot, using
`feed.SSMParamReader` keyed off the `SSM_PARAM_PREFIX` env var (set to
`/sb-collector` on the EC2 instance by Terraform).

SSM is **authoritative over the env vars**: if `SSM_PARAM_PREFIX` is set and
either parameter is present, the SSM value wins; the env vars
(`CAPTURE_WATCHLIST`, `CAPTURE_WATCHLIST_ID`) survive as local-dev fallbacks
only. Terraform provisions both parameters as plain `String` types in
`infra/ssm.tf` and keeps them in sync with `var.watchlist` and
`var.watchlist_id`:

```
/sb-collector/watchlist    — comma-separated symbol list (e.g. "ADRO,PTBA,ITMG,…")
/sb-collector/watchlist_id — short stable label (e.g. "lq45-mining")
```

To change the mined symbol set an operator runs `tofu apply` with updated
`tfvars`; the new SSM values take effect on the collector's **next boot**
(either the daily EventBridge start per [ADR-0003](./0003-market-hours-instance-scheduling.md)
or a manual restart). No instance replacement, no binary rebuild, no manual
SSH.

## Consequences

**Positive**

- Symbol set and label are changed with a single `tofu apply`; the instance
  need not be replaced or touched manually.
- Config is centralized and auditable in Parameter Store: AWS CloudTrail records
  every `PutParameter` with caller identity and timestamp.
- The watchlist_id in SSM stays in sync with the S3 partition that Terraform
  manages, eliminating a class of label mismatch between capture files and the
  planned S3 layout.
- The env-var fallback keeps the local dev loop unchanged — no SSM call is
  attempted when `SSM_PARAM_PREFIX` is unset.

**Negative / costs**

- The collector now has a **hard runtime dependency on SSM availability** at
  startup. If Parameter Store is unreachable at boot (network misconfiguration,
  IAM regression, regional outage), the process exits with `log.Fatalf` rather
  than falling back silently, which is intentional but means a boot failure in
  a degraded-network scenario instead of running on stale env-var config.
- Changes apply at **next boot only** — there is no live reload. An operator
  who updates SSM mid-session must wait for the next daily start or trigger a
  manual restart to capture under the new watchlist.
- Terraform is authoritative for the SSM content; an out-of-band `aws ssm
  put-parameter` that drifts from `tfvars` will be overwritten on the next
  apply (unlike the token parameters, which carry `ignore_changes = [value]`).

## Related

- [ADR-0002 — segment storage & compression lifecycle](./0002-segment-storage-and-compression.md) —
  `watchlist_id` from SSM becomes the `<watchlistID>` directory in every
  segment path and is recorded in each manifest sidecar.
- [ADR-0003 — weekday market-hours instance scheduling](./0003-market-hours-instance-scheduling.md) —
  the daily EventBridge start is the normal vector by which SSM config changes
  reach the running collector; the "next boot" cadence is already once per
  trading day.
