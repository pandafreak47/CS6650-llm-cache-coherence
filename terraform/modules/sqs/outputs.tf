output "queue_url" {
  description = "URL of the SQS FIFO queue — pass to workers as SQS_QUEUE_URL"
  value       = aws_sqs_queue.worker.url
}

output "queue_arn" {
  description = "ARN of the SQS FIFO queue — used for IAM policy bindings"
  value       = aws_sqs_queue.worker.arn
}
