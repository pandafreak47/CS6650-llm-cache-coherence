import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException

from commit import commit
from llm import BaseLLM, LLMResult, create_llm
from message_builder import build_message
from models import (
    GenerateRequest,
    GenerateResponse,
    TaskAccepted,
    TaskRequest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── App lifecycle ─────────────────────────────────────────────────────────────

_llm: BaseLLM


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _llm
    _llm = create_llm()
    logger.info("LLM backend ready: %s", type(_llm).__name__)
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


# ── Background task ───────────────────────────────────────────────────────────

def _run_task(request_id: str, req: TaskRequest) -> None:
    """Full pipeline: build prompt → LLM → commit. Runs after the 202 is sent."""
    try:
        prompt, kv_state = build_message(
            repo=req.repo,
            context_files=req.context_files,
            target_file=req.target_file,
            task=req.task,
            branch=req.branch,
        )
        result = _llm.generate(prompt, kv_state, req.max_tokens)
        _record(result)
        commit(req, result)
        logger.info("task %s completed | %s → %s", request_id, req.repo, req.target_file)
    except Exception:
        logger.exception("task %s failed | repo=%s target=%s", request_id, req.repo, req.target_file)


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
def task(req: TaskRequest, background_tasks: BackgroundTasks):
    """
    Accept a coding task and process it asynchronously. Returns immediately
    with a request_id. The full pipeline (build → LLM → commit) runs in the
    background after the response is sent. Failures are logged server-side.
    """
    request_id = str(uuid.uuid4())
    background_tasks.add_task(_run_task, request_id, req)
    return TaskAccepted(request_id=request_id)


@app.get("/metrics")
def get_metrics():
    total = _metrics["total_requests"]
    avg_latency = _metrics["total_latency_ms"] / total if total > 0 else 0.0
    return {**_metrics, "avg_latency_ms": round(avg_latency, 2)}
