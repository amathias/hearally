output "zone_id" {
  description = "Route 53 hosted zone id for the apex domain."
  value       = aws_route53_zone.primary.zone_id
}

output "name_servers" {
  description = "Name servers to set at the registrar to delegate the domain."
  value       = aws_route53_zone.primary.name_servers
}

output "certificate_arn" {
  description = "Validated certificate covering the apex and every subdomain."
  value       = aws_acm_certificate_validation.primary.certificate_arn
}
