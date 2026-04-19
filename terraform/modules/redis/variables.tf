variable "service_name" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "vpc_id" {
  type = string
}

variable "ecs_security_group_id" {
  type        = string
  description = "Security group of ECS tasks — granted inbound access to Redis port."
}

variable "node_type" {
  type    = string
  default = "cache.t3.micro"
}
