variable "name" {
  description = "Resource name prefix, hearally-prod."
  type        = string
}

variable "domain_name" {
  description = "Apex domain served by this distribution."
  type        = string
}

variable "zone_id" {
  description = "Route 53 hosted zone for the alias records."
  type        = string
}

variable "certificate_arn" {
  description = "Validated ACM certificate covering the apex and www."
  type        = string
}

variable "api_origin_host" {
  description = "execute-api hostname used as the CloudFront origin."
  type        = string
}

variable "api_rate_limit" {
  description = "Requests per five minutes per IP before WAF blocks. Ally links are unauthenticated, so this is the enumeration guard."
  type        = number
  default     = 2000
}

variable "secret_name_prefix" {
  description = "Prefix for the edge-token secret, hearally-prod."
  type        = string
}

variable "media_origins" {
  description = "Origins the dashboard may play audio from: the silenced copies' signed links."
  type        = list(string)
  default     = []
}

variable "sign_in_origin" {
  description = "Origin of the Cognito hosted UI, which the dashboard calls to exchange sign-in codes for tokens."
  type        = string
}
