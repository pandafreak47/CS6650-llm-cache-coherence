output "worker_cluster_name" {
  description = "ECS cluster name for the worker service"
  value       = module.ecs_worker.cluster_name
}

output "worker_service_name" {
  description = "ECS service name for the workers"
  value       = module.ecs_worker.service_name
}

output "sqs_queue_url" {
  description = "Task queue URL — pass to load_test.py with --queue-url"
  value       = module.sqs.queue_url
}

output "sqs_dlq_url" {
  description = "Dead-letter queue URL — inspect here for permanently failed tasks"
  value       = module.sqs.dlq_url
}

# Uncomment when ecs_api is re-enabled (Phase 2+):
# output "api_cluster_name" {
#   value = module.ecs_api.cluster_name
# }
# output "api_service_name" {
#   value = module.ecs_api.service_name
# }
