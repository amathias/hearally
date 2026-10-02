output "name_servers" {
  description = "Set these four name servers at the registrar to delegate hearally.com to Route 53."
  value       = module.dns.name_servers
}

output "zone_id" {
  description = "Route 53 hosted zone id."
  value       = module.dns.zone_id
}

output "certificate_arn" {
  description = "Validated certificate covering the apex and every subdomain."
  value       = module.dns.certificate_arn
}

output "ses_email_identity" {
  description = "SES domain identity pending verification."
  value       = module.messaging.email_identity
}

output "web_bucket" {
  description = "Bucket holding the marketing site."
  value       = module.edge.bucket
}

output "web_distribution_id" {
  description = "CloudFront distribution id for invalidations."
  value       = module.edge.distribution_id
}

output "api_url" {
  description = "Public API base URL."
  value       = module.edge.api_url
}

output "hosted_ui_domain" {
  description = "Branded Cognito sign-in."
  value       = module.identity.hosted_ui_domain
}

output "user_pool_id" {
  value = module.identity.user_pool_id
}
