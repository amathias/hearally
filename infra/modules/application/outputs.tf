output "api_endpoint" {
  description = "execute-api endpoint. CloudFront uses it as an origin; it is not the public URL."
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "api_origin_host" {
  description = "Hostname only, for the CloudFront origin."
  value       = replace(aws_apigatewayv2_api.api.api_endpoint, "https://", "")
}

output "api_id" {
  value = aws_apigatewayv2_api.api.id
}

output "function_names" {
  description = "Watched by the observability module."
  value       = [for f in aws_lambda_function.handler : f.function_name]
}
