variable "name" {
  description = "Resource name prefix, hearally-prod."
  type        = string
}

variable "aws_region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "domain_name" {
  type = string
}

variable "zone_id" {
  type = string
}

variable "certificate_arn" {
  type = string
}

variable "service_version" {
  description = "Reported by GET /v1/config."
  type        = string
  default     = "0.1.0"
}

variable "backend_source_dir" {
  description = "Directory containing the hearally package."
  type        = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "processing_queue_url" {
  type = string
}

variable "processing_queue_arn" {
  type = string
}

variable "media_bucket" {
  type = string
}

variable "notify_configuration_id" {
  description = "The AWS End User Messaging Notify configuration that texts allies their phone codes (created by backend/src/hearally/configure_notify.py)."
  type        = string
}

variable "handlers" {
  description = "One entry per Lambda. All share a single zip; handler selects the entry point. grants names what the handler may do: edge_token, upload_sign, upload_verify, reservations, enqueue, account_read, pairing_issue, pairing_redeem, device_auth, computer_revoke, nebius_key, tavily_key, scam_library. A handler has a route (behind the API) or a schedule (an EventBridge expression), not both."
  type = map(object({
    handler     = string
    route       = optional(string)
    schedule    = optional(string)
    timeout     = optional(number, 10)
    memory      = optional(number, 512)
    environment = optional(map(string), {})
    grants      = optional(list(string), [])
    signed_in   = optional(bool, false)
    # The worker layer (Nebius SDK and the GPU job's code).
    layer = optional(bool, false)
    # At most one running at a time.
    single = optional(bool, false)
  }))
}

variable "worker_layer_dir" {
  description = "Directory built by scripts/build-worker-layer.ps1, or null for no worker layer."
  type        = string
  default     = null
}

variable "jwt_issuer" {
  description = "Cognito user pool issuer URL, for signed-in routes."
  type        = string
}

variable "jwt_audience" {
  description = "Cognito app client id; the ID token's audience."
  type        = string
}
