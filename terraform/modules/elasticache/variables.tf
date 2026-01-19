# ElastiCache Module Variables

variable "project_name" {
  description = "Name of the project, used for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where Redis will be deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for Redis"
  type        = list(string)
}

variable "lambda_security_group_id" {
  description = "Security group ID of Lambda function (for ingress rules)"
  type        = string
}

variable "node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"  # ~$12/month, good for dev
  # Production recommendations:
  # cache.r6g.large  - ~$100/month, good for moderate load
  # cache.r6g.xlarge - ~$200/month, high throughput
}

variable "num_cache_clusters" {
  description = "Number of cache clusters (nodes) in replication group"
  type        = number
  default     = 1  # Set to 2+ for Multi-AZ with automatic failover
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  type        = string
  default     = null
}
