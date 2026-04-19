variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# ---------------------------------------------------------------------------
# ECR / ECS basics
# ---------------------------------------------------------------------------

variable "ecr_repository_name" {
  type    = string
  default = "llm-agent-worker"
}

variable "service_name" {
  type    = string
  default = "llm-agent-worker"
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "log_retention_days" {
  type    = number
  default = 7
}

# ---------------------------------------------------------------------------
# Worker scaling
# ---------------------------------------------------------------------------

variable "worker_min_count" {
  type        = number
  default     = 1
  description = "Minimum Fargate tasks running at all times"
}

variable "worker_max_count" {
  type        = number
  default     = 1
  description = "Maximum Fargate tasks (upper bound for experiments)"
}

variable "scale_out_queue_depth" {
  type        = number
  default     = 1
  description = "Queue visible-message count that triggers a scale-out event"
}

# ---------------------------------------------------------------------------
# SQS
# ---------------------------------------------------------------------------

variable "sqs_visibility_timeout" {
  type        = number
  default     = 300
  description = "Seconds a message is hidden after receipt. Set >= max task duration."
}

# ---------------------------------------------------------------------------
# LLM backend
# ---------------------------------------------------------------------------

variable "llm_backend" {
  type        = string
  description = "LLM backend: 'anthropic', 'dummy', or 'llama'"
  default     = "dummy"
  validation {
    condition     = contains(["anthropic", "dummy", "llama"], var.llm_backend)
    error_message = "llm_backend must be 'anthropic', 'dummy', or 'llama'."
  }
}

variable "llama_model_url" {
  type        = string
  description = "HTTPS URL to download the GGUF model if not already at llama_model_path. Defaults to TinyLlama 1.1B Q4_K_M from HuggingFace."
  default     = "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
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

# ---------------------------------------------------------------------------
# Git / GitHub
# ---------------------------------------------------------------------------

variable "github_token" {
  type        = string
  description = "GitHub personal-access token with repo read/write access."
  sensitive   = true
  default     = ""
}

# ---------------------------------------------------------------------------
# Worker behaviour
# ---------------------------------------------------------------------------

variable "build_mode" {
  type        = string
  description = "Prompt-build strategy: 'naive' (full context string) or 'cached' (incremental LLM state, uses accumulate())."
  default     = "naive"
  validation {
    condition     = contains(["naive", "cached"], var.build_mode)
    error_message = "build_mode must be 'naive' or 'cached'."
  }
}

variable "kv_cache_size" {
  type        = number
  description = "Maximum number of entries in the in-memory KV cache per worker."
  default     = 100
}

variable "dummy_llm_latency" {
  type        = number
  description = "Simulated LLM latency in seconds for DummyLLM. 0 = no sleep."
  default     = 0
}

# ---------------------------------------------------------------------------
# Cache backend
# ---------------------------------------------------------------------------

variable "cache_backend" {
  type        = string
  description = "Cache backend: 'memory' (per-pod in-memory) or 'redis' (shared ElastiCache)."
  default     = "memory"
  validation {
    condition     = contains(["memory", "redis"], var.cache_backend)
    error_message = "cache_backend must be 'memory' or 'redis'."
  }
}

variable "redis_node_type" {
  type        = string
  description = "ElastiCache node type for the shared Redis cache."
  default     = "cache.t3.micro"
}
