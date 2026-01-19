# ElastiCache Module Outputs

output "redis_endpoint" {
  description = "Redis primary endpoint address"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "redis_port" {
  description = "Redis port"
  value       = aws_elasticache_replication_group.main.port
}

output "redis_connection_string" {
  description = "Redis connection string for application"
  value       = "${aws_elasticache_replication_group.main.primary_endpoint_address}:${aws_elasticache_replication_group.main.port}"
}

output "security_group_id" {
  description = "Security group ID for Redis"
  value       = aws_security_group.redis.id
}

output "replication_group_id" {
  description = "ElastiCache replication group ID"
  value       = aws_elasticache_replication_group.main.id
}
