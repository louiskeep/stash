"""Channel ports: ingest and delivery, split by responsibility."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class IncomingMessage:
    update_id: int
    chat_id: str
    chat_type: str
    sender_id: str
    msg_id: str
    text: str


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


class AsyncDelivery(Protocol):
    async def send(self, chat_id: str, text: str) -> None: ...


class FakeAsyncDelivery:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail_times

    async def send(self, chat_id: str, text: str) -> None:
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("transient")
        self.sent.append((chat_id, text))


class FakeAsyncIngest:
    def __init__(self, messages: list[IncomingMessage] | None = None) -> None:
        self._queue = list(messages or [])
        self.offset: str | None = None

    async def poll(self, offset=None) -> list[IncomingMessage]:
        out, self._queue = self._queue, []
        return out

    def ack(self, offset: str) -> None:
        self.offset = offset
