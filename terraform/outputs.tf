output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = module.ecs.cluster_name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = module.ecs.service_name
}

output "sqs_queue_url" {
  description = "SQS FIFO queue URL — pass to test_runner.py as SQS_QUEUE_URL"
  value       = module.sqs.queue_url
}

output "ecr_repository_url" {
  description = "ECR repository URL for the worker image"
  value       = module.ecr.repository_url
}
