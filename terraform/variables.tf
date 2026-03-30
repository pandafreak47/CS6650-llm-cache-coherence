# Region to deploy into
variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# ECR & ECS settings
variable "ecr_repository_name" {
  type    = string
  default = "llm-backend"
}

variable "service_name" {
  type    = string
  default = "llm-backend"
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "ecs_count" {
  type        = number
  default     = 1
  description = "Number of API service containers"
}

variable "ecs_worker_count" {
  type        = number
  default     = 1
  description = "Number of worker containers. Each processes one task at a time. Scale up to increase throughput."
}

# How long to keep logs
variable "log_retention_days" {
  type    = number
  default = 7
}

# SQS
variable "sqs_queue_name" {
  type        = string
  default     = "llm-tasks"
  description = "Base name for the SQS FIFO queue (without .fifo suffix)"
}

variable "sqs_visibility_timeout" {
  type        = number
  default     = 300
  description = "Seconds a dequeued message is hidden from other workers. Should exceed max LLM call duration."
}

variable "sqs_max_receive_count" {
  type        = number
  default     = 3
  description = "Delivery attempts before a task is sent to the dead-letter queue."
}

# LLM backend configuration
variable "llm_backend" {
  type        = string
  description = "LLM backend to use: 'anthropic' or 'dummy'"
  default     = "anthropic"
  validation {
    condition     = contains(["anthropic", "dummy"], var.llm_backend)
    error_message = "llm_backend must be 'anthropic' or 'dummy'."
    # Add 'llama' here when llama.cpp support is implemented.
  }
}

variable "anthropic_api_key" {
  type        = string
  description = "Anthropic API key. Unused when llm_backend = 'dummy'."
  sensitive   = true
  default     = ""
}

variable "llm_model" {
  type        = string
  description = "Claude model ID. Unused when llm_backend = 'dummy'."
  default     = "claude-haiku-4-5-20251001"
}

# Queue backend (for local dev override — always 'sqs' in production)
variable "queue_backend" {
  type    = string
  default = "sqs"
}
