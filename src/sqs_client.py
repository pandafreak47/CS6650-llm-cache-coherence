from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

import boto3


@dataclass
class RawMessage:
    body: str
    receipt_handle: str


class SQSClient:
    """Thin wrapper around boto3 SQS for FIFO queue operations."""

    def __init__(self, queue_url: str, region: str | None = None):
        self._queue_url = queue_url
        self._sqs = boto3.client(
            "sqs",
            region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
        )

    def receive(self, wait_seconds: int = 20) -> RawMessage | None:
        """Long-poll for one message. Returns None if the queue is empty."""
        response = self._sqs.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        m = messages[0]
        return RawMessage(body=m["Body"], receipt_handle=m["ReceiptHandle"])

    def ack(self, msg: RawMessage) -> None:
        """Delete the message from the queue, signalling successful processing."""
        self._sqs.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=msg.receipt_handle,
        )

    def send(self, body: dict, message_group_id: str) -> None:
        """Send a message to the FIFO queue.

        message_group_id should be the target_file path — SQS ensures only one
        message per group is in-flight at a time, giving us file-level locking.
        """
        body_str = json.dumps(body)
        # Deterministic deduplication ID: hash of group + body.
        dedup_id = hashlib.sha256(
            f"{message_group_id}:{body_str}".encode()
        ).hexdigest()[:40]
        self._sqs.send_message(
            QueueUrl=self._queue_url,
            MessageBody=body_str,
            MessageGroupId=message_group_id,
            MessageDeduplicationId=dedup_id,
        )

    def get_queue_depth(self) -> tuple[int, int]:
        """Return (visible_messages, in_flight_messages)."""
        response = self._sqs.get_queue_attributes(
            QueueUrl=self._queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )
        attrs = response["Attributes"]
        visible = int(attrs.get("ApproximateNumberOfMessages", 0))
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
        return visible, in_flight

    def purge(self) -> None:
        """Purge all messages. Note: SQS may take up to 60 s to complete."""
        self._sqs.purge_queue(QueueUrl=self._queue_url)
