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
    empty / no-op state used by backends that ignore prior context.
    """

    def byte_size(self) -> int:
        """Serialised byte size of this state — used for cache I/O accounting."""
        return 0


class ContentBlock(BaseModel):
    """
    A single structured text block for the Anthropic messages API.

    cache_control is intentionally NOT a field here. It is injected at
    send time by AnthropicLLM.generate() on the last context block only.
    Storing it here would corrupt cached states whenever a new block is
    appended later.
    """
    type: Literal["text"] = "text"
    text: str


class AnthropicCachedState(LLMState):
    """
    State backed by real file text as structured content blocks.

    Used by both AnthropicLLM (sends blocks with cache_control markers)
    and DummyLLM (stores text so cache byte-size metrics are realistic).
    Order matters — Anthropic's prefix cache is positional.
    """
    blocks: list[ContentBlock] = []

    def byte_size(self) -> int:
        return sum(len(b.text.encode()) for b in self.blocks)


class LlamaKVState(LLMState):
    """State for the llama.cpp backend. Carries the accumulated KV cache."""
    prompt: str = ""
    token_count: int = 0
    llama_state_b64: str = ""  # pickle+base64 of LlamaState; empty until first accumulate

    def byte_size(self) -> int:
        # base64 encodes 3 binary bytes as 4 chars — approximate decoded size
        return len(self.llama_state_b64) * 3 // 4 if self.llama_state_b64 else len(self.prompt.encode())


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
    detail: str = ""


class StatusResponse(BaseModel):
    status: WorkerStatusEnum


class MetricsResponse(BaseModel):
    llm_backend: str = ""
    build_mode: str = ""
    cache_backend: str = ""
    kv_compress: bool = True
    cache_order: str = "size_desc"
    llama_seed: int = -1
    llama_temperature: float = 0.8
    total_input_tokens: int
    total_output_tokens: int
    total_latency_ms: float
    total_requests: int
    # Anthropic server-side cache (only populated with LLM_BACKEND=anthropic)
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    # Local LLM state cache I/O
    cache_bytes_written: int = 0
    cache_bytes_read: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0
