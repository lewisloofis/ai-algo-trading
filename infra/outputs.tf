# §12.5 — Outputs: bucket name, instance id, region, and out-of-band token-seed commands
#
# The two aws ssm put-parameter commands are shown as outputs so the operator
# knows exactly what to run after apply. Refresh token is seeded FIRST because
# it is the restart-critical token (§4.5).

output "s3_bucket_name" {
  description = "Name of the S3 bucket holding raw market-data segments."
  value       = aws_s3_bucket.marketdata.bucket
}

output "collector_instance_id" {
  description = "EC2 instance ID for the market-data collector."
  value       = aws_instance.collector.id
}

output "aws_region" {
  description = "AWS region all resources are deployed in."
  value       = data.aws_region.current.name
}

# Out-of-band token seed commands (§12.5).
# Run these once from a trusted developer machine AFTER tofu apply.
# Refresh token first — it is the restart-critical token (§4.5).
output "token_seed_commands" {
  description = "Run these once from a trusted machine to seed the real Stockbit tokens into SSM. Never commit actual token values."
  value       = <<-EOT
    # 1. Seed refresh token first (restart-critical — see docs/plans/01-market-data-capture.md §4.5)
    aws ssm put-parameter \
      --region ${data.aws_region.current.name} \
      --name /sb-collector/refresh \
      --type SecureString \
      --overwrite \
      --value "<YOUR_REFRESH_TOKEN>"

    # 2. Then seed access token
    aws ssm put-parameter \
      --region ${data.aws_region.current.name} \
      --name /sb-collector/access \
      --type SecureString \
      --overwrite \
      --value "<YOUR_ACCESS_TOKEN>"
  EOT
}
