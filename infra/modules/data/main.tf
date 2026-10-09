# Durable state for Hearally.
#
# What lives here: paraphrased items, structured metadata, consent records,
# learned patterns and silenced audio. Held-back moments are kept only as short
# summaries for the account holder alone. Raw transcripts and unredacted audio
# pass through only while they are processed, then are deleted. See
# docs/ARCHITECTURE.md sections 3, 6 and 11.

resource "aws_dynamodb_table" "application" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  # hash_key/range_key are marked deprecated in favour of a table-level
  # key_schema block, but that block is not yet accepted by provider 6.56.
  # Revisit when the provider supports it.
  hash_key         = "pk"
  range_key        = "sk"
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = "ttl_epoch"
    enabled        = true
  }

  deletion_protection_enabled = true
}

# Audio jobs. Retention and visibility are far longer than an earlier prototype's text
# queue because transcription runs on a GPU that may be cold or batched: a job
# can legitimately sit for minutes before a worker starts it.
resource "aws_sqs_queue" "processing_dead_letter" {
  name                      = "${var.name}-processing-dead-letter.fifo"
  fifo_queue                = true
  message_retention_seconds = 86400
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "processing" {
  name                        = "${var.name}-processing.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  # Matches the raw audio: a job that outlives its incoming/ object is useless.
  message_retention_seconds  = 86400
  visibility_timeout_seconds = 900
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dead_letter.arn
    maxReceiveCount     = 3
  })
}

# Secrets are created empty here and filled by scripts/set-secrets.ps1. Terraform
# manages the container, never the value, so a rotated key does not show up as
# configuration drift.
resource "aws_secretsmanager_secret" "nebius_api_key" {
  name                    = "${var.name}/nebius-api-key"
  description             = "Nebius Token Factory key, for Nemotron judgement calls"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "tavily_api_key" {
  name                    = "${var.name}/tavily-api-key"
  description             = "Tavily key, for the weekly search for new scam warnings"
  recovery_window_in_days = 7
}

# The transcription worker's own key for the hearally-transcriber service
# account on Nebius AI Cloud: {"public_key_id": ..., "private_key": PEM}.
# Separate from the operator's key, which never leaves the operator's machine.
resource "aws_secretsmanager_secret" "nebius_worker_key" {
  name                    = "${var.name}/nebius-worker-key"
  description             = "Nebius AI Cloud service account key for the transcription worker"
  recovery_window_in_days = 7
}

# CALL-E, for heads-up calls to allies who chose them (backend/src/hearally/calls.py):
# {"api_key": ...}. Created empty; filled by scripts/set-secrets.ps1 -Secret CallE.
resource "aws_secretsmanager_secret" "calle" {
  name                    = "${var.name}/calle"
  description             = "CALL-E API key, for heads-up calls to allies"
  recovery_window_in_days = 7
}

# The pepper for an ally's phone codes (backend/src/hearally/ally_phone.py): only
# a keyed hash of a code is stored. Generated here, so nobody types it; it is in
# the encrypted Terraform state, like every other generated value.
resource "random_password" "phone_pepper" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "phone_pepper" {
  name                    = "${var.name}/phone-pepper"
  description             = "Pepper for hashing allies' phone verification codes"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "phone_pepper" {
  secret_id     = aws_secretsmanager_secret.phone_pepper.id
  secret_string = jsonencode({ pepper = random_password.phone_pepper.result })
}

resource "aws_secretsmanager_secret" "twilio" {
  name                    = "${var.name}/twilio"
  description             = "Twilio account SID and auth token, for heads-up messages"
  recovery_window_in_days = 7
}

# Redacted audio only. The original file is deleted after redaction, and the
# excerpt an ally can play expires on its own so nothing accumulates.
resource "aws_s3_bucket" "media" {
  bucket = "${var.name}-media"
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  # Unredacted audio lands under incoming/. The pipeline's explicit delete, as
  # soon as the redacted copy exists, is the primary control. This rule is only
  # the backstop for when the pipeline fails, and it is weaker than "one day":
  # S3 rounds expiry up to the next midnight UTC and deletes asynchronously, so
  # an object can survive up to about 48 hours, occasionally longer. One day is
  # nevertheless the shortest expiry S3 lifecycle supports.
  rule {
    id     = "expire-raw-audio"
    status = "Enabled"

    filter {
      prefix = "incoming/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Transcripts from the Parakeet job (transcripts/) and each job's manifest and
  # report (jobs/). Raw transcripts are never kept (AGENTS.md): the pipeline
  # deletes a transcript as soon as the judgement has read it, and this rule is
  # the same backstop as for raw audio above, with the same rounding.
  rule {
    id     = "expire-transcripts"
    status = "Enabled"

    filter {
      prefix = "transcripts/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-transcription-jobs"
    status = "Enabled"

    filter {
      prefix = "jobs/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-redacted-clips"
    status = "Enabled"

    filter {
      prefix = "redacted/"
    }

    expiration {
      days = var.clip_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "media" {
  bucket = aws_s3_bucket.media.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.media.arn,
        "${aws_s3_bucket.media.arn}/*",
      ]
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}
