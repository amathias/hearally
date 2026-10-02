# Hearally production stack.
#
# Production is the only environment. There is no environment selector and no
# dev, test or staging resources. See docs/adr/0002-production-only.md.

locals {
  name        = "hearally-prod"
  environment = "production"
  domain_name = "hearally.com"

  common_tags = {
    Application = "Hearally"
    Environment = "Production"
    ManagedBy   = "Terraform"
  }
}

module "dns" {
  source = "../modules/dns"

  domain_name = local.domain_name
}

module "messaging" {
  source = "../modules/messaging"

  name        = local.name
  domain_name = local.domain_name
  zone_id     = module.dns.zone_id
}

module "edge" {
  source = "../modules/edge"

  name               = local.name
  domain_name        = local.domain_name
  zone_id            = module.dns.zone_id
  certificate_arn    = module.dns.certificate_arn
  secret_name_prefix = local.name
  api_origin_host    = module.application.api_origin_host
  sign_in_origin     = module.identity.hosted_ui_domain
}

module "data" {
  source = "../modules/data"

  name = local.name
}

data "aws_caller_identity" "current" {}

module "application" {
  source = "../modules/application"

  name            = local.name
  aws_region      = var.aws_region
  account_id      = data.aws_caller_identity.current.account_id
  domain_name     = local.domain_name
  zone_id         = module.dns.zone_id
  certificate_arn = module.dns.certificate_arn

  backend_source_dir = "${path.root}/../../backend/src"
  worker_layer_dir   = "${path.root}/../../.build/worker-layer"

  table_name           = module.data.table_name
  table_arn            = module.data.table_arn
  processing_queue_url = module.data.processing_queue_url
  processing_queue_arn = module.data.processing_queue_arn
  media_bucket         = module.data.media_bucket
  jwt_issuer           = module.identity.issuer
  jwt_audience         = module.identity.client_id

  handlers = {
    config = {
      handler = "hearally.handlers.config.lambda_handler"
      route   = "GET /v1/config"
      grants  = ["edge_token"]
    }
    uploads = {
      handler = "hearally.handlers.collector.upload_handler"
      route   = "POST /v1/uploads"
      grants  = ["edge_token", "device_auth", "upload_sign"]
    }
    ingest = {
      handler = "hearally.handlers.collector.ingest_handler"
      route   = "POST /v1/ingest"
      grants  = ["edge_token", "device_auth", "upload_verify", "reservations", "enqueue"]
      timeout = 15
    }
    dashboard = {
      handler   = "hearally.handlers.dashboard.lambda_handler"
      route     = "GET /v1/me/{section}"
      grants    = ["edge_token", "account_read"]
      signed_in = true
    }
    # Pairing a computer with an account: see backend/src/hearally/pairing.py.
    pairing = {
      handler   = "hearally.handlers.pairing.create_handler"
      route     = "POST /v1/me/pairings"
      grants    = ["edge_token", "pairing_issue"]
      signed_in = true
    }
    # Accept the signup acknowledgement (ARCHITECTURE.md section 9) without pairing a
    # computer: someone who connects only through Plaud sign-in needs it too.
    acknowledge = {
      handler   = "hearally.handlers.pairing.acknowledge_handler"
      route     = "POST /v1/me/acknowledgement"
      grants    = ["edge_token", "pairing_issue"]
      signed_in = true
    }
    # No sign-in: holding a live, one-use code is the authorisation. The API's
    # WAF rate limit applies.
    redeem = {
      handler = "hearally.handlers.pairing.redeem_handler"
      route   = "POST /v1/pairings/redeem"
      grants  = ["edge_token", "pairing_redeem"]
    }
    revoke = {
      handler   = "hearally.handlers.pairing.revoke_handler"
      route     = "DELETE /v1/me/computers/{device_id}"
      grants    = ["edge_token", "computer_revoke"]
      signed_in = true
    }
    # Plaud sign-in (experimental): reach the account holder's Plaud through
    # Plaud's hosted MCP. A prototype pending Plaud's permission (ARCHITECTURE.md section 2).
    plaud_link = {
      handler   = "hearally.handlers.plaud_link.lambda_handler"
      route     = "POST /v1/me/plaud/{action}"
      grants    = ["edge_token", "plaud_link"]
      signed_in = true
      timeout   = 29
    }
    # Every 15 minutes: new recordings from connected Plaud accounts, into
    # the same intake as the collector (backend/src/hearally/plaud_import.py).
    # Its grants are the collector's, plus the Plaud partition.
    plaud_import = {
      handler  = "hearally.handlers.plaud_import.lambda_handler"
      schedule = "rate(15 minutes)"
      timeout  = 600
      memory   = 1024
      grants   = ["plaud_link", "device_auth", "upload_sign", "upload_verify", "reservations", "enqueue"]
    }
    # Allies (design sections 3, 4 and 7). The layer brings time-zone data
    # for quiet hours and the daily list.
    allies = {
      handler   = "hearally.handlers.allies.manage_handler"
      route     = "POST /v1/me/allies/{action}"
      grants    = ["edge_token", "allies_manage", "ses_send"]
      signed_in = true
      layer     = true
      environment = {
        SES_CONFIGURATION_SET = local.name
      }
    }
    # No sign-in: holding a live ally link is the authorisation. The token
    # comes in the body, never in a URL. The API's WAF rate limit applies.
    ally_link = {
      handler = "hearally.handlers.allies.link_handler"
      route   = "POST /v1/ally/{action}"
      grants  = ["edge_token", "ally_link"]
      layer   = true
    }
    # Every 10 minutes: send what is due to allies, by email.
    deliver = {
      handler  = "hearally.handlers.allies.deliver_handler"
      schedule = "rate(10 minutes)"
      timeout  = 120
      layer    = true
      single   = true
      grants   = ["deliver_outbox", "deliver_account", "link_issue", "ses_send"]
      environment = {
        SES_CONFIGURATION_SET = local.name
      }
    }
    # The transcription worker (backend/src/hearally/worker.py, ARCHITECTURE.md section 3).
    # Dispatch: what is queued becomes one batch and one GPU job on Nebius.
    transcribe_dispatch = {
      handler  = "hearally.handlers.worker.dispatch_handler"
      schedule = "rate(30 minutes)"
      timeout  = 180
      layer    = true
      single   = true
      grants   = ["worker_queue", "worker_state", "worker_media", "nebius_worker_key"]
      environment = {
        NEBIUS_WORKER_KEY_SECRET_ID = "${local.name}/nebius-worker-key"
      }
    }
    # Collect: a finished job's transcripts become judged items, and the
    # unredacted audio and the transcripts are deleted.
    transcribe_collect = {
      handler  = "hearally.handlers.worker.collect_handler"
      schedule = "rate(10 minutes)"
      timeout  = 900
      memory   = 1024
      layer    = true
      single   = true
      grants = [
        "worker_state", "worker_media", "worker_list", "worker_account",
        "nebius_worker_key", "nebius_key", "scam_library", "outbox_put", "account_query",
      ]
      environment = {
        NEBIUS_SECRET_ID            = "${local.name}/nebius-api-key"
        NEBIUS_WORKER_KEY_SECRET_ID = "${local.name}/nebius-worker-key"
      }
    }
    # Weekly: new scam warnings from trusted publishers, found with Tavily and
    # written up by Nemotron (ARCHITECTURE.md section 5). No route; not on the API.
    scam_refresh = {
      handler  = "hearally.handlers.scam_refresh.lambda_handler"
      schedule = "cron(0 14 ? * MON *)"
      timeout  = 300
      grants   = ["nebius_key", "tavily_key", "scam_library"]
      environment = {
        NEBIUS_SECRET_ID = "${local.name}/nebius-api-key"
        TAVILY_SECRET_ID = "${local.name}/tavily-api-key"
      }
    }
  }
}

module "observability" {
  source = "../modules/observability"

  name                   = local.name
  alarm_email            = var.alarm_email
  function_names         = module.application.function_names
  api_id                 = module.application.api_id
  dead_letter_queue_name = module.data.dead_letter_queue_name
  processing_queue_name  = module.data.processing_queue_name
  application_tag        = local.common_tags.Application
  monthly_budget_usd     = "20"
}

module "identity" {
  source = "../modules/identity"

  name            = local.name
  region          = var.aws_region
  domain_name     = local.domain_name
  zone_id         = module.dns.zone_id
  certificate_arn = module.dns.certificate_arn
  callback_urls   = ["https://${local.domain_name}/dashboard/callback/"]
  logout_urls     = ["https://${local.domain_name}/dashboard/signed-out/"]
  stylesheet_path = "${path.root}/../../brand/cognito-hosted-ui.css"
  terms_url         = "https://${local.domain_name}/terms/"
  branding_settings = jsonencode(jsondecode(file("${path.root}/../../brand/cognito-managed-login.json")))
  privacy_url     = "https://${local.domain_name}/privacy/"
  prefix_domain   = "${local.name}-${data.aws_caller_identity.current.account_id}"

  ses_identity_arn = module.messaging.email_identity_arn
}
