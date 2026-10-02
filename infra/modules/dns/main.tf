# Public DNS and the single production certificate for Hearally.
#
# The zone is created before the domain is delegated. Certificate validation
# cannot succeed until the registrar points at these name servers, so the
# nameserver switch is the first manual step of the build.

resource "aws_route53_zone" "primary" {
  name    = var.domain_name
  comment = "Production public DNS for Hearally"

  lifecycle {
    prevent_destroy = true
  }
}

# One certificate with subject alternative names, rather than five certificates.
# CloudFront requires us-east-1, which is the only region this stack uses.
resource "aws_acm_certificate" "primary" {
  domain_name               = var.domain_name
  subject_alternative_names = [for name in var.subdomains : "${name}.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.primary.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }

  zone_id         = aws_route53_zone.primary.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "primary" {
  certificate_arn         = aws_acm_certificate.primary.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate_validation : record.fqdn]

  timeouts {
    create = "60m"
  }
}
