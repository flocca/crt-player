"""Screen — what the TV teletext page shows while paused or stopped.

Pure logic, no I/O beyond the two QueueItem file checks: PlayerCore owns one
instance while the player is paused or stopped, moves it on next/prev and
sends `view()` to the receiver page.

The selection is a video of the playlist; a second axis walks the commands
(two rows: video commands, then player commands).
"""
from __future__ import annotations

from typing import Callable

from crt.library_store import QueueItem

ROWS_PER_PAGE = 10
PREVIEW_WINDOW_S = 20.0

LABELS = {
    "resume": "Riprendi",
    "play": "Riproduci",
    "delete": "Elimina",
    "stop": "Stop",
    "calibrate": "Calibra",
    "loop": "Loop",
    "sync": "Sync",
    "power": "Spegni",
}

CONFIRM_DELETE = "Eliminare dalla playlist? OK = sì"
NOT_READY_NOTICE = "Non ancora pronto"
END_OF_QUEUE_NOTICE = "Fine della coda"
REMOVED_NOTICE = "Video rimosso dalla playlist"
EMPTY_PLAYLIST = "La playlist è vuota"

_READY_LIKE = ("ready", "casting", "playing", "paused")
_PREPARING = ("queued", "downloading", "encoding")
_PREFIXES = {
    "queued": "In coda · ",
    "downloading": "Download · ",
    "encoding": "Codifica · ",
    "error": "Errore nel video · ",
}
_VIDEO_COMMANDS = ("resume", "delete", "stop")
_PLAYER_COMMANDS = ["calibrate", "loop", "sync", "power"]


def preview_window(position: float) -> tuple[float, float]:
    """The 20 s of video shown behind the page: the ones before `position`."""
    if position >= PREVIEW_WINDOW_S:
        return (position - PREVIEW_WINDOW_S, position)
    return (0.0, PREVIEW_WINDOW_S)


def _format_position(seconds: float) -> str:
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def row_for(item: QueueItem, loaded_video_id: str | None) -> dict:
    """One playlist row; `num` is filled by Screen.view."""
    status = item.status
    loaded = item.video_id == loaded_video_id
    if loaded:
        symbol, kind = "▶", "ready"
    elif status in _READY_LIKE:
        symbol, kind = "○", "ready"
    elif status == "done":
        symbol, kind = "✓", "done"
    elif status == "error":
        symbol, kind = "!", "error"
    else:  # queued, downloading, encoding and anything unknown
        symbol, kind = "…", "preparing"
    text = item.title if loaded else _PREFIXES.get(status, "") + item.title
    return {"num": 0, "symbol": symbol, "text": text, "right": _right_field(item), "kind": kind}


def _right_field(item: QueueItem) -> str:
    """Percentage while preparing, time left once started, else the duration."""
    if item.status in ("downloading", "encoding"):
        return f"{int(item.progress)}%"
    if item.duration <= 0:
        return ""
    if item.playback_position > 0:
        # Floored apart like row 4 (00:52 / 09:12 → -08:20).
        left = max(0, int(item.duration) - int(item.playback_position))
        if left >= 36000:  # -10:00:00 would take 9 columns
            h, rest = divmod(left, 3600)
            return f"-{h}h{rest // 60:02d}"
        return "-" + _format_position(left)
    return _format_position(item.duration)


class Screen:
    def __init__(self, selected_video_id: str | None, loaded_video_id: str | None = None) -> None:
        self.selected_video_id = selected_video_id
        self.loaded_video_id = loaded_video_id
        self.command_index = 0
        self.confirm_video_id: str | None = None
        self.flash: str | None = None
        self.flash_kind = "info"  # "info" | "error"
        self.arrival: str | None = None
        self.night = False

    def commands(self, items: list[QueueItem]) -> list[str]:
        video: list[str] = []
        if items:
            video = ["resume", "delete"] + (["stop"] if self.loaded_video_id else [])
        return video + _PLAYER_COMMANDS

    def fix_selection(self, items: list[QueueItem], cursor_video_id: str | None) -> None:
        ids = [i.video_id for i in items]
        if self.selected_video_id not in ids:
            if cursor_video_id in ids:
                self.selected_video_id = cursor_video_id
            else:
                self.selected_video_id = ids[0] if ids else None
        if self.confirm_video_id not in ids:
            self.confirm_video_id = None

    def _clear_flash(self) -> None:
        self.confirm_video_id = None
        self.flash = None

    def move(self, items: list[QueueItem], delta: int) -> None:
        self.fix_selection(items, None)
        ids = [i.video_id for i in items]
        if ids:
            idx = ids.index(self.selected_video_id)
            self.selected_video_id = ids[(idx + delta) % len(ids)]
        self.command_index = 0
        self._clear_flash()

    def move_command(self, items: list[QueueItem], delta: int) -> None:
        self.command_index = (self._index(items) + delta) % len(self.commands(items))
        self._clear_flash()

    def _index(self, items: list[QueueItem]) -> int:
        return min(max(self.command_index, 0), len(self.commands(items)) - 1)

    def selected_command(self, items: list[QueueItem]) -> str:
        return self.commands(items)[self._index(items)]

    def _selected_item(self, items: list[QueueItem]) -> QueueItem | None:
        return next((i for i in items if i.video_id == self.selected_video_id), None)

    def resume_label(self, items: list[QueueItem]) -> str:
        item = self._selected_item(items)
        if item is not None and (
            item.video_id == self.loaded_video_id or item.playback_position > 0
        ):
            return LABELS["resume"]
        return LABELS["play"]

    def _label(self, key: str, items: list[QueueItem]) -> str:
        return self.resume_label(items) if key == "resume" else LABELS[key]

    def view(
        self,
        items: list[QueueItem],
        cursor_video_id: str | None,
        *,
        loop: bool,
        sync_at: str | None,
        sync_error: bool,
        preview_url: Callable[[QueueItem], str | None],
    ) -> dict:
        self.fix_selection(items, cursor_video_id)
        count = len(items)
        ids = [i.video_id for i in items]
        sel_idx = ids.index(self.selected_video_id) if self.selected_video_id in ids else 0
        pages = max(1, -(-count // ROWS_PER_PAGE))
        page_idx = sel_idx // ROWS_PER_PAGE
        start = page_idx * ROWS_PER_PAGE
        rows = []
        for n, item in enumerate(items[start:start + ROWS_PER_PAGE], start=start + 1):
            row = row_for(item, self.loaded_video_id)
            row["num"] = n
            row["selected"] = item.video_id == self.selected_video_id
            row["confirm"] = item.video_id == self.confirm_video_id
            rows.append(row)

        keys = self.commands(items)
        sel_key = keys[self._index(items)]
        entries = [
            {"key": k, "label": self._label(k, items), "selected": k == sel_key}
            for k in keys
        ]
        video_cmds = [e for e in entries if e["key"] in _VIDEO_COMMANDS]
        player_cmds = [e for e in entries if e["key"] not in _VIDEO_COMMANDS]

        loaded = next((i for i in items if i.video_id == self.loaded_video_id), None)
        selected = self._selected_item(items)
        background, preview = "black", None
        if selected is not None:
            if selected.video_id == self.loaded_video_id:
                background = "frame"
            elif selected.status in _PREPARING or selected.status == "error":
                background = "snow"
            else:
                url = preview_url(selected)
                if url is not None:
                    start_s, end_s = preview_window(selected.playback_position)
                    background = "preview"
                    preview = {"url": url, "start": start_s, "end": end_s}

        if self.confirm_video_id is not None:
            flash, flash_kind = CONFIRM_DELETE, "confirm"
        else:
            flash, flash_kind = self.flash, self.flash_kind
        status = self.arrival or (EMPTY_PLAYLIST if not items else None)

        return {
            "state": "paused" if self.loaded_video_id else "stopped",
            "count": count,
            "page": page_idx + 1,
            "pages": pages,
            "rows": rows,
            "paused_position": loaded.playback_position if loaded else None,
            "paused_duration": (loaded.duration or None) if loaded else None,
            "status": status,
            "commands": [video_cmds, player_cmds],
            "flash": flash,
            "flash_kind": flash_kind,
            "help": "OK = " + self._label(sel_key, items),
            "loop": loop,
            "sync_at": sync_at,
            "sync_error": sync_error,
            "background": background,
            "preview": preview,
            "night": self.night,
        }
