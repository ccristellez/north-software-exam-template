# Root Module Outputs

output "api_endpoint" {
  description = "API Gateway endpoint URL"
  value       = module.lambda.api_endpoint
}

output "redis_endpoint" {
  description = "Redis endpoint for debugging"
  value       = module.elasticache.redis_endpoint
  sensitive   = true
}

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "lambda_function_name" {
  description = "Lambda function name"
  value       = module.lambda.function_name
}

# Useful deployment info
output "deployment_info" {
  description = "Deployment summary"
  value = {
    environment   = var.environment
    region        = var.aws_region
    api_endpoint  = module.lambda.api_endpoint
    redis_cluster = module.elasticache.replication_group_id
  }
}
