"""Queue and notification ports, each with an AWS and an in-memory implementation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class JobQueue(ABC):
    @abstractmethod
    def send(self, message: dict[str, Any]) -> None: ...


class Notifier(ABC):
    @abstractmethod
    def send(self, subject: str, body: str) -> None: ...


class SqsQueue(JobQueue):
    def __init__(self, queue_url: str, region: str | None = None, client: Any = None):
        if client is None:
            import boto3

            client = boto3.client("sqs", region_name=region)
        self._client = client
        self._url = queue_url

    def send(self, message: dict[str, Any]) -> None:
        self._client.send_message(QueueUrl=self._url, MessageBody=json.dumps(message))


class SnsNotifier(Notifier):
    def __init__(self, topic_arn: str, region: str | None = None, client: Any = None):
        if client is None:
            import boto3

            client = boto3.client("sns", region_name=region)
        self._client = client
        self._topic = topic_arn

    def send(self, subject: str, body: str) -> None:
        # SNS subjects are limited to 100 printable ASCII characters.
        safe_subject = subject.encode("ascii", "replace").decode()[:100]
        self._client.publish(TopicArn=self._topic, Subject=safe_subject, Message=body)


class MemoryQueue(JobQueue):
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class MemoryNotifier(Notifier):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class ConsoleNotifier(Notifier):
    def send(self, subject: str, body: str) -> None:
        print(f"\n[notification] {subject}\n{body}\n")
