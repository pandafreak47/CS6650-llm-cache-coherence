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

variable "task_role_name" {
  type        = string
  description = "IAM Role name (used to attach inline policies)"
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
  default     = "256"
  description = "vCPU units"
}

variable "memory" {
  type        = string
  default     = "512"
  description = "Memory (MiB)"
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
