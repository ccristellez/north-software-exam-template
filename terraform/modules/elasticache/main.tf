# ElastiCache Redis Module - Managed Redis for Congestion Monitor
# Multi-AZ deployment with automatic failover

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Security Group for Redis
# -----------------------------------------------------------------------------

resource "aws_security_group" "redis" {
  name        = "${var.project_name}-redis-sg"
  description = "Security group for ElastiCache Redis"
  vpc_id      = var.vpc_id

  # Allow inbound Redis traffic from Lambda security group
  ingress {
    description     = "Redis from Lambda"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.lambda_security_group_id]
  }

  # Allow all outbound (for replication)
  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-redis-sg"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Subnet Group
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name        = "${var.project_name}-redis-subnet-group"
  description = "Subnet group for ElastiCache Redis"
  subnet_ids  = var.private_subnet_ids

  tags = {
    Name        = "${var.project_name}-redis-subnet-group"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Parameter Group
# -----------------------------------------------------------------------------

resource "aws_elasticache_parameter_group" "main" {
  family      = "redis7"
  name        = "${var.project_name}-redis-params"
  description = "Redis parameter group for Congestion Monitor"

  # Optimize for our use case (high write throughput, TTL-based expiration)
  parameter {
    name  = "maxmemory-policy"
    value = "volatile-ttl"  # Evict keys with TTL when memory is full
  }

  parameter {
    name  = "notify-keyspace-events"
    value = "Ex"  # Enable keyspace notifications for expired keys
  }

  tags = {
    Name        = "${var.project_name}-redis-params"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Replication Group (Redis Cluster)
# -----------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.project_name}-redis"
  description          = "Redis cluster for Congestion Monitor"

  # Engine configuration
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = var.node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.main.name

  # Cluster configuration
  num_cache_clusters         = var.num_cache_clusters
  automatic_failover_enabled = var.num_cache_clusters > 1
  multi_az_enabled           = var.num_cache_clusters > 1

  # Network configuration
  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # Maintenance and backups
  maintenance_window       = "sun:05:00-sun:06:00"
  snapshot_window          = "04:00-05:00"
  snapshot_retention_limit = var.environment == "prod" ? 7 : 1
  auto_minor_version_upgrade = true

  # Encryption (recommended for production)
  at_rest_encryption_enabled = var.environment == "prod"
  transit_encryption_enabled = var.environment == "prod"

  # Notifications
  notification_topic_arn = var.sns_topic_arn

  tags = {
    Name        = "${var.project_name}-redis"
    Environment = var.environment
    Project     = var.project_name
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms for Redis
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${var.project_name}-redis-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75
  alarm_description   = "Redis CPU utilization is high"

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.main.id
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${var.project_name}-redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Redis memory usage is high"

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.main.id
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_connections" {
  alarm_name          = "${var.project_name}-redis-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CurrConnections"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 1000
  alarm_description   = "Redis connection count is high"

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.main.id
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}
