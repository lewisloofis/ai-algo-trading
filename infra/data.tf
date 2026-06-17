# §12.1 / §12.3 — caller identity, region, and latest AL2023 arm64 AMI

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Latest Amazon Linux 2023 arm64 AMI via the SSM public parameter.
# The parameter path is documented by AWS:
#   /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64
# lifecycle { ignore_changes = [ami] } on the EC2 instance ensures tofu never
# replaces the instance on a new AL2023 release — see ec2.tf.
data "aws_ssm_parameter" "al2023_arm64_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}
