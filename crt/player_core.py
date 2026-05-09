"""PlayerCore — transport layer for the headless sync daemon.

Manages cursor navigation (next/prev/play), casting via ChromecastManager,
and autoplay/loop on playback completion.  Does not download or encode —
it consumes items that are already in status='ready' with a filename set.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore, QueueItem

log = logging.getLogger(__name__)


def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class PlayerCore:
    def __init__(self, library: LibraryStore, chromecast: ChromecastManager) -> None:
        self.library = library
        self.chromecast = chromecast
        self.state: str = "idle"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_of(self, video_id: str) -> int | None:
        for i, item in enumerate(self.library.items):
            if item.video_id == video_id:
                return i
        return None

    def _cursor_index(self) -> int | None:
        if self.library.cursor_video_id is None:
            return None
        return self._index_of(self.library.cursor_video_id)

    # ------------------------------------------------------------------
    # Transport commands
    # ------------------------------------------------------------------

    async def next(self) -> None:
        """Advance cursor by one (with loop wrap if enabled) then cast."""
        if not self.library.items:
            return
        idx = self._cursor_index()
        if idx is None:
            new_idx = 0
        elif idx + 1 < len(self.library.items):
            new_idx = idx + 1
        elif self.library.loop_mode:
            new_idx = 0
        else:
            return
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        await self._cast_current()

    async def prev(self) -> None:
        """Move cursor back by one then cast."""
        if not self.library.items:
            return
        idx = self._cursor_index()
        if idx is None or idx == 0:
            return
        self.library.cursor_video_id = self.library.items[idx - 1].video_id
        await self._cast_current()

    async def play(self, video_id: str) -> None:
        """Jump cursor to a specific video_id and cast.

        Raises KeyError if video_id is not in the library.
        """
        idx = self._index_of(video_id)
        if idx is None:
            raise KeyError(f"video_id not in library: {video_id}")
        self.library.cursor_video_id = video_id
        await self._cast_current()

    async def toggle(self) -> None:
        """play→pause, paused→resume, idle→start cursor item (or first item)."""
        if self.state in ("playing", "casting"):
            await asyncio.to_thread(self.chromecast.pause_or_resume)
            self.state = "paused"
            return
        if self.state == "paused":
            await asyncio.to_thread(self.chromecast.pause_or_resume)
            self.state = "playing"
            return
        # idle → start cursor item (or first if no cursor)
        if self.library.cursor_video_id is None:
            if not self.library.items:
                return
            self.library.cursor_video_id = self.library.items[0].video_id
        await self._cast_current()

    async def stop(self) -> None:
        """Stop playback and set state to idle. Cursor stays on the current item;
        its status reverts to 'ready' so a subsequent toggle/play can re-cast it."""
        await asyncio.to_thread(self.chromecast.stop)
        self.state = "idle"
        idx = self._cursor_index()
        if idx is not None:
            item = self.library.items[idx]
            if item.status in ("playing", "casting", "paused"):
                item.status = "ready"

    async def stop_and_remove(self, video_id: str) -> None:
        """If video_id is the currently playing/casting/paused item, stop it.

        The caller is responsible for actually removing the item from the
        library after this returns.
        """
        if (
            self.library.cursor_video_id == video_id
            and self.state in ("playing", "casting", "paused")
        ):
            await asyncio.to_thread(self.chromecast.stop)
            self.state = "idle"

    async def on_playback_finished(self) -> None:
        """Called when the Chromecast reports natural end of the current item.

        Marks the current item as done, advances the cursor, and either casts
        the next item or transitions to idle (respecting loop_mode).
        """
        idx = self._cursor_index()
        if idx is not None:
            self.library.items[idx].status = "done"
            self.library.items[idx].playback_position = 0.0

        if idx is None:
            return

        if idx + 1 < len(self.library.items):
            self.library.cursor_video_id = self.library.items[idx + 1].video_id
            await self._cast_current()
        elif self.library.loop_mode:
            self.library.cursor_video_id = self.library.items[0].video_id
            await self._cast_current()
        else:
            self.state = "idle"

    # ------------------------------------------------------------------
    # Cast helpers
    # ------------------------------------------------------------------

    async def _cast_current(self) -> None:
        """Cast the item at the current cursor position.

        No-op if the item is not in status='ready' or has no filename.
        """
        idx = self._cursor_index()
        if idx is None:
            return
        item = self.library.items[idx]
        if not item.filename:
            log.info(
                "Cursor item %s has no cache file (status=%s); waiting",
                item.video_id,
                item.status,
            )
            return

        # Any item currently labelled playing/casting/paused but that's no longer
        # the cursor is stale — revert to "ready" so a future cursor move back
        # doesn't trip the "already playing" guard.
        for other in self.library.items:
            if other is not item and other.status in ("playing", "casting", "paused"):
                other.status = "ready"

        await self.chromecast.wait_for_connection()
        local_ip = _get_local_ip()
        url = f"http://{local_ip}:{config.SERVER_PORT}/media/{item.filename}"
        item.status = "casting"
        await asyncio.to_thread(
            self.chromecast.cast_url,
            url,
            start_position=item.playback_position,
        )
        self.state = "casting"
        item.status = "playing"
        log.info("Casting %s (resume from %.1fs)", item.video_id, item.playback_position)

    async def calibrate(self) -> None:
        """Cast a calibration pattern to the Chromecast.

        Uses calibration.generate_calibration_clip(out_path, duration_s) which
        is an async coroutine that renders an MP4 via ffmpeg and returns the
        path.  The resulting file is served from TEMP_DIR via the media server.
        """
        from crt import calibration

        await self.chromecast.wait_for_connection()
        out_path = os.path.join(config.TEMP_DIR, "calibration_pattern.mp4")
        await calibration.generate_calibration_clip(out_path)
        local_ip = _get_local_ip()
        filename = os.path.basename(out_path)
        url = f"http://{local_ip}:{config.SERVER_PORT}/media/{filename}"
        await asyncio.to_thread(self.chromecast.cast_url, url, start_position=0.0)
        log.info("Calibration pattern cast to Chromecast")
