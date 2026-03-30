output "queue_url" {
  description = "URL of the task queue (set as SQS_QUEUE_URL in container env)"
  value       = aws_sqs_queue.this.url
}

output "queue_arn" {
  description = "ARN of the task queue"
  value       = aws_sqs_queue.this.arn
}

output "dlq_url" {
  description = "URL of the dead-letter queue"
  value       = aws_sqs_queue.dlq.url
}
