# §12.4 — all input variables with documented defaults

# --- Region & identity -----------------------------------------------------------

variable "aws_region" {
  description = "AWS region for all resources. Jakarta for lowest latency to Stockbit servers."
  type        = string
  default     = "ap-southeast-3"
}

# --- Cost-audit tags -------------------------------------------------------------

variable "project" {
  description = "Primary cost-allocation dimension; used in resource names and tags."
  type        = string
  default     = "sb-algo-trading"
}

variable "environment" {
  description = "Deployment environment (prod/staging/dev)."
  type        = string
  default     = "prod"
}

variable "cost_center" {
  description = "Cost-center tag value for billing attribution."
  type        = string
  default     = "market-data"
}

variable "extra_tags" {
  description = "Additional tags merged into common_tags on every resource."
  type        = map(string)
  default     = {}
}

# --- Watchlist -------------------------------------------------------------------

variable "watchlist" {
  description = "Symbols to subscribe. Written to /sb-collector/watchlist in SSM."
  type        = list(string)
  # Mining names (coal + nickel/metals) plus BUMI/DEWA (heavily-traded retail
  # names, not stable LQ45 members). DEWA adds tick-band-2 (Rp200–499) coverage
  # that the prior all-mining set lacked. Verify against the current official
  # LQ45 list before each apply — membership rotates every Feb & Aug.
  default = ["ITMG", "INDY", "ANTM", "MDKA", "BUMI", "DEWA"]
}

variable "watchlist_id" {
  description = "Short stable identifier for this symbol set; used as S3 key prefix segment."
  type        = string
  default     = "mining-sample"
}

# --- Compute ---------------------------------------------------------------------

variable "instance_type" {
  description = "EC2 instance type. t4g.nano is sufficient for a ≤50-symbol Go collector."
  type        = string
  default     = "t4g.nano"
}

variable "root_volume_size" {
  description = "Root EBS gp3 volume size in GiB."
  type        = number
  default     = 8
}

# --- Market-hours scheduling (§7.3) ---------------------------------------------

variable "market_open_schedule" {
  description = "EventBridge Scheduler cron for instance start. Default = 08:30 WIB (Asia/Jakarta) — 15 min before IDX pre-opening (08:45) for boot + auth + WS connect margin."
  type        = string
  default     = "cron(30 8 ? * MON-FRI *)"
}

variable "market_close_schedule" {
  description = "EventBridge Scheduler cron for instance stop. Default = 16:25 WIB (Asia/Jakarta) — ~10 min after IDX post-trading close (16:15) so the final segment flushes/compresses/uploads on SIGTERM."
  type        = string
  default     = "cron(25 16 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  description = "IANA timezone for EventBridge Scheduler expressions."
  type        = string
  default     = "Asia/Jakarta"
}

# --- Cost governance (§8, §12.2) ------------------------------------------------

variable "monthly_budget_limit_usd" {
  description = "Monthly budget cap in USD. Alerts fire at 80% actual and 100% forecasted."
  type        = number
  default     = 5
}

variable "budget_alert_emails" {
  description = "Email addresses for budget alert notifications. Leave empty to skip."
  type        = list(string)
  default     = []
}

variable "activate_cost_allocation_tags" {
  description = "Whether to activate Project/Component/CostCenter as Cost Explorer dimensions."
  type        = bool
  default     = true
}

# --- Collector binary ------------------------------------------------------------

variable "collector_artifact_s3_uri" {
  description = "Optional S3 URI (s3://bucket/key) for the arm64 collector binary. Cloud-init fetches it if set."
  type        = string
  default     = ""
}
