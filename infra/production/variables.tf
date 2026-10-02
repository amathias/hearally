variable "aws_region" {
  description = "The single region this production stack deploys to."
  type        = string
  default     = "us-east-1"
}

variable "alarm_email" {
  description = "Where CloudWatch alarms and the budget notice are delivered. Set in terraform.tfvars, which is not committed."
  type        = string
}
