# §4.5 / §12.3 — SSM Parameter Store parameters under /sb-collector/
#
# IMPORTANT — token parameters use lifecycle { ignore_changes = [value] }.
# This means:
#   1. TF applies placeholder values on first apply only.
#   2. The collector's SSMTokenStore.Save() rotates the real tokens in-place
#      after each refresh; TF never reverts them.
#   3. Real tokens NEVER enter TF state — they are seeded out-of-band (§12.5).
#
# Seed order after apply (refresh-first, per §4.5):
#   aws ssm put-parameter --name /sb-collector/refresh --type SecureString --overwrite --value "<REFRESH>"
#   aws ssm put-parameter --name /sb-collector/access  --type SecureString --overwrite --value "<ACCESS>"

resource "aws_ssm_parameter" "refresh_token" {
  name        = "/sb-collector/refresh"
  description = "Stockbit rotating refresh token. Seeded out-of-band; never set via TF after initial apply."
  type        = "SecureString"
  value       = "PLACEHOLDER_SEED_OUT_OF_BAND"

  lifecycle {
    # Prevent TF from reverting the token that the collector rotates each day.
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "access_token" {
  name        = "/sb-collector/access"
  description = "Stockbit rotating access token. Seeded out-of-band; never set via TF after initial apply."
  type        = "SecureString"
  value       = "PLACEHOLDER_SEED_OUT_OF_BAND"

  lifecycle {
    ignore_changes = [value]
  }
}

# Watchlist is a plain String — not a secret; the collector reads it at boot,
# authoritative over the CAPTURE_WATCHLIST env var (which remains a local-dev fallback).
resource "aws_ssm_parameter" "watchlist" {
  name        = "/sb-collector/watchlist"
  description = "Comma-separated symbol watchlist read by the collector at startup (authoritative over CAPTURE_WATCHLIST env)."
  type        = "String"
  value       = join(",", var.watchlist)
}

# Short stable id for the symbol set; used as the S3 key prefix segment.
# Read by the collector at startup (authoritative over the CAPTURE_WATCHLIST_ID env).
resource "aws_ssm_parameter" "watchlist_id" {
  name        = "/sb-collector/watchlist_id"
  description = "Short stable id for the symbol set; S3 key prefix segment. Read by the collector at startup."
  type        = "String"
  value       = var.watchlist_id
}

# §4 (Plan 03) — Credentials + stable device identity for headless re-login
#
# /sb-collector/{username,password,player_id} are DELIBERATELY NOT MANAGED BY OPENTOFU.
# They are created and rotated entirely out-of-band; see infra/README.md
# "Plan 03 headless re-login setup" for the seeding procedure.
#
# They *were* declared here as managed resources in 742d7e2 (2026-06-19 18:42), about
# eight minutes AFTER the real values had already been hand-seeded (SSM shows username
# v1 18:32:34, player_id v2 18:32:22, password v1 18:34:33). Because they were never in
# state, every `tofu plan` from that day until 2026-09-05 read
# "3 to add ... value = PLACEHOLDER_SEED_OUT_OF_BAND" — i.e. the next apply would have
# silently overwritten the live Stockbit credentials and the server-trusted player_id
# with placeholder strings.
#
# `lifecycle { ignore_changes = [value] }` was on all three and did NOT protect them:
# ignore_changes applies to UPDATES of an already-managed resource, never to CREATE.
# That is what made the trap invisible — the guard read as protection.
#
# Failure mode had it fired: nothing breaks immediately, because the collector only
# touches these when the refresh token dies. The next refresh-token death would fall
# through to headless re-login, authenticate with a placeholder username/password, fail,
# and take capture down mid-week — the exact failure plan 03 exists to prevent. Losing
# player_id is worse than losing a password: it is a device identity trusted server-side
# once, so re-establishing it needs an interactive login with OTP (README step 1-5).
#
# Do NOT re-add them as managed resources. `tofu import` is also the wrong fix: it would
# pull the real secret values into terraform.tfstate, breaking the invariant stated at the
# top of this file that credentials never enter TF state. Out-of-band is the correct and
# intended lifecycle for all three.
#
# IAM is unaffected — the SSMParameters statement in iam.tf grants the collector
# ssm:GetParameter/GetParameters/PutParameter on
# arn:aws:ssm:<region>:<account>:parameter/sb-collector/* , which already covers these
# three by prefix and never depended on them being TF-managed.
