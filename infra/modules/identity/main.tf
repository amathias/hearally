# Accounts for the account holder. Allies never have one.
#
# Only the person with the recorder signs in. Allies reach everything through
# signed, expiring links, which is why there is one pool and one client here
# and no invite or second user type. See docs/ARCHITECTURE.md section 7.

resource "aws_cognito_user_pool" "account_holders" {
  name                = "${var.name}-account-holders"
  deletion_protection = "ACTIVE"

  lifecycle {
    prevent_destroy = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Sent through our own SES domain, not Cognito's shared default sender:
  # mail from no-reply@verificationemail.com landed in Gmail's spam folder on
  # 2026-09-25, and the default is capped at 50 a day. hearally.com has DKIM,
  # SPF on a custom MAIL FROM, and DMARC (infra/modules/messaging), so the
  # From address aligns. Cognito sends with its service-linked role.
  email_configuration {
    email_sending_account = "DEVELOPER"
    source_arn            = var.ses_identity_arn
    from_email_address    = "Hearally <no-reply@${var.domain_name}>"
  }

  # Even the confirmation email carries the disclaimer, as every message does
  # section 5 puts it on every surface, not only the marketing site.
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "Confirm your Hearally account"
    email_message        = "Your Hearally confirmation code is {####}. Hearally is not an emergency service."
  }

  schema {
    attribute_data_type = "String"
    mutable             = true
    name                = "email"
    required            = true

    string_attribute_constraints {
      min_length = 5
      max_length = 254
    }
  }
}

resource "aws_cognito_user_pool_client" "portal" {
  name         = "${var.name}-portal"
  user_pool_id = aws_cognito_user_pool.account_holders.id

  # Public client, authorization-code flow with PKCE. No secret, because the
  # portal is a static export and could not keep one.
  generate_secret                      = false
  prevent_user_existence_errors        = "ENABLED"
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# managed_login_version 2 is Managed Login (2026-10-01). It replaced the
# classic hosted UI (version 1, styled by brand/cognito-hosted-ui.css) because
# only Managed Login shows the terms of use and privacy policy on sign-up. Its
# look is the managed login branding below, kept in Terraform, not the console.
#
# A prefix domain, not auth.<domain>. This account is at the Cognito limit of
# four custom domains, all held by other projects, and a quota increase has a
# lead time this build cannot spend. Every other project here does the same,
# including an earlier prototype. The stylesheet applies identically either way, so the
# only loss is the hostname. Set use_custom_domain once a slot is free.
resource "aws_cognito_user_pool_domain" "auth" {
  domain                = var.use_custom_domain ? "auth.${var.domain_name}" : var.prefix_domain
  user_pool_id          = aws_cognito_user_pool.account_holders.id
  certificate_arn       = var.use_custom_domain ? var.certificate_arn : null
  managed_login_version = 2
}

# The sign-in and sign-up pages' style. Settings are merged over Cognito's
# defaults, so only what differs is listed (brand/README.md for the colours).
resource "aws_cognito_managed_login_branding" "portal" {
  client_id    = aws_cognito_user_pool_client.portal.id
  user_pool_id = aws_cognito_user_pool.account_holders.id

  use_cognito_provided_values = var.branding_settings == null ? true : null
  settings                    = var.branding_settings

  depends_on = [aws_cognito_user_pool_domain.auth]
}

# Terms of use and privacy policy links on the sign-up page: "By signing up,
# you agree to ...". Cognito shows them only when both exist. The AWS provider
# has no resource for them yet, so this one CloudFormation stack, owned by
# Terraform, holds them. The 18+ confirmation is asked in the app instead
# (ARCHITECTURE.md section 9): Cognito offers no custom agreement wording.
resource "aws_cloudformation_stack" "terms" {
  name = "${var.name}-cognito-terms"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "Hearally sign-up terms documents (managed by Terraform)"
    Resources = {
      TermsOfUse = {
        Type = "AWS::Cognito::Terms"
        Properties = {
          UserPoolId  = aws_cognito_user_pool.account_holders.id
          ClientId    = aws_cognito_user_pool_client.portal.id
          TermsName   = "terms-of-use"
          TermsSource = "LINK"
          Enforcement = "NONE"
          Links       = { "cognito:default" = var.terms_url }
        }
      }
      PrivacyPolicy = {
        Type = "AWS::Cognito::Terms"
        Properties = {
          UserPoolId  = aws_cognito_user_pool.account_holders.id
          ClientId    = aws_cognito_user_pool_client.portal.id
          TermsName   = "privacy-policy"
          TermsSource = "LINK"
          Enforcement = "NONE"
          Links       = { "cognito:default" = var.privacy_url }
        }
      }
    }
  })
}

resource "aws_route53_record" "auth" {
  count = var.use_custom_domain ? 1 : 0

  zone_id = var.zone_id
  name    = "auth.${var.domain_name}"
  type    = "A"

  alias {
    name                   = aws_cognito_user_pool_domain.auth.cloudfront_distribution
    zone_id                = aws_cognito_user_pool_domain.auth.cloudfront_distribution_zone_id
    evaluate_target_health = false
  }
}
