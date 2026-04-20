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
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .commit import commit_changes
from .git_client import GitClient
from .kv_cache import InMemoryKVCache, KVCacheInterface, RedisKVCache
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
_cache: KVCacheInterface
_status: WorkerStatusEnum = WorkerStatusEnum.STANDBY
_total_requests: int = 0
_llm_ready = threading.Event()
_init_detail: str = ""

def _download_with_progress(url: str, dest: str) -> None:
    global _init_detail
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    def _reporthook(block: int, block_size: int, total: int) -> None:
        if total > 0:
            mb_done = block * block_size / 1_048_576
            mb_total = total / 1_048_576
            global _init_detail
            _init_detail = f"downloading model ({mb_done:.0f} MB / {mb_total:.0f} MB)"

    urllib.request.urlretrieve(url, dest, reporthook=_reporthook)


_BUILD_MODE = os.environ.get("BUILD_MODE", "naive").lower()      # "naive" | "cached"
_KV_CACHE_SIZE = int(os.environ.get("KV_CACHE_SIZE", "100"))
_CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "memory").lower()  # "memory" | "redis"

# ---------------------------------------------------------------------------
# SQS worker loop
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    global _status, _total_requests
    _llm_ready.wait()

    queue_url = os.environ["SQS_QUEUE_URL"]
    sqs = SQSClient(queue_url=queue_url)

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
                kv_state, prompt = build_cached(msg, git, _llm, _cache)
            else:
                kv_state, prompt = build_naive(msg, git)

            kv_state, output = _llm.generate(prompt=prompt, state=kv_state)

            commit_changes(git, msg.target_file, output, msg.task_prompt)
            _cache.invalidate(msg.target_file)

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
    global _llm, _cache
    backend = os.getenv("LLM_BACKEND", "dummy").lower()
    model = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
    if backend == "anthropic":
        _llm = AnthropicLLM(model=model)
        _llm_ready.set()
    elif backend == "dummy":
        _llm = DummyLLM()
        _llm_ready.set()
    elif backend == "llama":
        model_path = os.environ.get("LLAMA_MODEL_PATH", "/tmp/model.gguf")
        model_url = os.getenv(
            "LLAMA_MODEL_URL",
            "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF"
            "/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        )

        def _init_llama() -> None:
            global _llm, _init_detail
            if not os.path.exists(model_path) and model_url:
                _init_detail = "downloading model…"
                logger.info("Downloading llama model from %s → %s", model_url, model_path)
                _download_with_progress(model_url, model_path)
                logger.info("Download complete")
            _init_detail = "loading model into memory"
            logger.info("Loading llama model: %s", model_path)
            from .llm.llama_llm import LlamaLLM
            _llm = LlamaLLM(model_path=model_path)
            _init_detail = ""
            logger.info("LLM backend ready: LlamaLLM")
            _llm_ready.set()

        threading.Thread(target=_init_llama, daemon=True, name="llama-init").start()
    else:
        raise ValueError(f"Unknown LLM_BACKEND={backend!r}. Valid options: 'anthropic', 'dummy', 'llama'")

    if _llm_ready.is_set():
        logger.info("LLM backend ready: %s", type(_llm).__name__)

    if _CACHE_BACKEND == "redis":
        _cache = RedisKVCache(redis_url=os.environ["REDIS_URL"], capacity=_KV_CACHE_SIZE)
        logger.info("Cache backend: Redis (%s)", os.environ["REDIS_URL"])
    else:
        _cache = InMemoryKVCache(capacity=_KV_CACHE_SIZE)
        logger.info("Cache backend: in-memory (capacity=%d)", _KV_CACHE_SIZE)

    t = threading.Thread(target=_worker_loop, daemon=True, name="sqs-worker")
    t.start()

    yield


app = FastAPI(title="CS6650 LLM Agent Worker", lifespan=_lifespan)


@app.get("/health", response_model=HealthResponse)
def health():
    if not _llm_ready.is_set():
        return HealthResponse(status="initializing", detail=_init_detail)
    return HealthResponse()


@app.get("/status", response_model=StatusResponse)
def status():
    return StatusResponse(status=_status)


@app.get("/metrics", response_model=MetricsResponse)
def get_metrics():
    in_tok, out_tok, latency = _llm.metrics()
    cs = _cache.stats()
    return MetricsResponse(
        llm_backend=os.getenv("LLM_BACKEND", "dummy"),
        build_mode=_BUILD_MODE,
        cache_backend=_CACHE_BACKEND,
        total_input_tokens=in_tok,
        total_output_tokens=out_tok,
        total_latency_ms=latency,
        total_requests=_total_requests,
        total_cache_read_tokens=getattr(_llm, "total_cache_read_tokens", 0),
        total_cache_creation_tokens=getattr(_llm, "total_cache_creation_tokens", 0),
        cache_bytes_written=cs.bytes_written,
        cache_bytes_read=cs.bytes_read,
        cache_hit_count=cs.hit_count,
        cache_miss_count=cs.miss_count,
    )


@app.post("/metrics/clear")
def clear_metrics():
    global _total_requests
    _llm.metrics(reset=True)
    _cache.reset_stats()
    _total_requests = 0
    return {"cleared": True}
