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
# These three params support refresh-token-death self-healing (docs/plans/03-token-rotation.md).
# When the refresh token dies the collector re-logs-in headlessly using username +
# password + a pre-trusted player_id UUID, then re-seeds the token params above.
#
# Like the token params, all three use lifecycle { ignore_changes = [value] }:
#   1. TF writes the placeholder on first apply only.
#   2. Real values are seeded out-of-band (see infra/README.md §Plan 03).
#   3. Credentials NEVER enter TF state.
#
# IAM coverage: the existing SSMParameters statement in iam.tf already grants
# ssm:GetParameter/ssm:GetParameters/ssm:PutParameter on
# arn:aws:ssm:<region>:<account>:parameter/sb-collector/*
# which covers /sb-collector/username, /sb-collector/password, and
# /sb-collector/player_id with no IAM change required.

resource "aws_ssm_parameter" "username" {
  name        = "/sb-collector/username"
  description = "Stockbit account username for headless re-login (Plan 03). Seeded out-of-band; never set via TF after initial apply."
  type        = "SecureString"
  value       = "PLACEHOLDER_SEED_OUT_OF_BAND"

  lifecycle {
    # Prevent TF from reverting the credential seeded out-of-band.
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "password" {
  name        = "/sb-collector/password"
  description = "Stockbit account password for headless re-login (Plan 03). Seeded out-of-band; never set via TF after initial apply."
  type        = "SecureString"
  value       = "PLACEHOLDER_SEED_OUT_OF_BAND"

  lifecycle {
    # Prevent TF from reverting the credential seeded out-of-band.
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "player_id" {
  name        = "/sb-collector/player_id"
  description = "Stable random UUID used as Stockbit device identity (player_id). Established once interactively; trusted thereafter. Seeded out-of-band; never set via TF after initial apply."
  type        = "SecureString"
  value       = "PLACEHOLDER_SEED_OUT_OF_BAND"

  lifecycle {
    # Prevent TF from reverting the player_id seeded out-of-band.
    ignore_changes = [value]
  }
}
