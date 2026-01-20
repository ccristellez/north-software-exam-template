# Root Module Variables
# Override these in environment-specific tfvars files

# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "congestion-monitor"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy to"
  type        = string
  default     = "us-east-1"
}

# -----------------------------------------------------------------------------
# VPC Configuration
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones"
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = "Enable NAT Gateway (costs ~$32/month)"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Lambda Configuration
# -----------------------------------------------------------------------------

variable "lambda_memory_size" {
  description = "Lambda memory size in MB"
  type        = number
  default     = 256
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrent executions"
  type        = number
  default     = null
}

variable "lambda_provisioned_concurrency" {
  description = "Provisioned concurrency for reduced cold starts"
  type        = number
  default     = 0
}

# -----------------------------------------------------------------------------
# API Gateway Configuration
# -----------------------------------------------------------------------------

variable "cors_origins" {
  description = "Allowed CORS origins"
  type        = list(string)
  default     = ["*"]
}

variable "api_throttle_rate" {
  description = "API throttle rate (requests/second)"
  type        = number
  default     = 1000
}

variable "api_throttle_burst" {
  description = "API throttle burst limit"
  type        = number
  default     = 2000
}

# -----------------------------------------------------------------------------
# Redis Configuration
# -----------------------------------------------------------------------------

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_num_cache_clusters" {
  description = "Number of cache clusters for replication"
  type        = number
  default     = 1
}

# -----------------------------------------------------------------------------
# Alerts
# -----------------------------------------------------------------------------

variable "enable_alerts" {
  description = "Enable CloudWatch alarms and SNS notifications"
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Email address for alert notifications"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Database Configuration (Supabase)
# -----------------------------------------------------------------------------

variable "database_url" {
  description = "PostgreSQL connection URL for Supabase (for historical percentile data)"
  type        = string
  default     = ""
  sensitive   = true
}
