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

    def _find_playable_index(self, *, start_idx: int, direction: int, wrap: bool) -> int | None:
        """Scan items starting at start_idx in `direction` (+1 forward, -1 backward),
        returning the index of the first item with a non-empty filename (cache hit).

        If `wrap` is True, continues from the opposite end after crossing the
        boundary, stopping when it would revisit start_idx. Returns None if no
        playable item exists in the scanned range.

        Used by next/prev/toggle/on_playback_finished to skip items that are
        still encoding or queued — the remote should land only on cast-ready items.
        """
        n = len(self.library.items)
        if n == 0 or start_idx < 0 or start_idx >= n:
            return None
        idx = start_idx
        for _ in range(n):
            if self.library.items[idx].filename:
                return idx
            idx += direction
            if idx < 0 or idx >= n:
                if not wrap:
                    return None
                idx %= n
        return None

    # ------------------------------------------------------------------
    # Transport commands
    # ------------------------------------------------------------------

    async def next(self) -> None:
        """Advance cursor to the next playable item (with loop wrap if enabled) then cast.

        Skips items still encoding/queued — the remote should never land on
        an item that can't be cast.
        """
        if not self.library.items:
            return
        n = len(self.library.items)
        idx = self._cursor_index()
        if idx is None:
            start = 0
        else:
            start = idx + 1
            if start >= n:
                if not self.library.loop_mode:
                    return
                start = 0
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            return
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        await self._cast_current()

    async def prev(self) -> None:
        """Move cursor back to the previous playable item then cast.

        Skips items still encoding/queued. No wrap (matches existing behavior).
        """
        if not self.library.items:
            return
        idx = self._cursor_index()
        if idx is None or idx == 0:
            return
        new_idx = self._find_playable_index(
            start_idx=idx - 1, direction=-1, wrap=False,
        )
        if new_idx is None:
            return
        self.library.cursor_video_id = self.library.items[new_idx].video_id
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
        """play→pause, paused→resume, idle→start nearest playable item.

        From idle, scans forward from the cursor (inclusive) for the first item
        with a cache file and casts it. If the cursor lands on an unready item
        (encoding/queued), the remote's toggle skips ahead instead of no-op.
        """
        if self.state in ("playing", "casting"):
            await asyncio.to_thread(self.chromecast.pause_or_resume)
            self.state = "paused"
            return
        if self.state == "paused":
            await asyncio.to_thread(self.chromecast.pause_or_resume)
            self.state = "playing"
            return
        if not self.library.items:
            return
        idx = self._cursor_index()
        start = 0 if idx is None else idx
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            return
        self.library.cursor_video_id = self.library.items[new_idx].video_id
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

        Marks the current item as done, advances the cursor to the next
        playable item (skipping encoding/queued items), and casts it.
        Transitions to idle if no playable item is reachable.
        """
        idx = self._cursor_index()
        if idx is None:
            return
        self.library.items[idx].status = "done"
        self.library.items[idx].playback_position = 0.0

        n = len(self.library.items)
        start = idx + 1
        if start >= n:
            if not self.library.loop_mode:
                self.state = "idle"
                return
            start = 0
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            self.state = "idle"
            return
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        await self._cast_current()

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
        # Defensive: clear any stale "FINISHED" flag from the previous item so
        # the natural-end watcher doesn't fire immediately for the new cast.
        self.chromecast.reset_playback_ended()
        await asyncio.to_thread(
            self.chromecast.cast_url,
            url,
            start_position=item.playback_position,
        )
        self.state = "casting"
        item.status = "playing"
        log.info("Casting %s (resume from %.1fs)", item.video_id, item.playback_position)

    async def watch_natural_end(self) -> None:
        """Background loop: when the Chromecast reports natural end of the
        current item (idle_reason == 'FINISHED'), advance the cursor and autoplay.

        Daemon spawns this as a long-running task. Does not return until
        cancelled via task.cancel()."""
        while True:
            await self.chromecast.wait_for_playback_end()
            self.chromecast.reset_playback_ended()
            try:
                await self.on_playback_finished()
            except Exception:
                log.exception("on_playback_finished failed")

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
