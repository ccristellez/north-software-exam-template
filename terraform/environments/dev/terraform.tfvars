# Development Environment Configuration
# Optimized for cost (minimal resources)

environment = "dev"
aws_region  = "us-east-1"

# VPC - Minimal setup
vpc_cidr           = "10.0.0.0/16"
az_count           = 2
enable_nat_gateway = false  # Save ~$32/month

# Lambda - Minimal resources
lambda_memory_size             = 256
lambda_timeout                 = 30
lambda_reserved_concurrency    = null  # Unreserved
lambda_provisioned_concurrency = 0     # No provisioned (accept cold starts)

# API Gateway - Generous limits for testing
cors_origins       = ["*"]
api_throttle_rate  = 100
api_throttle_burst = 200

# Redis - Smallest instance
redis_node_type          = "cache.t3.micro"  # ~$12/month
redis_num_cache_clusters = 1                 # No replication

# Alerts - Disabled for dev
enable_alerts = false
alert_email   = ""

# Database - Supabase PostgreSQL (for historical percentile data)
# Get this from Supabase dashboard: Project Settings > Database > Connection string (URI)
# Use the "connection pooler" URL with port 6543 for IPv4 compatibility
# database_url = "postgresql://user:pass@your-project.pooler.supabase.com:6543/postgres"
database_url = ""  # Leave empty if not using historical data
