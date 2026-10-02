variable "name" {
  description = "Resource name prefix, hearally-prod."
  type        = string
}

variable "alarm_email" {
  description = "Where alarms and the budget notice go. The subscription must be confirmed by email before anything is delivered."
  type        = string
}

variable "function_names" {
  description = "Lambdas to alarm on."
  type        = list(string)
}

variable "api_id" {
  description = "HTTP API to watch for 5xx."
  type        = string
}

variable "dead_letter_queue_name" {
  type = string
}

variable "application_tag" {
  description = "Value of the Application tag, used to scope the budget to this project alone."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly ceiling in USD. Notifies at 100% of actual spend."
  type        = string
  default     = "20"
}

variable "processing_queue_name" {
  description = "The queue whose oldest message age is watched."
  type        = string
}
