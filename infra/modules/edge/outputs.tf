output "bucket" {
  description = "S3 bucket holding the marketing site."
  value       = aws_s3_bucket.web.id
}

output "distribution_id" {
  description = "CloudFront distribution id, for invalidations after deploy."
  value       = aws_cloudfront_distribution.web.id
}

output "distribution_domain" {
  description = "CloudFront domain name."
  value       = aws_cloudfront_distribution.web.domain_name
}

output "api_distribution_id" {
  value = aws_cloudfront_distribution.api.id
}

output "api_url" {
  description = "Public API base URL, through CloudFront and WAF."
  value       = "https://api.${var.domain_name}"
}

output "edge_token_secret_arn" {
  description = "Secret holding the token the backend must require."
  value       = aws_secretsmanager_secret.edge_token.arn
}
