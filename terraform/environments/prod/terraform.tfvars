# Production Environment Configuration
# Optimized for reliability and performance

environment = "prod"
aws_region  = "us-east-1"

# VPC - Full setup with Multi-AZ
vpc_cidr           = "10.0.0.0/16"
az_count           = 3                 # 3 AZs for high availability
enable_nat_gateway = true              # Required for Lambda internet access

# Lambda - Production resources
lambda_memory_size             = 512   # More memory = faster execution
lambda_timeout                 = 30
lambda_reserved_concurrency    = 100   # Prevent runaway scaling
lambda_provisioned_concurrency = 5     # Reduce cold starts for baseline traffic

# API Gateway - Production limits
cors_origins       = ["https://your-frontend-domain.com"]  # Restrict in production
api_throttle_rate  = 10000
api_throttle_burst = 20000

# Redis - Production cluster with replication
redis_node_type          = "cache.r6g.large"  # ~$100/month, good performance
redis_num_cache_clusters = 2                  # Primary + 1 replica for failover

# Alerts - Enabled
enable_alerts = true
alert_email   = "ops-team@your-company.com"  # Update with real email

# Database - Supabase PostgreSQL (for historical percentile data)
# Get this from Supabase dashboard: Project Settings > Database > Connection string (URI)
# Use the "connection pooler" URL with port 6543 for IPv4 compatibility
# IMPORTANT: In production, use TF_VAR_database_url environment variable instead of hardcoding
database_url = ""  # Set via TF_VAR_database_url for security
