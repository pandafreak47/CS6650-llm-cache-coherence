from abc import ABC, abstractmethod
from dataclasses import dataclass

from models import QueuedTask


@dataclass
class QueueMessage:
    message_id: str      # broker-assigned message ID
    receipt_handle: str  # opaque token needed to ack or nack
    task: QueuedTask


class AbstractQueue(ABC):
    @abstractmethod
    def enqueue(self, task: QueuedTask) -> str:
        """Push a task onto the queue. Returns the broker message ID."""
        ...

    @abstractmethod
    def dequeue(self) -> list[QueueMessage]:
        """
        Blocking poll for the next available message. Returns a list with
        0 or 1 items — 0 if the poll timed out with no messages waiting.
        """
        ...

    @abstractmethod
    def ack(self, receipt_handle: str) -> None:
        """Permanently remove a successfully processed message."""
        ...

    @abstractmethod
    def nack(self, receipt_handle: str) -> None:
        """Make a failed message immediately visible for redelivery."""
        ...
