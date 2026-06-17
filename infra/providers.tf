# §12.2 — two providers: default ap-southeast-3 + aliased us-east-1 for Cost Explorer
# Both providers receive default_tags so every taggable resource is tagged automatically.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

# Cost Explorer (aws_ce_*) and cost-allocation-tag activation require us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.common_tags
  }
}
