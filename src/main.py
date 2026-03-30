"""
Worker entry point.

Runs two things concurrently:
  1. A background thread that polls the SQS queue and processes tasks.
  2. A FastAPI HTTP server exposing /health, /status, /metrics, /metrics/clear.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .commit import commit_changes
from .git_client import GitClient
from .kv_cache import InMemoryKVCache
from .llm import AnthropicLLM, DummyLLM, InterfaceLLM
from .message_builder import build_naive, build_cached
from .models import (
    HealthResponse,
    MetricsResponse,
    SQSMessage,
    StatusResponse,
    WorkerStatusEnum,
)
from .sqs_client import SQSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker state (module-level singletons, written only from the worker thread)
# ---------------------------------------------------------------------------

_llm: InterfaceLLM
_status: WorkerStatusEnum = WorkerStatusEnum.STANDBY
_total_requests: int = 0

_BUILD_MODE = os.environ.get("BUILD_MODE", "naive").lower()  # "naive" | "cached"
_KV_CACHE_SIZE = int(os.environ.get("KV_CACHE_SIZE", "100"))

# ---------------------------------------------------------------------------
# SQS worker loop
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    global _status, _total_requests

    queue_url = os.environ["SQS_QUEUE_URL"]
    sqs = SQSClient(queue_url=queue_url)
    cache = InMemoryKVCache(capacity=_KV_CACHE_SIZE)

    logger.info("Worker started | mode=%s cache_size=%d", _BUILD_MODE, _KV_CACHE_SIZE)

    while True:
        raw = sqs.receive(wait_seconds=20)
        if raw is None:
            continue  # long-poll timed out, try again

        _status = WorkerStatusEnum.PROCESSING
        try:
            msg = SQSMessage.model_validate_json(raw.body)
            git = GitClient(msg.git_repo)

            logger.info(
                "Processing | target=%s branch=%s",
                msg.target_file,
                msg.git_repo.branch,
            )

            if _BUILD_MODE == "cached":
                kv_state, prompt = build_cached(msg, git, _llm, cache)
            else:
                kv_state, prompt = build_naive(msg, git)

            kv_state, output = _llm.generate(prompt=prompt, kv_state=kv_state)

            commit_changes(git, msg.target_file, output, msg.task_prompt)

            sqs.ack(raw)
            _total_requests += 1
            logger.info("Done | target=%s", msg.target_file)

        except Exception:
            logger.exception("Failed to process message — leaving in queue for redelivery")
        finally:
            _status = WorkerStatusEnum.STANDBY


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _llm
    backend = os.getenv("LLM_BACKEND", "dummy").lower()
    model = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
    if backend == "anthropic":
        _llm = AnthropicLLM(model=model)
    elif backend == "dummy":
        _llm = DummyLLM()
    # elif backend == "llama":
    #     from .llm.llama_llm import LlamaLLM
    #     _llm = LlamaLLM(model_path=os.environ["LLAMA_MODEL_PATH"])
    else:
        raise ValueError(f"Unknown LLM_BACKEND={backend!r}. Valid options: 'anthropic', 'dummy'")
    logger.info("LLM backend ready: %s", type(_llm).__name__)

    t = threading.Thread(target=_worker_loop, daemon=True, name="sqs-worker")
    t.start()

    yield


app = FastAPI(title="CS6650 LLM Agent Worker", lifespan=_lifespan)


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@app.get("/status", response_model=StatusResponse)
def status():
    return StatusResponse(status=_status)


@app.get("/metrics", response_model=MetricsResponse)
def get_metrics():
    in_tok, out_tok, latency = _llm.metrics()
    return MetricsResponse(
        total_input_tokens=in_tok,
        total_output_tokens=out_tok,
        total_latency_ms=latency,
        total_requests=reqs,
    )


@app.post("/metrics/clear")
def clear_metrics():
    global _total_requests
    _llm.metrics(reset=True)
    _total_requests = 0
    return {"cleared": True}
