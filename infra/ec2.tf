# §7.2 / §9.5 / §10 / §12.3 — EC2 t4g.nano instance
#
# Key choices:
#   - AL2023 arm64 AMI resolved at plan time via SSM public parameter (data.tf)
#   - lifecycle { ignore_changes = [ami] } → no surprise replacement on AL2023 updates
#   - IMDSv2 required (http_tokens = "required") — hardened instance metadata
#   - Egress-only security group — no inbound from the internet; shell access via SSM
#   - user_data from user_data.sh.tftpl installs and starts the collector systemd unit
#   - KillSignal=SIGTERM + TimeoutStopSec=120 in the unit → clean segment flush on stop

# Egress-only security group — collector only needs outbound to Stockbit WS + AWS APIs.
resource "aws_security_group" "collector" {
  name        = "${local.name_prefix}-collector"
  description = "Egress-only SG for market-data collector. No inbound; shell access via SSM."

  # All outbound traffic allowed — Stockbit WS, S3, SSM, and metadata service.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }
}

resource "aws_instance" "collector" {
  ami           = data.aws_ssm_parameter.al2023_arm64_ami.value
  instance_type = var.instance_type

  iam_instance_profile   = aws_iam_instance_profile.collector.name
  vpc_security_group_ids = [aws_security_group.collector.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size
    encrypted   = true
  }

  # IMDSv2 required — prevents SSRF-based metadata exfiltration.
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    watchlist_id              = var.watchlist_id
    watchlist                 = join(",", var.watchlist)
    ssm_prefix                = "/sb-collector"
    s3_bucket                 = aws_s3_bucket.marketdata.bucket
    region                    = data.aws_region.current.name
    collector_artifact_s3_uri = var.collector_artifact_s3_uri
  })

  tags = {
    Name = "${local.name_prefix}-collector"
  }

  lifecycle {
    # Never replace the instance on a new AL2023 AMI release — upgrade deliberately.
    ignore_changes = [ami, user_data]
  }
}

# §4 (Plan 03) — Elastic IP for stable public IP across stop/start
#
# Uncomment only if Plan 03 Phase 0 shows the changing public IP re-challenges
# device trust (i.e. the IP-change probe returns a new_device challenge instead
# of tokens directly). If Phase 0 confirms trust survives IP changes, leave
# this commented out — the EIP incurs cost (~$3.60/month when unattached).
#
# resource "aws_eip" "collector" {
#   domain = "vpc"
#
#   tags = {
#     Name = "${local.name_prefix}-collector"
#   }
# }
#
# resource "aws_eip_association" "collector" {
#   instance_id   = aws_instance.collector.id
#   allocation_id = aws_eip.collector.id
# }
