output "redis_url" {
  description = "Redis connection URL for workers (redis://<host>:6379)"
  value       = "redis://${aws_elasticache_cluster.this.cache_nodes[0].address}:6379"
}

output "redis_endpoint" {
  description = "Raw ElastiCache endpoint hostname"
  value       = aws_elasticache_cluster.this.cache_nodes[0].address
}
