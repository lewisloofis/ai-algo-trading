# §12.2 — Activate Project, Component, and CostCenter as Cost Explorer dimensions
#
# Cost-allocation tag activation requires the us-east-1 provider (Cost Explorer
# is a global service whose API endpoint lives in us-east-1).
#
# count is gated on var.activate_cost_allocation_tags so this can be skipped in
# accounts that already have the tags activated, or during initial bootstrapping.
#
# Note: newly activated tags only attribute costs incurred AFTER activation and
# can take up to ~24h to appear as grouping dimensions in Cost Explorer.

resource "aws_ce_cost_allocation_tag" "project" {
  count    = var.activate_cost_allocation_tags ? 1 : 0
  provider = aws.us_east_1

  tag_key = "Project"
  status  = "Active"
}

resource "aws_ce_cost_allocation_tag" "component" {
  count    = var.activate_cost_allocation_tags ? 1 : 0
  provider = aws.us_east_1

  tag_key = "Component"
  status  = "Active"
}

resource "aws_ce_cost_allocation_tag" "cost_center" {
  count    = var.activate_cost_allocation_tags ? 1 : 0
  provider = aws.us_east_1

  tag_key = "CostCenter"
  status  = "Active"
}
