output "user_pool_id" {
  value = aws_cognito_user_pool.account_holders.id
}

output "user_pool_arn" {
  description = "Used by the API Gateway JWT authorizer on portal routes."
  value       = aws_cognito_user_pool.account_holders.arn
}

output "client_id" {
  value = aws_cognito_user_pool_client.portal.id
}

output "issuer" {
  description = "JWT issuer for the authorizer."
  value       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.account_holders.id}"
}

output "hosted_ui_domain" {
  description = "Where account holders sign in."
  value       = var.use_custom_domain ? "https://auth.${var.domain_name}" : "https://${aws_cognito_user_pool_domain.auth.domain}.auth.${var.region}.amazoncognito.com"
}
