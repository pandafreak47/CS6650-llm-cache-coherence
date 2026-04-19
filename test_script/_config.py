"""Shared defaults for test_runner.py and stream_logs.py."""
import os

ECS_CLUSTER   = os.environ.get("ECS_CLUSTER",   "llm-agent-worker-cluster")
ECS_SERVICE   = os.environ.get("ECS_SERVICE",   "llm-agent-worker")
WORKER_PORT   = int(os.environ.get("WORKER_PORT", "8080"))
AWS_REGION    = os.environ.get("AWS_REGION",    "us-east-1")
LOG_GROUP     = os.environ.get("LOG_GROUP",     f"/ecs/{ECS_SERVICE}")
CONTAINER     = os.environ.get("CONTAINER",     f"{ECS_SERVICE}-container")
