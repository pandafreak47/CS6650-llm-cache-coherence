"""
Worker process — consumes tasks from the queue and runs the full pipeline:
  dequeue → build_message → llm.generate → commit → ack

One worker container processes exactly one task at a time, which is the
correct model when the LLM backend consumes all available container memory.
Scale the number of parallel workers by changing ecs_worker_count in terraform.

Run locally:
    QUEUE_BACKEND=memory LLM_BACKEND=dummy python worker.py
"""

import logging
import signal

from commit import commit
from llm import BaseLLM, create_llm
from message_builder import build_message
from queue import AbstractQueue, create_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def run(queue: AbstractQueue, llm: BaseLLM) -> None:
    logger.info("Worker started | llm=%s queue=%s", type(llm).__name__, type(queue).__name__)

    running = True

    def _handle_stop(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received — finishing current task then stopping")
        running = False

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    while running:
        messages = queue.dequeue()
        if not messages:
            continue

        msg = messages[0]
        task = msg.task
        logger.info(
            "task %s | started | repo=%s target=%s",
            task.request_id, task.repo, task.target_file,
        )

        try:
            prompt, kv_state = build_message(
                repo=task.repo,
                context_files=task.context_files,
                target_file=task.target_file,
                task=task.task,
                branch=task.branch,
            )
            result = llm.generate(prompt, kv_state, task.max_tokens)
            commit(task, result)
            queue.ack(msg.receipt_handle)
            logger.info(
                "task %s | completed | tokens=%d latency=%.1fms",
                task.request_id,
                result.input_tokens + result.output_tokens,
                result.latency_ms,
            )
        except Exception:
            logger.exception(
                "task %s | failed | repo=%s target=%s — nacking for redelivery",
                task.request_id, task.repo, task.target_file,
            )
            queue.nack(msg.receipt_handle)

    logger.info("Worker stopped cleanly")


if __name__ == "__main__":
    run(queue=create_queue(), llm=create_llm())
