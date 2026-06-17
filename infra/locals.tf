# §12.2 — cost-audit tag set applied to every resource via provider default_tags
locals {
  common_tags = merge({
    Project     = var.project             # primary cost-allocation dimension
    Component   = "market-data-collector" # isolates this workload within the account
    Environment = var.environment
    CostCenter  = var.cost_center
    ManagedBy   = "opentofu"
    Repo        = "sb-algo-trading-explore"
  }, var.extra_tags)

  # Consistent prefix for resource names: e.g. "sb-algo-trading-prod"
  name_prefix = "${var.project}-${var.environment}"
}
