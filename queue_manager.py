from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field

import config

log = logging.getLogger(__name__)

ACTIVE_STATUSES = {"downloading", "encoding", "casting", "playing", "ready"}


@dataclass
class QueueItem:
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None
    playback_position: float = 0.0
    downloaded_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "filename": self.filename,
            "playback_position": self.playback_position,
            "downloaded_path": self.downloaded_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        return cls(
            url=d["url"],
            id=d.get("id", str(uuid.uuid4())),
            title=d.get("title", ""),
            status=d.get("status", "queued"),
            progress=d.get("progress", 0.0),
            error=d.get("error"),
            filename=d.get("filename"),
            playback_position=d.get("playback_position", 0.0),
            downloaded_path=d.get("downloaded_path"),
        )


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

    def move_to_front(self, item_id: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                self.items.pop(i)
                self.items.insert(0, item)
                return True
        return False

    def next_pending(self) -> QueueItem | None:
        for item in self.items:
            if item.status in ("queued", "ready"):
                return item
        return None

    def first_queued(self) -> QueueItem | None:
        """First item with status 'queued' (for the prepare loop)."""
        for item in self.items:
            if item.status == "queued":
                return item
        return None

    def next_ready(self) -> QueueItem | None:
        """First 'ready' item that can be cast now (no unfinished items before it)."""
        for item in self.items:
            if item.status in ("queued", "downloading", "encoding"):
                return None
            if item.status == "ready":
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

    def save_state(self, path: str, playback_position: float = 0.0) -> None:
        data = {
            "version": 1,
            "playback_position": playback_position,
            "items": [item.to_dict() for item in self.items],
            "history": [item.to_dict() for item in self.history],
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            log.exception("Failed to save state to %s", path)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def load_state(self, path: str) -> float:
        """Load queue state from disk. Returns saved playback position."""
        if not os.path.isfile(path):
            return 0.0
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt or unreadable state file %s, starting fresh", path)
            return 0.0

        playback_position = data.get("playback_position", 0.0)

        for raw in data.get("items", []):
            item = QueueItem.from_dict(raw)
            if item.status == "downloading":
                item.status = "queued"
                item.progress = 0.0
                item.filename = None
                item.downloaded_path = None
            elif item.status == "encoding":
                # Clean up partial encoded output so the cache check won't reuse it
                if item.downloaded_path:
                    base = os.path.splitext(os.path.basename(item.downloaded_path))[0]
                    partial = os.path.join(config.TEMP_DIR, f"{base}_pal_{config.SCALE_MODE}.mp4")
                    if os.path.isfile(partial):
                        try:
                            os.unlink(partial)
                        except OSError:
                            pass
                    # Keep downloaded_path only if the source file still exists
                    if not os.path.isfile(item.downloaded_path):
                        item.downloaded_path = None
                item.status = "queued"
                item.progress = 0.0
                item.filename = None
            elif item.status == "casting":
                item.status = "queued"
                item.progress = 0.0
                item.filename = None
            elif item.status == "playing":
                if item.filename and os.path.isfile(
                    os.path.join(config.TEMP_DIR, item.filename)
                ):
                    item.status = "ready"
                else:
                    item.status = "queued"
                    item.filename = None
                item.progress = 0.0
            self.items.append(item)

        for raw in data.get("history", []):
            self.history.append(QueueItem.from_dict(raw))

        log.info(
            "Loaded state: %d items, %d history, resume at %.1fs",
            len(self.items), len(self.history), playback_position,
        )
        return playback_position
