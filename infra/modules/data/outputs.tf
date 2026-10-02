output "table_name" {
  description = "Single DynamoDB table."
  value       = aws_dynamodb_table.application.name
}

output "table_arn" {
  value = aws_dynamodb_table.application.arn
}

output "table_stream_arn" {
  description = "Stream feeding the delivery pipeline."
  value       = aws_dynamodb_table.application.stream_arn
}

output "processing_queue_url" {
  value = aws_sqs_queue.processing.url
}

output "processing_queue_arn" {
  value = aws_sqs_queue.processing.arn
}

output "dead_letter_queue_arn" {
  description = "Watched by the observability module for depth."
  value       = aws_sqs_queue.processing_dead_letter.arn
}

output "media_bucket" {
  value = aws_s3_bucket.media.id
}


output "nebius_api_key_secret_arn" {
  value = aws_secretsmanager_secret.nebius_api_key.arn
}

output "tavily_api_key_secret_arn" {
  value = aws_secretsmanager_secret.tavily_api_key.arn
}

output "twilio_secret_arn" {
  value = aws_secretsmanager_secret.twilio.arn
}

output "dead_letter_queue_name" {
  description = "Watched by the observability module for depth."
  value       = aws_sqs_queue.processing_dead_letter.name
}

output "processing_queue_name" {
  description = "Watched by the observability module for message age."
  value       = aws_sqs_queue.processing.name
}
