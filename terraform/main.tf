# Wire together: ecr, logging, sqs, ecs-worker.
# The HTTP API (ecs_api) is commented out — for Phase 0-1 the load test
# script pushes directly to SQS. Uncomment when real agents need an HTTP
# interface (Phase 2+).

module "network" {
  source         = "./modules/network"
  service_name   = var.service_name
  container_port = var.container_port
  # container_port only affects the inbound SG rule — keep it set for when
  # ecs_api is re-enabled. The worker uses this SG for egress only.
}

module "ecr" {
  source          = "./modules/ecr"
  repository_name = var.ecr_repository_name
}

module "logging" {
  source            = "./modules/logging"
  service_name      = var.service_name
  retention_in_days = var.log_retention_days
}

module "sqs" {
  source                     = "./modules/sqs"
  queue_name                 = var.sqs_queue_name
  visibility_timeout_seconds = var.sqs_visibility_timeout
  max_receive_count          = var.sqs_max_receive_count
}

# Reuse an existing IAM role for ECS tasks
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

locals {
  worker_env = [
    { name = "LLM_BACKEND",       value = var.llm_backend },
    { name = "ANTHROPIC_API_KEY", value = var.anthropic_api_key },
    { name = "LLM_MODEL",         value = var.llm_model },
    { name = "QUEUE_BACKEND",     value = var.queue_backend },
    { name = "SQS_QUEUE_URL",     value = module.sqs.queue_url },
    { name = "AWS_REGION",        value = var.aws_region },
  ]
}

# Worker service — dequeues tasks and runs the build → LLM → commit pipeline.
# Scale by changing ecs_worker_count: each container handles one task at a time.
module "ecs_worker" {
  source             = "./modules/ecs"
  service_name       = "${var.service_name}-worker"
  image              = "${module.ecr.repository_url}:latest"
  container_port     = null
  command            = ["python", "worker.py"]
  subnet_ids         = module.network.subnet_ids
  security_group_ids = [module.network.security_group_id]
  execution_role_arn = data.aws_iam_role.lab_role.arn
  task_role_arn      = data.aws_iam_role.lab_role.arn
  log_group_name     = module.logging.log_group_name
  ecs_count          = var.ecs_worker_count
  region             = var.aws_region
  env_vars           = local.worker_env
}

# API service — uncomment when real agents need an HTTP interface (Phase 2+).
# Until then the load test script pushes directly to SQS.
#
# module "ecs_api" {
#   source             = "./modules/ecs"
#   service_name       = "${var.service_name}-api"
#   image              = "${module.ecr.repository_url}:latest"
#   container_port     = var.container_port
#   subnet_ids         = module.network.subnet_ids
#   security_group_ids = [module.network.security_group_id]
#   execution_role_arn = data.aws_iam_role.lab_role.arn
#   task_role_arn      = data.aws_iam_role.lab_role.arn
#   log_group_name     = module.logging.log_group_name
#   ecs_count          = var.ecs_count
#   region             = var.aws_region
#   env_vars           = local.worker_env
# }

# Build and push the image into ECR.
# Both ecs_api and ecs_worker pull the same image — entrypoint is selected
# via the `command` override in each task definition.
resource "docker_image" "app" {
  name = "${module.ecr.repository_url}:latest"
  build {
    context = "../src"
  }
}

resource "docker_registry_image" "app" {
  name = docker_image.app.name
}
