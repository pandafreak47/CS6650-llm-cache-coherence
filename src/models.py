from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# LLM state hierarchy
# ---------------------------------------------------------------------------

class LLMState(BaseModel):
    """
    Base class for all backend-specific prefix-cache states.

    Each backend subclasses this and stores whatever it needs to resume
    a prefill from a known point. The base class itself represents an
    empty / no-op state used by backends that ignore prior context
    (AnthropicLLM, DummyLLM).
    """


class ContentBlock(BaseModel):
    """
    A single structured text block for the Anthropic messages API.

    cache_control is intentionally NOT a field here. It is injected at
    send time by AnthropicCachedLLM.generate() on the last context block
    only. Storing it here would corrupt cached states whenever a new block
    is appended later.
    """
    type: Literal["text"] = "text"
    text: str


class AnthropicCachedState(LLMState):
    """
    State for AnthropicCachedLLM.

    Holds an ordered list of ContentBlocks accumulated via accumulate().
    Order matters — Anthropic's server-side prefix cache is positional, so
    all workers must send blocks in the same order to get cache hits.
    """
    blocks: list[ContentBlock] = []


class LlamaKVState(LLMState):
    """
    State for the llama.cpp backend (not yet implemented).

    Will hold serialised KV-cache tensors once LlamaLLM is written.
    """
    # future fields:
    #   tensors: list[bytes] = []
    #   seq_len: int = 0


# Transitional alias — keeps old imports working while the rename propagates.
KVState = LLMState


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class GitRepo(BaseModel):
    """Everything needed to connect to a specific branch of a GitHub repo."""
    url: str
    branch: str


class SQSMessage(BaseModel):
    """Task message consumed from AWS SQS."""
    git_repo: GitRepo
    context_files: list[str]
    target_file: str
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
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
