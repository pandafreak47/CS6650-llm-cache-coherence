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
  type    = number
  default = 1
}

# How long to keep logs
variable "log_retention_days" {
  type    = number
  default = 7
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
  description = "Anthropic API key passed to the container as an env var. Unused when llm_backend = 'dummy'."
  sensitive   = true
  default     = ""
}

variable "llm_model" {
  type        = string
  description = "Claude model ID the backend will use. Unused when llm_backend = 'dummy'."
  default     = "claude-haiku-4-5-20251001"
}
