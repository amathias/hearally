# The API edge: CloudFront and WAF in front of the HTTP API.
#
# WAF cannot attach to an HTTP API directly — only to CloudFront or a REST API —
# so the distribution exists to carry the web ACL as much as to cache.
#
# CloudFront adds a shared secret header to every origin request. The backend
# rejects anything without it, so hitting the execute-api hostname directly
# bypasses the edge and gets a 403 rather than the API.

resource "aws_wafv2_web_acl" "api" {
  name        = "${var.name}-api"
  description = "Common protections for the Hearally API."
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "common-rules"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-common-rules"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "known-bad-inputs"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-known-bad-inputs"
      sampled_requests_enabled   = false
    }
  }

  # Ally links are unauthenticated by design, so the rate limit is the only
  # thing standing between a leaked link and someone enumerating tokens.
  rule {
    name     = "rate-limit"
    priority = 3

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.api_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-limit"
      sampled_requests_enabled   = false
    }
  }

  # Sampled requests stay off throughout: a sampled request would capture ally
  # link tokens and request paths, which AGENTS.md forbids retaining.
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-api"
    sampled_requests_enabled   = false
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${var.name} API"
  price_class     = "PriceClass_100"
  aliases         = ["api.${var.domain_name}"]
  web_acl_id      = aws_wafv2_web_acl.api.arn

  origin {
    domain_name = var.api_origin_host
    origin_id   = "api-gateway"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    custom_header {
      name  = "x-hearally-edge"
      value = random_password.edge_token.result
    }
  }

  default_cache_behavior {
    target_origin_id       = "api-gateway"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # Managed CachingDisabled and AllViewerExceptHostHeader. An API response is
    # per-user; caching one would serve one ally another ally's page.
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "api" {
  zone_id = var.zone_id
  name    = "api.${var.domain_name}"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.api.domain_name
    zone_id                = aws_cloudfront_distribution.api.hosted_zone_id
    evaluate_target_health = false
  }
}

# The shared secret is generated here rather than typed by a human: nobody needs
# to know it, and it must match in two places that Terraform already owns.
resource "random_password" "edge_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "edge_token" {
  name                    = "${var.secret_name_prefix}/edge-token"
  description             = "Shared secret proving a request arrived through CloudFront"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "edge_token" {
  secret_id     = aws_secretsmanager_secret.edge_token.id
  secret_string = jsonencode({ token = random_password.edge_token.result })
}
