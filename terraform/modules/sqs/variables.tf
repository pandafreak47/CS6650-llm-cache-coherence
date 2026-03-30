variable "queue_name" {
  type        = string
  description = "Base name for the SQS FIFO queue ('.fifo' is appended automatically)"
}

variable "visibility_timeout_seconds" {
  type        = number
  default     = 300
  description = "How long a message is hidden after a worker receives it. Should be >= max task duration."
}

variable "message_retention_seconds" {
  type        = number
  default     = 86400  # 24 hours
  description = "How long unprocessed messages are kept in the queue."
}
