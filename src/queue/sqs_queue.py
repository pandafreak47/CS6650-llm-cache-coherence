import os
import uuid

import boto3

from models import QueuedTask
from .base import AbstractQueue, QueueMessage

# Long-poll duration (seconds). Max allowed by SQS is 20.
_WAIT_SECONDS = 20


class SQSQueue(AbstractQueue):
    def __init__(self) -> None:
        region = os.getenv("AWS_REGION", "us-east-1")
        self._client = boto3.client("sqs", region_name=region)
        self._queue_url = os.environ["SQS_QUEUE_URL"]

    def enqueue(self, task: QueuedTask) -> str:
        resp = self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=task.model_dump_json(),
            # Unique group per message — FIFO for deduplication only,
            # not for ordering. Switch to target_file for per-file ordering
            # in a later phase.
            MessageGroupId=str(uuid.uuid4()),
            # request_id ensures uniqueness within the 5-minute dedup window
            # even if two agents submit identical task bodies.
            MessageDeduplicationId=task.request_id,
        )
        return resp["MessageId"]

    def dequeue(self) -> list[QueueMessage]:
        resp = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=_WAIT_SECONDS,
        )
        messages = []
        for msg in resp.get("Messages", []):
            task = QueuedTask.model_validate_json(msg["Body"])
            messages.append(QueueMessage(
                message_id=msg["MessageId"],
                receipt_handle=msg["ReceiptHandle"],
                task=task,
            ))
        return messages

    def ack(self, receipt_handle: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )

    def nack(self, receipt_handle: str) -> None:
        # Zero-second timeout makes the message immediately visible again
        # rather than waiting out the full visibility timeout.
        self._client.change_message_visibility(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=0,
        )
