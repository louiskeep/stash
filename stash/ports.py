"""Channel ports: ingest and delivery, split by responsibility."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class IncomingMessage:
    chat_id: str
    msg_id: str
    text: str
    sender_id: str


class IngestSource(Protocol):
    def poll(self) -> list[IncomingMessage]: ...
    def ack(self, offset: str) -> None: ...


class Delivery(Protocol):
    def send(self, chat_id: str, text: str) -> None: ...


class MemoryIngest:
    def __init__(self, messages: list[IncomingMessage] | None = None) -> None:
        self._queue = list(messages or [])
        self.offset: str | None = None

    def poll(self) -> list[IncomingMessage]:
        out, self._queue = self._queue, []
        return out

    def ack(self, offset: str) -> None:
        self.offset = offset


class MemoryDelivery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))
