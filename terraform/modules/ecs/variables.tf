variable "service_name" {
  type        = string
  description = "Base name for ECS resources"
}

variable "image" {
  type        = string
  description = "ECR image URI (with tag)"
}

variable "container_port" {
  type        = number
  description = "Port the worker's HTTP server listens on"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnets for Fargate tasks"
}

variable "security_group_ids" {
  type        = list(string)
  description = "Security groups for Fargate tasks"
}

variable "execution_role_arn" {
  type        = string
  description = "ECS Task Execution Role ARN"
}

variable "task_role_arn" {
  type        = string
  description = "IAM Role ARN for app-level permissions (SQS, etc.)"
}

variable "log_group_name" {
  type        = string
  description = "CloudWatch log group name"
}

variable "region" {
  type        = string
  description = "AWS region (for awslogs driver)"
}

variable "cpu" {
  type        = string
  default     = "512"
  description = "Fargate vCPU units (256=0.25, 512=0.5, 1024=1, 2048=2, 4096=4). For llama.cpp switch to EC2-backed ECS with a GPU instance — Fargate has no GPU support."
}

variable "memory" {
  type        = string
  default     = "1024"
  description = "Memory (MiB). Valid Fargate pairs: 512/1024/2048/3072/4096 for 0.5 vCPU; up to 30720 for 4 vCPU. For llama.cpp a 7B 4-bit model alone needs ~5000 MiB."
}

variable "env_vars" {
  type = list(object({
    name  = string
    value = string
  }))
  default     = []
  description = "Environment variables injected into the container"
}

# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------

variable "worker_min_count" {
  type        = number
  default     = 1
  description = "Minimum number of running worker tasks"
}

variable "worker_max_count" {
  type        = number
  default     = 5
  description = "Maximum number of running worker tasks"
}

variable "scale_out_queue_depth" {
  type        = number
  default     = 1
  description = "SQS visible-message count that triggers a scale-out event"
}

# ---------------------------------------------------------------------------
# SQS (needed for autoscaling alarms and IAM)
# ---------------------------------------------------------------------------

variable "sqs_queue_name" {
  type        = string
  description = "Name of the SQS FIFO queue (used in CloudWatch alarm dimension)"
}

variable "sqs_queue_arn" {
  type        = string
  description = "ARN of the SQS FIFO queue (used for IAM policy)"
}
