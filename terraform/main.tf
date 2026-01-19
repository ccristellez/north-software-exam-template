# Congestion Monitor - Main Terraform Configuration
#
# This is the root module that composes all infrastructure components.
# Use environment-specific tfvars files for deployment.
#
# Usage:
#   terraform init
#   terraform plan -var-file=environments/dev/terraform.tfvars
#   terraform apply -var-file=environments/dev/terraform.tfvars

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment for remote state (recommended for production)
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "congestion-monitor/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Local Variables
# -----------------------------------------------------------------------------

locals {
  # Lambda deployment package path (built by CI/CD)
  lambda_zip_path = "${path.module}/../dist/lambda.zip"

  # Compute hash only if file exists (for planning without artifact)
  lambda_source_hash = fileexists(local.lambda_zip_path) ? filebase64sha256(local.lambda_zip_path) : "placeholder"
}

# -----------------------------------------------------------------------------
# SNS Topic for Alerts (optional)
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  count = var.enable_alerts ? 1 : 0
  name  = "${var.project_name}-alerts"

  tags = {
    Name = "${var.project_name}-alerts"
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.enable_alerts && var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# -----------------------------------------------------------------------------
# VPC Module
# -----------------------------------------------------------------------------

module "vpc" {
  source = "./modules/vpc"

  project_name       = var.project_name
  environment        = var.environment
  aws_region         = var.aws_region
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  enable_nat_gateway = var.enable_nat_gateway
}

# -----------------------------------------------------------------------------
# Lambda Module (creates security group needed by ElastiCache)
# -----------------------------------------------------------------------------

module "lambda" {
  source = "./modules/lambda"

  project_name       = var.project_name
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  vpc_cidr           = module.vpc.vpc_cidr
  private_subnet_ids = module.vpc.private_subnet_ids

  # Redis connection (from ElastiCache module)
  redis_endpoint = module.elasticache.redis_endpoint
  redis_port     = module.elasticache.redis_port

  # Lambda configuration
  lambda_zip_path     = local.lambda_zip_path
  lambda_source_hash  = local.lambda_source_hash
  memory_size         = var.lambda_memory_size
  timeout             = var.lambda_timeout
  reserved_concurrency    = var.lambda_reserved_concurrency
  provisioned_concurrency = var.lambda_provisioned_concurrency

  # API Gateway configuration
  cors_origins       = var.cors_origins
  api_throttle_rate  = var.api_throttle_rate
  api_throttle_burst = var.api_throttle_burst

  # Alerts
  sns_topic_arn = var.enable_alerts ? aws_sns_topic.alerts[0].arn : null
}

# -----------------------------------------------------------------------------
# ElastiCache Module
# -----------------------------------------------------------------------------

module "elasticache" {
  source = "./modules/elasticache"

  project_name             = var.project_name
  environment              = var.environment
  vpc_id                   = module.vpc.vpc_id
  private_subnet_ids       = module.vpc.private_subnet_ids
  lambda_security_group_id = module.lambda.security_group_id

  # Redis configuration
  node_type          = var.redis_node_type
  num_cache_clusters = var.redis_num_cache_clusters

  # Alerts
  sns_topic_arn = var.enable_alerts ? aws_sns_topic.alerts[0].arn : null
}
