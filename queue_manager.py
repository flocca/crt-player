from __future__ import annotations

import uuid
from dataclasses import dataclass, field


ACTIVE_STATUSES = {"downloading", "encoding", "casting", "playing"}


@dataclass
class QueueItem:
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None


class QueueManager:
    def __init__(self) -> None:
        self.items: list[QueueItem] = []
        self.history: list[QueueItem] = []

    def push_to_history(self, item: QueueItem) -> None:
        self.history.append(item)

    def pop_from_history(self) -> QueueItem | None:
        if not self.history:
            return None
        return self.history.pop()

    def add(self, url: str, mode: str = "queue") -> QueueItem:
        item = QueueItem(url=url)
        if mode == "queue":
            self.items.append(item)
        elif mode == "next":
            insert_idx = self._after_active_index()
            self.items.insert(insert_idx, item)
        elif mode == "now":
            self.items.insert(0, item)
        return item

    def remove(self, item_id: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                if item.status != "queued":
                    return False
                self.items.pop(i)
                return True
        return False

    def move(self, item_id: str, direction: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                if item.status != "queued":
                    return False
                if direction == "up" and i > 0 and self.items[i - 1].status == "queued":
                    self.items[i], self.items[i - 1] = self.items[i - 1], self.items[i]
                    return True
                if direction == "down" and i < len(self.items) - 1 and self.items[i + 1].status == "queued":
                    self.items[i], self.items[i + 1] = self.items[i + 1], self.items[i]
                    return True
                return False
        return False

    def next_pending(self) -> QueueItem | None:
        for item in self.items:
            if item.status == "queued":
                return item
        return None

    def active_item(self) -> QueueItem | None:
        for item in self.items:
            if item.status in ACTIVE_STATUSES:
                return item
        return None

    def _after_active_index(self) -> int:
        for i, item in enumerate(self.items):
            if item.status in ACTIVE_STATUSES:
                return i + 1
        return 0
