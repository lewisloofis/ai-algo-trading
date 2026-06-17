# §7.3 / §12.3 — EventBridge Scheduler rules for weekday market-hours start/stop
#
# Uses EventBridge Scheduler universal targets (not EventBridge Rules) — universal
# targets support ec2:StartInstances / ec2:StopInstances directly and have per-
# invocation pricing ($1/million; ~44/month ≈ free at this scale).
#
# Schedules are expressed in Asia/Jakarta timezone so the cron reads as local
# market time (08:30 and 16:25 WIB) rather than requiring UTC conversion.
# Start is 15 min before IDX pre-opening (08:45); stop is ~10 min after the
# post-trading close (16:15) to leave room for the SIGTERM segment flush.

# --- IAM role for the scheduler to call EC2 ------------------------------------

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json
  description        = "Allows EventBridge Scheduler to start/stop the collector EC2 instance."
}

data "aws_iam_policy_document" "scheduler_ec2" {
  statement {
    sid    = "EC2StartStop"
    effect = "Allow"
    actions = [
      "ec2:StartInstances",
      "ec2:StopInstances",
    ]
    resources = [aws_instance.collector.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_ec2" {
  name   = "${local.name_prefix}-scheduler-ec2"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_ec2.json
}

# --- Start schedule (08:30 WIB, MON-FRI) ----------------------------------------

resource "aws_scheduler_schedule" "market_open" {
  name        = "${local.name_prefix}-market-open"
  description = "Start collector EC2 instance 15 min before IDX pre-opening (08:30 WIB, weekdays)."

  schedule_expression          = var.market_open_schedule
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      InstanceIds = [aws_instance.collector.id]
    })
  }
}

# --- Stop schedule (16:25 WIB, MON-FRI) -----------------------------------------

resource "aws_scheduler_schedule" "market_close" {
  name        = "${local.name_prefix}-market-close"
  description = "Stop collector EC2 instance ~10 min after IDX post-trading close (16:25 WIB, weekdays)."

  schedule_expression          = var.market_close_schedule
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      InstanceIds = [aws_instance.collector.id]
    })
  }
}
