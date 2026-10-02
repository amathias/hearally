# The API and its Lambdas.
#
# One zip holds the whole backend package; each handler is selected by its
# module path in `var.handlers`, so adding a route is a map entry rather than a
# new build. Nothing here depends on the VPC: every dependency is reached over
# HTTPS with IAM auth, so there are no private subnets to justify.

data "archive_file" "backend" {
  type        = "zip"
  source_dir  = var.backend_source_dir
  output_path = "${path.module}/backend.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}

# One role per handler, granted only what that handler does. A shared role would
# let the public config endpoint read raw audio and every secret; if any one
# handler were ever compromised, it should get no further than its own job.
#
# Handlers name the grants they need in var.handlers; the grant definitions are
# here, so the policy for any handler is readable in one place.
locals {
  grants = {
    # The edge token is granted by name rather than by ARN. Taking the ARN from
    # the edge module would make application depend on edge, while edge already
    # depends on application for the origin hostname, a dependency cycle.
    # Secret ARNs carry a random suffix, hence the wildcard.
    edge_token = {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name}/edge-token-*"]
    }
    # The Nebius Token Factory key, for Nemotron and the embedding model.
    nebius_key = {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name}/nebius-api-key-*"]
    }
    # The Tavily key, for the weekly scam-library refresh only.
    tavily_key = {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name}/tavily-api-key-*"]
    }
    # Discovered scam scripts (backend/src/hearally/scam_library.py): the one
    # SCAMLIB partition, so the refresh job can touch nothing that belongs to
    # an account.
    scam_library = {
      actions   = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringEquals"
        variable = "dynamodb:LeadingKeys"
        values   = ["SCAMLIB"]
      }
    }
    # A presigned POST is signed with the caller's credentials, so issuing one
    # needs PutObject. Confined to incoming/.
    upload_sign = {
      actions   = ["s3:PutObject"]
      resources = ["arn:aws:s3:::${var.media_bucket}/incoming/*"]
    }
    # HeadObject, including the stored checksum, needs GetObject.
    upload_verify = {
      actions   = ["s3:GetObject"]
      resources = ["arn:aws:s3:::${var.media_bucket}/incoming/*"]
    }
    # Exactly the four calls the reservation repository makes, on reservations
    # only.
    reservations = {
      actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["INGEST#*"]
      }
    }
    # Pairing (backend/src/hearally/pairing.py). Each grant is confined to the
    # partition prefixes its handler touches: pairing codes live under PAIR#,
    # computers, recorders and acknowledgements under USER#.
    #
    # Issue a code: read and record the acknowledgement, store the code.
    pairing_issue = {
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["PAIR#*", "USER#*"]
      }
    }
    # Redeem a code: read it, then one transaction that deletes it and stores
    # the computer. IAM checks each item in a transaction against these.
    pairing_redeem = {
      actions   = ["dynamodb:GetItem", "dynamodb:DeleteItem", "dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["PAIR#*", "USER#*"]
      }
    }
    # Authenticate a paired computer on every upload, and note when it and its
    # Plauds were last seen.
    device_auth = {
      actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
    # Revoke a computer. The handler scopes it to the signed-in person's own.
    computer_revoke = {
      actions   = ["dynamodb:UpdateItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
    # Plaud sign-in (backend/src/hearally/plaud_link.py): a pending sign-in's
    # state, the Plaud tokens and the imported-recording markers under PLAUD#,
    # and the list of connected accounts under PLAUDLINKS. Kept out of USER# on
    # purpose, so the dashboard's account_read grant can never read a Plaud
    # token.
    plaud_link = {
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Query"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["PLAUD#*", "PLAUDLINKS"]
      }
    }
    # The transcription worker (backend/src/hearally/worker.py).
    #
    # Take recordings off the processing queue.
    worker_queue = {
      actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
      resources = [var.processing_queue_arn]
    }
    # Its open batches, under the one WORKER partition.
    worker_state = {
      actions   = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringEquals"
        variable = "dynamodb:LeadingKeys"
        values   = ["WORKER"]
      }
    }
    # Sign the job's links (which needs the matching permission), read the
    # transcripts, check for the silenced copy, and delete what is done with.
    worker_media = {
      actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      resources = [
        "arn:aws:s3:::${var.media_bucket}/incoming/*",
        "arn:aws:s3:::${var.media_bucket}/transcripts/*",
        "arn:aws:s3:::${var.media_bucket}/jobs/*",
        "arn:aws:s3:::${var.media_bucket}/redacted/*",
      ]
    }
    # Without ListBucket, S3 answers 403 rather than 404 for a missing
    # object, and the worker could not tell "no silenced copy" from an error.
    worker_list = {
      actions   = ["s3:ListBucket"]
      resources = ["arn:aws:s3:::${var.media_bucket}"]
    }
    # Items, held-back moments, heads-ups, the daily budget and recording
    # status, under the account holder's own USER# partition.
    worker_account = {
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
    nebius_worker_key = {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name}/nebius-worker-key-*"]
    }
    # Allies (backend/src/hearally/allies.py, design sections 3, 4 and 7).
    #
    # The account holder adds, changes and removes allies, and their links.
    allies_manage = {
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*", "LINKTOKEN#*"]
      }
    }
    # An ally's own link: read it, record their decision or "this was nothing".
    ally_link = {
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*", "LINKTOKEN#*"]
      }
    }
    # The deliver job takes what is due from the OUTBOX partition only.
    deliver_outbox = {
      actions   = ["dynamodb:Query", "dynamodb:DeleteItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["OUTBOX"]
      }
    }
    # It reads allies and items, and logs what was sent, under USER#.
    deliver_account = {
      actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
    # A fresh ally link in each message.
    link_issue = {
      actions   = ["dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["LINKTOKEN#*"]
      }
    }
    # The worker's collect step queues deliveries for what it stores.
    outbox_put = {
      actions   = ["dynamodb:PutItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["OUTBOX"]
      }
    }
    account_query = {
      actions   = ["dynamodb:Query"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
    # Email from hearally.com through the account's configuration set.
    ses_send = {
      actions = ["ses:SendEmail"]
      resources = [
        "arn:aws:ses:${var.aws_region}:${var.account_id}:identity/${var.domain_name}",
        "arn:aws:ses:${var.aws_region}:${var.account_id}:configuration-set/${var.name}",
      ]
    }
    # Send only. Receiving and deleting belong to the worker's own role.
    enqueue = {
      actions   = ["sqs:SendMessage"]
      resources = [var.processing_queue_arn]
    }
    # The dashboard reads the signed-in person's own records. The condition
    # confines it to USER# partitions at the IAM layer, so even a bug in the
    # handler could not read upload reservations, ally-link tokens or anything
    # else stored under another prefix. The handler scopes further, to one
    # USER#<sub> taken from the verified Cognito token.
    account_read = {
      actions   = ["dynamodb:Query", "dynamodb:GetItem"]
      resources = [var.table_arn]
      condition = {
        test     = "ForAllValues:StringLike"
        variable = "dynamodb:LeadingKeys"
        values   = ["USER#*"]
      }
    }
  }
}

resource "aws_iam_role" "handler" {
  for_each = var.handlers

  name = "${var.name}-${each.key}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

data "aws_iam_policy_document" "handler" {
  for_each = var.handlers

  # Its own log group only.
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda[each.key].arn}:*"]
  }

  dynamic "statement" {
    for_each = { for name in each.value.grants : name => local.grants[name] }

    content {
      sid       = replace(title(replace(statement.key, "_", " ")), " ", "")
      effect    = "Allow"
      actions   = statement.value.actions
      resources = statement.value.resources

      dynamic "condition" {
        for_each = lookup(statement.value, "condition", null) == null ? [] : [statement.value.condition]

        content {
          test     = condition.value.test
          variable = condition.value.variable
          values   = condition.value.values
        }
      }
    }
  }
}

resource "aws_iam_role_policy" "handler" {
  for_each = var.handlers

  name   = "${var.name}-${each.key}"
  role   = aws_iam_role.handler[each.key].id
  policy = data.aws_iam_policy_document.handler[each.key].json
}

# A log group per Lambda, retained 30 days. Declared explicitly so retention is
# set from the start rather than defaulting to never expire.
resource "aws_cloudwatch_log_group" "lambda" {
  for_each = var.handlers

  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 30
}

resource "aws_lambda_function" "handler" {
  for_each = var.handlers

  function_name    = "${var.name}-${each.key}"
  role             = aws_iam_role.handler[each.key].arn
  handler          = each.value.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  # The worker's steps must never run twice at once: one at a time each.
  reserved_concurrent_executions = each.value.single ? 1 : -1
  layers                         = each.value.layer ? [aws_lambda_layer_version.worker[0].arn] : []
  filename         = data.archive_file.backend.output_path
  source_code_hash = data.archive_file.backend.output_base64sha256

  environment {
    variables = merge(
      {
        HEARALLY_ENV         = "production"
        HEARALLY_VERSION     = var.service_version
        HEARALLY_TABLE_NAME  = var.table_name
        PROCESSING_QUEUE_URL = var.processing_queue_url
        MEDIA_BUCKET         = var.media_bucket
        NOTIFY_DISPLAY_NAME  = "Hearally"
        NOTIFY_TEMPLATE_ID   = "notify-code-verification-english-001"
        EDGE_TOKEN_SECRET_ID = "${var.name}/edge-token"
        EDGE_REQUIRED        = "true"
      },
      each.value.environment,
    )
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

# The transcription worker's dependencies: the Nebius SDK (and grpc) built
# for arm64, plus the GPU job's own code, injected into each job. Built by
# scripts/build-worker-layer.ps1 before Terraform runs.
data "archive_file" "worker_layer" {
  count       = var.worker_layer_dir == null ? 0 : 1
  type        = "zip"
  source_dir  = var.worker_layer_dir
  output_path = "${path.module}/worker-layer.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}

resource "aws_lambda_layer_version" "worker" {
  count                    = var.worker_layer_dir == null ? 0 : 1
  layer_name               = "${var.name}-worker"
  filename                 = data.archive_file.worker_layer[0].output_path
  source_code_hash         = data.archive_file.worker_layer[0].output_base64sha256
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["arm64"]
}

resource "aws_apigatewayv2_api" "api" {
  name          = var.name
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["https://${var.domain_name}"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
    max_age       = 600
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${var.name}"
  retention_in_days = 30
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn

    # Deliberately no request or response bodies: AGENTS.md forbids logging
    # transcripts, ally details or signed tokens.
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      responseTime   = "$context.responseLatency"
    })
  }

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

# Only handlers with a route sit behind the API. Scheduled jobs have none.
locals {
  routed    = { for key, handler in var.handlers : key => handler if handler.route != null }
  scheduled = { for key, handler in var.handlers : key => handler if handler.schedule != null }
}

resource "aws_apigatewayv2_integration" "handler" {
  for_each = local.routed

  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.handler[each.key].invoke_arn
  payload_format_version = "2.0"
}

# Signed-in routes are checked by API Gateway before any Lambda runs: the
# Cognito ID token's signature, issuer, audience and expiry. The handler then
# trusts only the verified claims.
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.api.id
  name             = "${var.name}-cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = [var.jwt_audience]
  }
}

resource "aws_apigatewayv2_route" "handler" {
  for_each = local.routed

  api_id    = aws_apigatewayv2_api.api.id
  route_key = each.value.route
  target    = "integrations/${aws_apigatewayv2_integration.handler[each.key].id}"

  authorization_type = each.value.signed_in ? "JWT" : "NONE"
  authorizer_id      = each.value.signed_in ? aws_apigatewayv2_authorizer.cognito.id : null
}

resource "aws_lambda_permission" "api" {
  for_each = local.routed

  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handler[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# Scheduled jobs, such as the weekly scam-library refresh. EventBridge invokes
# the function directly; nothing about them is reachable from the API.
resource "aws_cloudwatch_event_rule" "schedule" {
  for_each = local.scheduled

  name                = "${var.name}-${each.key}"
  description         = "Runs ${var.name}-${each.key} on its schedule"
  schedule_expression = each.value.schedule
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each = local.scheduled

  rule = aws_cloudwatch_event_rule.schedule[each.key].name
  arn  = aws_lambda_function.handler[each.key].arn
}

resource "aws_lambda_permission" "schedule" {
  for_each = local.scheduled

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handler[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[each.key].arn
}
