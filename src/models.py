from typing import Any, Optional

from pydantic import BaseModel

# Opaque KV-cache state. For Anthropic this is always empty — the API handles
# caching server-side. When we switch to llama.cpp this will carry the actual
# key/value tensors so the backend can skip re-processing shared prefix tokens.
KVState = dict[str, Any]


# ── Raw generation (low-level) ────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    max_tokens: int = 1024
    system: Optional[str] = None


class GenerateResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


# ── Agent task (high-level) ───────────────────────────────────────────────────

class TaskRequest(BaseModel):
    repo: str
    context_files: list[str]
    target_file: str
    task: str
    branch: str = "main"
    max_tokens: int = 1024


class TaskResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
