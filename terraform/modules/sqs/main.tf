# FIFO queue — message groups give per-target-file ordering (implicit file lock).
resource "aws_sqs_queue" "worker" {
  name                        = "${var.queue_name}.fifo"
  fifo_queue                  = true
  content_based_deduplication = false  # we supply explicit deduplication IDs

  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds

  tags = {
    Project = var.queue_name
  }
}
