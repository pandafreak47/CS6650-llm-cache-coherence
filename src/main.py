import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from llm import BaseLLM, LLMResult, create_llm
from models import (
    GenerateRequest,
    GenerateResponse,
    QueuedTask,
    TaskAccepted,
    TaskRequest,
)
from queue import AbstractQueue, create_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── App lifecycle ─────────────────────────────────────────────────────────────

_llm: BaseLLM
_queue: AbstractQueue


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _llm, _queue
    _llm = create_llm()
    _queue = create_queue()
    logger.info(
        "Ready | llm=%s queue=%s",
        type(_llm).__name__,
        type(_queue).__name__,
    )
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


@app.post("/task", status_code=202, response_model=TaskAccepted)
def task(req: TaskRequest):
    """
    Enqueue a coding task and return immediately. Processing happens in a
    separate worker container — the caller should not wait for a result here.
    Use request_id to correlate log entries in CloudWatch.
    """
    queued = QueuedTask(request_id=str(uuid.uuid4()), **req.model_dump())
    try:
        _queue.enqueue(queued)
    except Exception as e:
        logger.exception("Failed to enqueue task for %s", req.target_file)
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {e}")

    logger.info("task %s | enqueued | repo=%s target=%s", queued.request_id, req.repo, req.target_file)
    return TaskAccepted(request_id=queued.request_id)


@app.get("/metrics")
def get_metrics():
    total = _metrics["total_requests"]
    avg_latency = _metrics["total_latency_ms"] / total if total > 0 else 0.0
    return {**_metrics, "avg_latency_ms": round(avg_latency, 2)}
