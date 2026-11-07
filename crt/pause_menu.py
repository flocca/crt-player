"""PauseMenu — what the TV menu shows while paused and where the highlight is.

Pure logic, no I/O: PlayerCore owns one instance while paused, moves it on
next/prev and sends `view()` to the receiver page.

The highlight walks a ring: the playlist videos in library order, then the
five commands, then back to the first video.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crt.library_store import QueueItem

COMMANDS: tuple[tuple[str, str], ...] = (
    ("stop", "Stop"),
    ("delete", "Elimina video"),
    ("calibrate", "Calibrate"),
    ("loop", "Toggle loop"),
    ("sync", "Sync now"),
)
VISIBLE_ROWS = 7
CONFIRM_DELETE = "Confermi? OK sì"

_READY_LIKE = ("ready", "casting", "playing", "paused")


@dataclass(frozen=True)
class Entry:
    kind: Literal["video", "command"]
    key: str


def status_symbol(item: QueueItem, is_cursor: bool) -> str:
    if is_cursor:
        return "▶"
    status = item.status
    if status in _READY_LIKE:
        return "○"
    if status == "done":
        return "✓"
    if status in ("downloading", "encoding"):
        return f"… {int(item.progress)}%"
    if status == "error":
        return "!"
    return "…"  # queued and anything unknown


class PauseMenu:
    def __init__(self, current_video_id: str) -> None:
        self.current_video_id = current_video_id
        self.selected: Entry = Entry("video", current_video_id)
        self.confirming_delete: bool = False
        self.notice: str | None = None

    def entries(self, video_ids: list[str]) -> list[Entry]:
        return [Entry("video", v) for v in video_ids] + [
            Entry("command", key) for key, _ in COMMANDS
        ]

    def _fix_selection(self, video_ids: list[str], cursor_video_id: str | None) -> None:
        """A selected video that left the library falls back to the cursor
        video, else to the first entry."""
        if self.selected.kind != "video" or self.selected.key in video_ids:
            return
        if cursor_video_id is not None and cursor_video_id in video_ids:
            self.selected = Entry("video", cursor_video_id)
        else:
            self.selected = self.entries(video_ids)[0]

    def move(self, video_ids: list[str], delta: int) -> None:
        # move() has no cursor argument: the video the menu opened on is the
        # cursor for the whole life of the menu.
        self._fix_selection(video_ids, self.current_video_id)
        ring = self.entries(video_ids)
        idx = ring.index(self.selected)
        self.selected = ring[(idx + delta) % len(ring)]
        self.confirming_delete = False
        self.notice = None

    def view(self, items: list[QueueItem], cursor_video_id: str | None) -> dict:
        video_ids = [i.video_id for i in items]
        self._fix_selection(video_ids, cursor_video_id)
        n = len(items)
        if self.selected.kind == "video":
            i = video_ids.index(self.selected.key)
            start = min(max(i - 3, 0), max(n - VISIBLE_ROWS, 0))
        else:
            start = max(n - VISIBLE_ROWS, 0)
        rows = [
            {
                "symbol": status_symbol(item, item.video_id == cursor_video_id),
                "title": item.title,
                "selected": self.selected == Entry("video", item.video_id),
                "dim": item.status == "done",
            }
            for item in items[start:start + VISIBLE_ROWS]
        ]
        commands = [
            {"label": label, "selected": self.selected == Entry("command", key)}
            for key, label in COMMANDS
        ]
        cursor = next((i for i in items if i.video_id == cursor_video_id), None)
        return {
            "title": cursor.title if cursor is not None else "",
            "rows": rows,
            "commands": commands,
            "notice": self.notice,
            "confirm": CONFIRM_DELETE if self.confirming_delete else None,
        }
