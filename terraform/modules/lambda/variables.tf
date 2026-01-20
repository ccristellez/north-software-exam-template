# Lambda Module Variables

variable "project_name" {
  description = "Name of the project, used for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where Lambda will be deployed"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for Lambda"
  type        = list(string)
}

variable "redis_endpoint" {
  description = "Redis endpoint address"
  type        = string
}

variable "redis_port" {
  description = "Redis port"
  type        = number
  default     = 6379
}

variable "lambda_zip_path" {
  description = "Path to Lambda deployment package"
  type        = string
}

variable "lambda_source_hash" {
  description = "Hash of Lambda source for change detection"
  type        = string
}

variable "memory_size" {
  description = "Lambda memory size in MB"
  type        = number
  default     = 256  # Sufficient for FastAPI
  # Recommendations:
  # 128MB  - Minimum, may be slow
  # 256MB  - Good balance for most use cases
  # 512MB  - For heavy processing
  # 1024MB - For high throughput
}

variable "timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30  # Sufficient for API requests
}

variable "reserved_concurrency" {
  description = "Reserved concurrent executions (null for unreserved)"
  type        = number
  default     = null
}

variable "provisioned_concurrency" {
  description = "Provisioned concurrency to reduce cold starts"
  type        = number
  default     = 0  # Set > 0 for production to avoid cold starts
}

variable "cors_origins" {
  description = "Allowed CORS origins"
  type        = list(string)
  default     = ["*"]  # Restrict in production
}

variable "api_throttle_rate" {
  description = "API Gateway throttle rate (requests/second)"
  type        = number
  default     = 1000
}

variable "api_throttle_burst" {
  description = "API Gateway throttle burst limit"
  type        = number
  default     = 2000
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  type        = string
  default     = null
}

variable "database_url" {
  description = "PostgreSQL connection URL for Supabase (for historical data)"
  type        = string
  default     = ""
  sensitive   = true
}
