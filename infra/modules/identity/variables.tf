variable "name" {
  description = "Resource name prefix, hearally-prod."
  type        = string
}

variable "domain_name" {
  type = string
}

variable "zone_id" {
  type = string
}

variable "certificate_arn" {
  description = "Certificate covering auth.<domain>. Cognito custom domains require it in us-east-1."
  type        = string
}

variable "callback_urls" {
  type = list(string)
}

variable "logout_urls" {
  type = list(string)
}

variable "stylesheet_path" {
  description = "Path to the hosted UI stylesheet. brand/cognito-hosted-ui.css stays canonical."
  type        = string
}

variable "region" {
  type = string
}

variable "use_custom_domain" {
  description = "Serve the hosted UI at auth.<domain>. Requires a free Cognito custom-domain slot; this account has four in use."
  type        = bool
  default     = false
}

variable "prefix_domain" {
  description = "Cognito prefix domain used when a custom domain is unavailable. Must be globally unique."
  type        = string
}

variable "ses_identity_arn" {
  description = "The SES domain identity Cognito sends its emails through."
  type        = string
}

variable "terms_url" {
  description = "The terms of use, linked from the sign-up page."
  type        = string
}

variable "privacy_url" {
  description = "The privacy policy, linked from the sign-up page."
  type        = string
}

variable "branding_settings" {
  description = "Managed login style settings (JSON), merged over Cognito's defaults; null for the defaults."
  type        = string
  default     = null
}
