from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from llm import AnthropicLLM, BaseLLM, LLMResult
from message_builder import build_message
from models import (
    GenerateRequest,
    GenerateResponse,
    TaskRequest,
    TaskResponse,
)

# ── App lifecycle ─────────────────────────────────────────────────────────────

_llm: BaseLLM


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _llm
    _llm = AnthropicLLM()
    yield


app = FastAPI(title="LLM Backend", lifespan=lifespan)

# ── In-memory aggregate metrics ───────────────────────────────────────────────

_metrics: dict = {
    "total_requests": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_latency_ms": 0.0,
}


def _record(result: LLMResult) -> None:
    _metrics["total_requests"] += 1
    _metrics["total_input_tokens"] += result.input_tokens
    _metrics["total_output_tokens"] += result.output_tokens
    _metrics["total_latency_ms"] += result.latency_ms


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    """Raw LLM call — useful for baselines and direct experiments."""
    try:
        result = _llm.generate(req.prompt, {}, req.max_tokens, req.system)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _record(result)
    return GenerateResponse(
        content=result.content,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )


@app.post("/task", response_model=TaskResponse)
def task(req: TaskRequest):
    """
    Agent task endpoint. Builds a prompt from the structured request, then
    calls the LLM. The KVState returned by build_message is passed through to
    the LLM wrapper — currently a no-op for Anthropic, but will carry prefix
    cache data when we switch to llama.cpp.
    """
    prompt, kv_state = build_message(
        repo=req.repo,
        context_files=req.context_files,
        target_file=req.target_file,
        task=req.task,
        branch=req.branch,
    )

    try:
        result = _llm.generate(prompt, kv_state, req.max_tokens)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _record(result)
    return TaskResponse(
        content=result.content,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )


@app.get("/metrics")
def get_metrics():
    total = _metrics["total_requests"]
    avg_latency = _metrics["total_latency_ms"] / total if total > 0 else 0.0
    return {**_metrics, "avg_latency_ms": round(avg_latency, 2)}
