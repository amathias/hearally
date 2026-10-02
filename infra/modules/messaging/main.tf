# Outbound email for digests, acknowledgements and the "nothing came through"
# notice. SMS is handled by AWS End User Messaging and is added separately.
#
# Domain verification and DKIM have a lead time, and leaving the SES sandbox is
# a support request. Both are started in week 1 for that reason.

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.domain_name

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

resource "aws_route53_record" "dkim" {
  count = 3

  zone_id = var.zone_id
  name    = "${element(aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens, count.index)}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 600
  records = ["${element(aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens, count.index)}.dkim.amazonses.com"]
}

# A custom MAIL FROM domain aligns SPF with the visible sender, which keeps
# digests out of spam folders.
resource "aws_sesv2_email_identity_mail_from_attributes" "domain" {
  email_identity         = aws_sesv2_email_identity.domain.email_identity
  mail_from_domain       = "mail.${var.domain_name}"
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_route53_record" "mail_from_mx" {
  zone_id = var.zone_id
  name    = "mail.${var.domain_name}"
  type    = "MX"
  ttl     = 600
  records = ["10 feedback-smtp.us-east-1.amazonses.com"]
}

resource "aws_route53_record" "mail_from_spf" {
  zone_id = var.zone_id
  name    = "mail.${var.domain_name}"
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com ~all"]
}

# DMARC in monitoring mode. Tighten to quarantine once digests are sending
# cleanly and reports look sane.
resource "aws_route53_record" "dmarc" {
  zone_id = var.zone_id
  name    = "_dmarc.${var.domain_name}"
  type    = "TXT"
  ttl     = 600
  records = ["v=DMARC1; p=none; rua=mailto:dmarc@${var.domain_name}"]
}

resource "aws_sesv2_configuration_set" "primary" {
  configuration_set_name = var.name

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }
}
