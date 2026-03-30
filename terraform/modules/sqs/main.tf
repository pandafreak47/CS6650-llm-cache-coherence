# FIFO task queue for LLM coding agent tasks
resource "aws_sqs_queue" "this" {
  # FIFO queue names must end in ".fifo"
  name                        = "${var.queue_name}.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  visibility_timeout_seconds  = var.visibility_timeout_seconds
  message_retention_seconds   = var.message_retention_seconds
}

# Dead-letter queue — receives tasks that have failed max_receive_count times
resource "aws_sqs_queue" "dlq" {
  name       = "${var.queue_name}-dlq.fifo"
  fifo_queue = true
  content_based_deduplication = true
}

resource "aws_sqs_queue_redrive_policy" "this" {
  queue_url = aws_sqs_queue.this.id
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}
