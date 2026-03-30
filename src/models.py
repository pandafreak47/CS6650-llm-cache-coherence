from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class KVState(BaseModel):
    """Opaque prefix-cache state passed between LLM calls.

    For Anthropic and DummyLLM this is always empty — they ignore it.
    For llama.cpp this will carry actual KV-cache tensors.
    """

    data: dict[str, Any] = {}


class GitRepo(BaseModel):
    """Everything needed to connect to a specific branch of a GitHub repo."""

    url: str     # e.g. "https://github.com/owner/repo"
    branch: str  # e.g. "test-branch-abc123"


class SQSMessage(BaseModel):
    """Task message consumed from AWS SQS."""

    git_repo: GitRepo
    context_files: list[str]  # file paths relative to repo root
    target_file: str          # file path relative to repo root
    task_prompt: str


class WorkerStatusEnum(str, Enum):
    STANDBY = "standby"
    PROCESSING = "processing"


class HealthResponse(BaseModel):
    status: str = "ok"


class StatusResponse(BaseModel):
    status: WorkerStatusEnum


class MetricsResponse(BaseModel):
    total_input_tokens: int
    total_output_tokens: int
    total_latency_ms: float
    total_requests: int
