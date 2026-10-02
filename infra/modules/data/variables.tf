variable "name" {
  description = "Resource name prefix, hearally-prod. Also the DynamoDB table name."
  type        = string
}

variable "clip_retention_days" {
  description = "How long a redacted evidence clip survives. Retained excerpts are capped at seven days."
  type        = number
  default     = 7
}
