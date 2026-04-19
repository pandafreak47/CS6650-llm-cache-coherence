module "network" {
  source         = "./modules/network"
  service_name   = var.service_name
  container_port = var.container_port
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
  queue_name                 = var.service_name
  visibility_timeout_seconds = var.sqs_visibility_timeout
}

module "redis" {
  source                = "./modules/redis"
  service_name          = var.service_name
  subnet_ids            = module.network.subnet_ids
  vpc_id                = module.network.vpc_id
  ecs_security_group_id = module.network.security_group_id
  node_type             = var.redis_node_type
  count                 = var.cache_backend == "redis" ? 1 : 0
}

# Reuse the pre-existing lab IAM role for ECS tasks.
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

module "ecs" {
  source             = "./modules/ecs"
  service_name       = var.service_name
  image              = "${module.ecr.repository_url}:latest"
  container_port     = var.container_port
  subnet_ids         = module.network.subnet_ids
  security_group_ids = [module.network.security_group_id]
  execution_role_arn = data.aws_iam_role.lab_role.arn
  task_role_arn      = data.aws_iam_role.lab_role.arn
  log_group_name     = module.logging.log_group_name
  region             = var.aws_region

  worker_min_count      = var.worker_min_count
  worker_max_count      = var.worker_max_count
  scale_out_queue_depth = var.scale_out_queue_depth

  sqs_queue_name = "${var.service_name}.fifo"
  sqs_queue_arn  = module.sqs.queue_arn

  env_vars = [
    { name = "LLM_BACKEND",       value = var.llm_backend },
    { name = "LLM_MODEL",         value = var.llm_model },
    { name = "ANTHROPIC_API_KEY", value = var.anthropic_api_key },
    { name = "GITHUB_TOKEN",      value = var.github_token },
    { name = "SQS_QUEUE_URL",     value = module.sqs.queue_url },
    { name = "AWS_REGION",        value = var.aws_region },
    { name = "BUILD_MODE",        value = var.build_mode },
    { name = "KV_CACHE_SIZE",     value = tostring(var.kv_cache_size) },
    { name = "CACHE_BACKEND",     value = var.cache_backend },
    { name = "REDIS_URL",         value = var.cache_backend == "redis" ? module.redis[0].redis_url : "" },
  ]
}

# ---------------------------------------------------------------------------
# Build and push the worker image into ECR on every terraform apply.
# For CI/CD pipelines, move this block to a separate build step.
# ---------------------------------------------------------------------------

resource "docker_image" "app" {
  name = "${module.ecr.repository_url}:latest"

  build {
    context    = abspath("${path.module}/../src/")
    dockerfile = "Dockerfile"
  }
}

resource "docker_registry_image" "app" {
  name          = docker_image.app.name
  keep_remotely = true

  triggers = {
    src_hash = sha256(join("", [for f in fileset("${path.module}/../src", "**") : filesha256("${path.module}/../src/${f}")]))
  }
}
