import os
import time
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="LLM Backend")

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

# In-memory aggregate metrics (reset on restart)
_metrics = {
    "total_requests": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_latency_ms": 0.0,
}


class GenerateRequest(BaseModel):
    prompt: str
    max_tokens: int = 1024
    system: Optional[str] = None


class GenerateResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    client = get_client()
    kwargs: dict = {
        "model": MODEL,
        "max_tokens": req.max_tokens,
        "messages": [{"role": "user", "content": req.prompt}],
    }
    if req.system:
        kwargs["system"] = req.system

    start = time.time()
    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    latency_ms = (time.time() - start) * 1000

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    _metrics["total_requests"] += 1
    _metrics["total_input_tokens"] += input_tokens
    _metrics["total_output_tokens"] += output_tokens
    _metrics["total_latency_ms"] += latency_ms

    return GenerateResponse(
        content=response.content[0].text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )


@app.get("/metrics")
def get_metrics():
    total = _metrics["total_requests"]
    avg_latency = _metrics["total_latency_ms"] / total if total > 0 else 0.0
    return {
        **_metrics,
        "avg_latency_ms": round(avg_latency, 2),
    }
