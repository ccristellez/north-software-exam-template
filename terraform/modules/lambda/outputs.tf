# Lambda Module Outputs

output "function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.api.function_name
}

output "function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.api.arn
}

output "api_endpoint" {
  description = "API Gateway endpoint URL"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "api_id" {
  description = "API Gateway ID"
  value       = aws_apigatewayv2_api.main.id
}

output "security_group_id" {
  description = "Lambda security group ID"
  value       = aws_security_group.lambda.id
}

output "lambda_role_arn" {
  description = "Lambda IAM role ARN"
  value       = aws_iam_role.lambda.arn
}
