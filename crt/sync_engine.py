from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from crt import config
from crt.library_store import LibraryStore, QueueItem
from crt.youtube_client import PlaylistEntry, YouTubeAuthError, YouTubeClient

log = logging.getLogger(__name__)


class SyncEngine:
    """Polls a YouTube playlist and reflects its state into LibraryStore.

    YT is master of content and order (decisions C1+D1 in the design):
    - new YT items → added to library as `queued`
    - YT removals → removed from library, cache files deleted
    - YT order changes → library reordered to match
    - never writes back to YT
    """

    def __init__(
        self,
        library: LibraryStore,
        yt_client: YouTubeClient,
        playlist_id: str,
        on_remove=None,
    ):
        """on_remove: optional callback(video_id) invoked BEFORE an item is removed.
        Lets PlayerCore stop playback if the removed item is currently playing."""
        self.library = library
        self.yt = yt_client
        self.playlist_id = playlist_id
        self._on_remove = on_remove
        self.last_sync_at: str | None = None
        self.last_error: str | None = None
        self.state: str = "ok"
        self._kick: asyncio.Event | None = None  # created lazily in run_loop

    def run_sync_once(self) -> None:
        """Fetch playlist snapshot and apply diff to library. Synchronous."""
        try:
            snapshot = self.yt.list_playlist_items(self.playlist_id)
        except YouTubeAuthError as e:
            self.state = "degraded"
            self.last_error = str(e)
            log.error("Sync failed (auth): %s", e)
            return
        except Exception as e:
            self.state = "degraded"
            self.last_error = str(e)
            log.exception("Sync failed: %s", e)
            return

        self._apply_diff(snapshot)
        self.state = "ok"
        self.last_error = None
        self.last_sync_at = datetime.now(timezone.utc).isoformat()

    def _apply_diff(self, snapshot: list[PlaylistEntry]) -> None:
        snapshot_ids = [e.video_id for e in snapshot]
        snapshot_set = set(snapshot_ids)
        current_ids = {item.video_id for item in self.library.items}

        # Remove items no longer in snapshot
        removed_ids = current_ids - snapshot_set
        for video_id in removed_ids:
            self._remove_item(video_id)

        # Add new items
        existing_after_remove = {item.video_id for item in self.library.items}
        for entry in snapshot:
            if entry.video_id not in existing_after_remove:
                self._add_item(entry)

        # Reorder to match snapshot order
        items_by_id = {item.video_id: item for item in self.library.items}
        self.library.items = [items_by_id[vid] for vid in snapshot_ids if vid in items_by_id]

    def _add_item(self, entry: PlaylistEntry) -> None:
        url = f"https://www.youtube.com/watch?v={entry.video_id}"
        item = QueueItem(url=url, video_id=entry.video_id, title=entry.title)
        self.library.items.append(item)
        log.info("Added: %s (%s)", entry.title, entry.video_id)

    def _remove_item(self, video_id: str) -> None:
        item = next((i for i in self.library.items if i.video_id == video_id), None)
        if item is None:
            return

        if self._on_remove is not None:
            try:
                self._on_remove(video_id)
            except Exception:
                log.exception("on_remove callback failed for %s", video_id)

        self._delete_cache_files(item)

        self.library.items = [i for i in self.library.items if i.video_id != video_id]
        if self.library.cursor_video_id == video_id:
            self.library.cursor_video_id = None
        log.info("Removed: %s", video_id)

    async def run_loop(self, interval_s: int = 300, initial_delay_s: int = 10) -> None:
        """Periodic sync. Cancellable via task.cancel()."""
        self._kick = asyncio.Event()
        await asyncio.sleep(initial_delay_s)
        backoff = 0
        while True:
            await asyncio.to_thread(self.run_sync_once)
            if self.state == "degraded":
                backoff = min((backoff or 30) * 2, 1800)  # 30s → 60s → ... → 30m
                wait = backoff
            else:
                backoff = 0
                wait = interval_s
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=wait)
                self._kick.clear()
            except asyncio.TimeoutError:
                pass

    def kick(self) -> None:
        """Force the next iteration to run immediately."""
        if self._kick is not None:
            self._kick.set()

    def _delete_cache_files(self, item: QueueItem) -> None:
        if item.filename:
            encoded = os.path.join(config.TEMP_DIR, item.filename)
            if os.path.isfile(encoded):
                try:
                    os.unlink(encoded)
                    log.debug("Deleted encoded: %s", encoded)
                except OSError as e:
                    log.warning("Failed to delete encoded %s: %s", encoded, e)
        if item.downloaded_path and os.path.isfile(item.downloaded_path):
            try:
                os.unlink(item.downloaded_path)
                log.debug("Deleted download: %s", item.downloaded_path)
            except OSError as e:
                log.warning("Failed to delete download %s: %s", item.downloaded_path, e)
