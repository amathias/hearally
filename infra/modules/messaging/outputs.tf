output "email_identity" {
  description = "SES domain identity."
  value       = aws_sesv2_email_identity.domain.email_identity
}

output "configuration_set_name" {
  description = "Configuration set applied to every outbound message."
  value       = aws_sesv2_configuration_set.primary.configuration_set_name
}

output "email_identity_arn" {
  value = aws_sesv2_email_identity.domain.arn
}
