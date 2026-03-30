"""
In-process queue for local development and testing.
No AWS credentials or network access required.
"""

import queue
import uuid

from models import QueuedTask
from .base import AbstractQueue, QueueMessage

# How long dequeue() blocks waiting for a message before returning []
_POLL_TIMEOUT_SECONDS = 1


class MemoryQueue(AbstractQueue):
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        # Maps receipt_handle → task so nack can re-enqueue
        self._inflight: dict[str, QueuedTask] = {}

    def enqueue(self, task: QueuedTask) -> str:
        msg_id = str(uuid.uuid4())
        self._q.put((msg_id, task))
        return msg_id

    def dequeue(self) -> list[QueueMessage]:
        try:
            msg_id, task = self._q.get(timeout=_POLL_TIMEOUT_SECONDS)
        except queue.Empty:
            return []
        receipt_handle = str(uuid.uuid4())
        self._inflight[receipt_handle] = task
        return [QueueMessage(message_id=msg_id, receipt_handle=receipt_handle, task=task)]

    def ack(self, receipt_handle: str) -> None:
        self._inflight.pop(receipt_handle, None)

    def nack(self, receipt_handle: str) -> None:
        task = self._inflight.pop(receipt_handle, None)
        if task:
            self._q.put((str(uuid.uuid4()), task))
