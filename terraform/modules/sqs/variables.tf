variable "queue_name" {
  type        = string
  description = "Base name for the queue (without .fifo suffix)"
}

variable "visibility_timeout_seconds" {
  type        = number
  default     = 300
  description = "How long a message is hidden after a worker dequeues it. Should exceed the worst-case LLM call duration."
}

variable "message_retention_seconds" {
  type        = number
  default     = 86400 # 24 hours
  description = "How long unprocessed messages are retained before SQS drops them."
}

variable "max_receive_count" {
  type        = number
  default     = 3
  description = "Number of delivery attempts before a message is moved to the DLQ."
}
