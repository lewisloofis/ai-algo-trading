# §7.5 / §12.3 — IAM role + instance profile for the EC2 collector
#
# Permissions:
#   s3:PutObject          → raw/* and universe/* on the marketdata bucket (upload only)
#   s3:GetObject          → artifacts/* on the marketdata bucket (boot-time binary fetch)
#   ssm:GetParameter      → /sb-collector/* (boot-time token + watchlist read)
#   ssm:GetParameters     → /sb-collector/* (batch read variant used by SDK)
#   ssm:PutParameter      → /sb-collector/* (post-refresh token rotation, §4.5)
#   kms:Decrypt/Encrypt   → scoped via kms:ViaService to the aws/ssm key
#   AmazonSSMManagedInstanceCore → Session Manager shell access; no inbound SSH needed

data "aws_iam_policy_document" "collector_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "collector" {
  name               = "${local.name_prefix}-collector"
  assume_role_policy = data.aws_iam_policy_document.collector_assume_role.json
  description        = "Instance role for the market-data collector on EC2."
}

# Inline policy: least-privilege S3 + SSM + KMS access.
data "aws_iam_policy_document" "collector_permissions" {
  # Upload compressed segments to raw/ and daily eligibility snapshots to
  # universe/ — write-only. universe/ is a separate prefix so it stays outside
  # the raw/ lifecycle rules: segments transition to cheaper storage on a clock
  # that suits bulk tick data, while the snapshots are a small, permanently-hot
  # index that every backtest reads (plan 18 A-3).
  statement {
    sid     = "S3PutObject"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.marketdata.arn}/raw/*",
      "${aws_s3_bucket.marketdata.arn}/universe/*"
    ]
  }

  # Fetch the collector binary from artifacts/ on boot — read-only, scoped to
  # the artifacts prefix (kept out of the raw/ lifecycle rules).
  statement {
    sid     = "S3GetArtifact"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.marketdata.arn}/artifacts/*"
    ]
  }

  # Read and write rotating tokens + watchlist from Parameter Store.
  statement {
    sid    = "SSMParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:PutParameter",
    ]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/sb-collector/*"
    ]
  }

  # KMS decrypt/encrypt for SecureString parameters — scoped via kms:ViaService
  # so this never grants general KMS access.
  statement {
    sid    = "KMSViaSSM"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "collector" {
  name   = "${local.name_prefix}-collector-policy"
  role   = aws_iam_role.collector.id
  policy = data.aws_iam_policy_document.collector_permissions.json
}

# Attach AWS-managed policy for SSM Session Manager (shell access without SSH).
resource "aws_iam_role_policy_attachment" "ssm_managed_instance_core" {
  role       = aws_iam_role.collector.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Instance profile wraps the role so EC2 can use it.
resource "aws_iam_instance_profile" "collector" {
  name = "${local.name_prefix}-collector"
  role = aws_iam_role.collector.name
}
