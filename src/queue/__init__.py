import os

from .base import AbstractQueue, QueueMessage
from .sqs_queue import SQSQueue
from .memory_queue import MemoryQueue

__all__ = ["AbstractQueue", "QueueMessage", "SQSQueue", "MemoryQueue", "create_queue"]


def create_queue() -> AbstractQueue:
    """
    Instantiate the queue backend selected by the QUEUE_BACKEND env var.

      QUEUE_BACKEND=sqs     (default) — AWS SQS FIFO queue
      QUEUE_BACKEND=memory            — in-process queue; no AWS needed
    """
    backend = os.getenv("QUEUE_BACKEND", "sqs").lower()

    if backend == "sqs":
        return SQSQueue()
    if backend == "memory":
        return MemoryQueue()

    raise ValueError(
        f"Unknown QUEUE_BACKEND={backend!r}. Valid options: 'sqs', 'memory'"
    )
