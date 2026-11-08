"""PlayerCore — transport layer for the headless sync daemon.

Manages cursor navigation (next/prev/play), playback through the receiver
page (opened on the Chromecast via DashCast, driven over ReceiverChannel),
the pause menu, and autoplay/loop on playback completion. Does not download
or encode — it consumes items that are already in status='ready' with a
filename set.

States: idle | playing | paused | calibrating. While paused the TV shows the
pause menu (PauseMenu) and next/prev/toggle/seek are reinterpreted for it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Callable

from googleapiclient.errors import HttpError

from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore, QueueItem
from crt.pause_menu import PauseMenu
from crt.receiver import ReceiverChannel
from crt.youtube_client import YouTubeAuthError

log = logging.getLogger(__name__)

# Seconds to wait for the page to connect after launching DashCast (in the
# background: commands answer at once with reason "launching").
RECEIVER_CONNECT_TIMEOUT_S = 20.0
# Seconds a menu notice stays on screen.
NOTICE_S = 2.0
# Stays until a key: the page cannot play the loaded file.
MEDIA_ERROR_NOTICE = "Errore nel video"
# Calibration asked before the startup render finished, or after it failed.
CALIBRATION_NOT_READY_NOTICE = "Calibrazione non pronta"
# Seconds the calibration pattern stays without a key press.
CALIBRATION_TIMEOUT_S = 60.0
# Seconds after the page's last event-stream subscriber leaves before the
# page counts as lost (a short blip reconnects within this window).
RECEIVER_LOST_GRACE_S = 10.0
# Seconds before the end where a seek stops: at exactly `duration` the
# element resumes from 0 instead of ending.
SEEK_END_MARGIN_S = 0.5


@dataclass
class ActionResult:
    """Outcome of a transport command, so callers (api.py → bridge / TUI) can
    distinguish "action performed" from "silent no-op" (issue #6).

    reason is one of: None (action performed), "no_items", "no_playable_item",
    "cursor_unchanged", "no_chromecast_session", or "launching" (performed;
    the page is opening in the background and will pick it up).
    """

    did_action: bool
    reason: str | None = None


# Convenience singletons for the common no-op outcomes.
_NO_ITEMS = ActionResult(False, "no_items")
_NO_PLAYABLE = ActionResult(False, "no_playable_item")
_CURSOR_UNCHANGED = ActionResult(False, "cursor_unchanged")
_NO_CC_SESSION = ActionResult(False, "no_chromecast_session")
_DID_ACTION = ActionResult(True)
_LAUNCHING = ActionResult(True, "launching")


def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class PlayerCore:
    # Path of the calibration clip once prepare_calibration rendered it.
    _calibration_clip: str | None = None

    def __init__(
        self,
        library: LibraryStore,
        chromecast: ChromecastManager,
        receiver: ReceiverChannel,
        youtube_client=None,
        on_cache_miss: Callable[[], None] | None = None,
        on_sync_request: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.library = library
        self.chromecast = chromecast
        self.receiver = receiver
        self.youtube = youtube_client
        # Wired by the daemon to sync_engine.kick (menu command "Sync now").
        self.on_sync_request = on_sync_request
        self._clock = clock
        self.menu: PauseMenu | None = None
        self._notice_handle: asyncio.TimerHandle | None = None
        # Invoked when an item's encoded file is found missing on disk and the
        # item is re-queued (issue #10). Wired to pipeline.wake_prepare so the
        # prepare loop re-downloads/re-encodes it immediately.
        self._on_cache_miss = on_cache_miss
        # When the player last became idle/paused (None while playing or
        # calibrating, and after the idle close): drives watch_idle.
        self._inactive_since: float | None = clock()
        self._state: str = "idle"
        self._calibration_timer: asyncio.TimerHandle | None = None
        self._calibration_return: str = "idle"
        self._calibration_url: str | None = None
        self._lost_task: asyncio.Task | None = None
        # The video the page has loaded (None after blank or when the page is
        # gone). The cursor can move before the page changes video (sync
        # removal, a pick during a relaunch), so position and stop decisions
        # follow this, not the cursor.
        self._loaded_video_id: str | None = None
        self._launch_task: asyncio.Task | None = None
        # The page reported a media error on the loaded video: resuming it
        # reloads the file instead of sending `play` to a broken element.
        self._load_failed = False
        self._last_menu_view: dict | None = None
        receiver.set_callbacks(
            self._on_receiver_connect, self._on_receiver_disconnect,
            on_media_error=self._on_media_error,
        )

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        if value in ("idle", "paused"):
            if value != self._state:
                self._inactive_since = self._clock()
        else:
            self._inactive_since = None
        self._state = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _requeue_missing_cache(self, item: QueueItem) -> None:
        """Demote an item whose encoded file vanished from disk so the prepare
        loop re-downloads/re-encodes it, then wake the pipeline (issue #10).

        Casting a stale filename 404s and leaves the Chromecast IDLE forever;
        re-queueing is the only way out.
        """
        log.warning(
            "Cache file for %s missing on disk (status=%s) — re-queueing for encode",
            item.video_id, item.status,
        )
        item.requeue()
        if self._on_cache_miss is not None:
            self._on_cache_miss()

    def _index_of(self, video_id: str) -> int | None:
        for i, item in enumerate(self.library.items):
            if item.video_id == video_id:
                return i
        return None

    def _cursor_index(self) -> int | None:
        if self.library.cursor_video_id is None:
            return None
        return self._index_of(self.library.cursor_video_id)

    def _media_url(self, filename: str) -> str:
        return f"http://{_get_local_ip()}:{config.SERVER_PORT}/media/{filename}"

    def _snapshot_position(self, item: QueueItem) -> None:
        """Copy the page's live position onto the item (resume point). Only
        the video the page has loaded owns that position."""
        if item.video_id != self._loaded_video_id:
            return
        live = self.receiver.live_position
        item.playback_position = live or item.playback_position or 0.0

    def _loaded_item(self) -> QueueItem | None:
        if self._loaded_video_id is None:
            return None
        return next(
            (i for i in self.library.items if i.video_id == self._loaded_video_id), None,
        )

    def _send_menu(self, view: dict | None = None) -> None:
        if self.menu is None:
            return
        if view is None:
            view = self.menu.view(self.library.items, self.library.cursor_video_id)
        self._last_menu_view = view
        self.receiver.send({"type": "menu", "view": view})

    def refresh_menu(self) -> None:
        """Redraw the open menu if the library changed under it (encode
        progress, a finished download, a sync). Wired by the daemon to the
        pipeline's updates and the end of each sync; must run on the loop."""
        if self.menu is None:
            return
        view = self.menu.view(self.library.items, self.library.cursor_video_id)
        if view != self._last_menu_view:
            self._send_menu(view)

    def _video_ids(self) -> list[str]:
        return [i.video_id for i in self.library.items]

    def live_position(self) -> float:
        """Where playback is now, for /status and the periodic save."""
        if (
            self.state in ("playing", "paused")
            and self._loaded_video_id is not None
            and self._loaded_video_id == self.library.cursor_video_id
        ):
            return self.receiver.live_position or 0.0
        return 0.0

    def _page_ready(self) -> bool:
        return self.receiver.connected and self.chromecast.receiver_app_active

    def _start_launch(self) -> None:
        """Open our page on the Chromecast in the background, once.

        Launching DashCast restarts the app (logo on screen), so it only
        happens when the page is not connected or another app is in front.
        Commands keep changing the player's state meanwhile; the new page
        gets that state when it subscribes (_on_receiver_connect)."""
        if self._launch_task is not None and not self._launch_task.done():
            return
        self._launch_task = asyncio.get_running_loop().create_task(self._launch())

    async def _launch(self) -> None:
        # Restored "ready" items can be played before discovery completes.
        await self.chromecast.wait_for_connection()
        url = f"http://{_get_local_ip()}:{config.SERVER_PORT}/receiver"
        seen = self.receiver.subscriptions
        try:
            await asyncio.to_thread(self.chromecast.launch_receiver, url)
        except Exception as e:
            log.error("Launching the receiver page failed: %s", e)
            self._drop_page("launch failed")
            return
        # Wait for a NEW connection: a stale subscriber left over from a page
        # that another app replaced must not count.
        if not await self.receiver.wait_new_subscriber(seen, RECEIVER_CONNECT_TIMEOUT_S):
            log.error(
                "Receiver page did not connect within %.0fs of launching %s",
                RECEIVER_CONNECT_TIMEOUT_S, url,
            )
            self._drop_page("page never connected")

    def _find_playable_index(self, *, start_idx: int, direction: int, wrap: bool) -> int | None:
        """Scan items starting at start_idx in `direction` (+1 forward, -1 backward),
        returning the index of the first item whose encoded file exists on disk.

        If `wrap` is True, continues from the opposite end after crossing the
        boundary, stopping when it would revisit start_idx. Returns None if no
        playable item exists in the scanned range.

        A stale filename (set on the item but the MP4 is gone from disk) is NOT
        a cache hit (issue #10) — such an item is demoted to queued so the
        prepare loop re-encodes it, and the scan continues past it.

        Used by next/prev/toggle/on_playback_finished to skip items that are
        still encoding or queued — the remote should land only on cast-ready items.
        """
        n = len(self.library.items)
        if n == 0 or start_idx < 0 or start_idx >= n:
            return None
        idx = start_idx
        for _ in range(n):
            item = self.library.items[idx]
            if item.cache_file_present():
                return idx
            if item.filename:
                self._requeue_missing_cache(item)
            idx += direction
            if idx < 0 or idx >= n:
                if not wrap:
                    return None
                idx %= n
        return None

    # ------------------------------------------------------------------
    # Transport commands
    # ------------------------------------------------------------------

    async def next(self) -> ActionResult:
        """Advance cursor to the next playable item (with loop wrap if enabled) then cast.

        Skips items still encoding/queued — the remote should never land on
        an item that can't be cast. Returns an ActionResult describing the
        outcome (issue #6). While paused it moves the menu highlight instead.
        """
        if self.state == "calibrating":
            await self._end_calibration()
            return _DID_ACTION
        if self.state == "paused" and self.menu is not None:
            return self._move_menu(+1)
        if not self.library.items:
            return _NO_ITEMS
        n = len(self.library.items)
        idx = self._cursor_index()
        if idx is None:
            start = 0
        else:
            start = idx + 1
            if start >= n:
                if not self.library.loop_mode:
                    return _NO_PLAYABLE
                start = 0
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            return _NO_PLAYABLE
        return self._cast(self.library.items[new_idx])

    async def prev(self) -> ActionResult:
        """Move cursor back to the previous playable item then cast.

        Skips items still encoding/queued. No wrap. Recovers from a stale /
        phantom cursor (a video_id no longer in the library) by scanning
        backward from the last item — symmetric to next()/toggle() (issue #5).
        While paused it moves the menu highlight instead.
        """
        if self.state == "calibrating":
            await self._end_calibration()
            return _DID_ACTION
        if self.state == "paused" and self.menu is not None:
            return self._move_menu(-1)
        if not self.library.items:
            return _NO_ITEMS
        idx = self._cursor_index()
        if idx is None:
            # Phantom cursor — jump to the last item and scan backward.
            start = len(self.library.items) - 1
        elif idx == 0:
            return _CURSOR_UNCHANGED
        else:
            start = idx - 1
        new_idx = self._find_playable_index(
            start_idx=start, direction=-1, wrap=False,
        )
        if new_idx is None:
            return _NO_PLAYABLE
        return self._cast(self.library.items[new_idx])

    async def play(self, video_id: str) -> ActionResult:
        """Jump cursor to a specific video_id and cast.

        Raises KeyError if video_id is not in the library.
        """
        idx = self._index_of(video_id)
        if idx is None:
            raise KeyError(f"video_id not in library: {video_id}")
        return self._cast(self.library.items[idx])

    def _move_menu(self, delta: int) -> ActionResult:
        self.menu.move(self._video_ids(), delta)
        self._send_menu()
        return _DID_ACTION

    async def toggle(self) -> ActionResult:
        """playing→pause (menu opens), paused→resume, idle→start nearest
        playable item.

        From idle, scans forward from the cursor (inclusive) for the first item
        with a cache file and casts it. If the cursor lands on an unready item
        (encoding/queued), the remote's toggle skips ahead instead of no-op.
        """
        if self.state == "calibrating":
            await self._end_calibration()
            return _DID_ACTION
        if self.state == "playing":
            self._pause()
            return _DID_ACTION
        if self.state == "paused":
            if self.menu is not None:
                return await self._menu_select()
            return await self._resume()
        if not self.library.items:
            return _NO_ITEMS
        idx = self._cursor_index()
        start = 0 if idx is None else idx
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            return _NO_PLAYABLE
        return self._cast(self.library.items[new_idx])

    def _pause(self, open_menu: bool = True) -> None:
        """Pause the page, save the position on the loaded video, open the
        menu on it."""
        item = self._loaded_item()
        if item is not None:
            self._snapshot_position(item)
            item.status = "paused"
        self.receiver.send({"type": "pause"})
        self.state = "paused"
        if open_menu:
            self.menu = PauseMenu(
                item.video_id if item is not None else (self.library.cursor_video_id or ""),
            )
            self._send_menu()
        log.info(
            "Paused %s at %.1fs",
            item.video_id if item else None,
            item.playback_position if item else 0.0,
        )

    async def _menu_select(self) -> ActionResult:
        """OK while the pause menu is open: act on the highlighted entry."""
        menu = self.menu
        entry = menu.selected
        if entry.kind == "video":
            if entry.key == self.library.cursor_video_id:
                return await self._resume()
            item = next((i for i in self.library.items if i.video_id == entry.key), None)
            if item is None or not item.cache_file_present():
                if item is not None and item.filename:
                    self._requeue_missing_cache(item)
                self._flash_notice("non ancora pronto")
                return _NO_PLAYABLE
            return self._cast(item)

        key = entry.key
        if key == "stop":
            return await self.stop()
        if key == "delete":
            # Always the paused (cursor) video, never the highlighted row, and
            # only after a second OK. Any other key clears the confirm.
            if not menu.confirming_delete:
                menu.confirming_delete = True
                self._send_menu()
                return _DID_ACTION
            paused_id = menu.current_video_id
            if paused_id != self.library.cursor_video_id or (
                self._loaded_video_id is not None and paused_id != self._loaded_video_id
            ):
                # The playlist moved under the menu (sync): never delete a
                # video other than the one that was paused.
                log.warning(
                    "Delete refused: paused %s, cursor %s, loaded %s",
                    paused_id, self.library.cursor_video_id, self._loaded_video_id,
                )
                menu.confirming_delete = False
                self._send_menu()
                return _NO_PLAYABLE
            self.menu = None
            await self.delete_current()
            return _DID_ACTION
        if key == "calibrate":
            await self.calibrate()
            return _DID_ACTION
        if key == "loop":
            self.library.loop_mode = not self.library.loop_mode
            self._flash_notice("Loop attivo" if self.library.loop_mode else "Loop disattivato")
            return _DID_ACTION
        if key == "sync":
            if self.on_sync_request is None:
                self._flash_notice("Sync non disponibile")
                return _DID_ACTION
            self.on_sync_request()
            self._flash_notice("Sync avviato")
            return _DID_ACTION
        log.warning("Unknown menu command %s", key)
        return _NO_PLAYABLE

    def _flash_notice(self, text: str) -> None:
        """Show `text` under the menu commands for NOTICE_S seconds. A newer
        notice cancels the pending clear of the older one."""
        if self.menu is None:
            return
        if self._notice_handle is not None:
            self._notice_handle.cancel()
        menu = self.menu
        menu.notice = text
        self._send_menu()

        def _clear() -> None:
            self._notice_handle = None
            # The menu may have closed or been replaced in the meantime.
            if self.menu is menu and menu.notice == text:
                menu.notice = None
                self._send_menu()

        self._notice_handle = asyncio.get_running_loop().call_later(NOTICE_S, _clear)

    async def _resume(self) -> ActionResult:
        """Resume the paused video. If the page is gone (another app took the
        Chromecast, or it is reconnecting), or it could not play the file,
        load the video again at the saved position instead of sending `play`
        to nobody or to a broken element."""
        item = self._loaded_item() or self.library.cursor_item()
        if not self._page_ready() or self._load_failed:
            if item is None:
                return _NO_PLAYABLE
            log.info("Resume by reloading %s", item.video_id)
            return self._cast(item)
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({"type": "play"})
        if item is not None:
            item.status = "playing"
        self.menu = None
        self.state = "playing"
        return _DID_ACTION

    async def stop(self) -> ActionResult:
        """Stop playback and set state to idle. Cursor stays on the current item;
        its status reverts to 'ready' so a subsequent toggle/play re-casts it
        from where it left off.

        The live position is snapshotted onto the item first (issue #13):
        without it, resume relied solely on the 30s periodic save, which can be
        stale by up to a full interval. The page stays open on a black screen
        so the next play starts without the DashCast logo.
        """
        item = self._loaded_item() or self.library.cursor_item()
        if item is not None and item.status in ("playing", "paused"):
            self._snapshot_position(item)
            item.status = "ready"
        self._blank()
        self.state = "idle"
        return _DID_ACTION

    def _blank(self) -> None:
        self._cancel_calibration_timer()
        self.menu = None
        self._loaded_video_id = None
        if self.receiver.connected:
            self.receiver.send({"type": "menu", "view": None})
            self.receiver.send({"type": "blank"})

    async def stop_and_remove(self, video_id: str) -> None:
        """If video_id is the currently playing/paused item, stop it.

        The caller is responsible for actually removing the item from the
        library after this returns.
        """
        # The sync thread may already have moved the cursor off the removed
        # video (this runs later, on the loop): the page still shows it.
        on_screen = video_id in (self._loaded_video_id, self.library.cursor_video_id)
        if on_screen and self.state in ("playing", "paused", "calibrating"):
            self._blank()
            self.state = "idle"

    async def seek_relative(self, seconds: int) -> ActionResult:
        """Seek the page by `seconds` from the live position, clamped to
        [0, duration] (no upper clamp while the duration is unknown). While
        paused the new position is also the resume point, and the menu is
        re-sent so its time line updates."""
        if self.state == "calibrating":
            await self._end_calibration()
            return _DID_ACTION
        if self.state not in ("playing", "paused"):
            return _NO_CC_SESSION
        item = self._loaded_item()
        current = self.receiver.live_position
        if current is None:
            current = item.playback_position if item is not None else 0.0
        new = max(0.0, current + seconds)
        if self.receiver.duration > 0:
            new = min(new, max(self.receiver.duration - SEEK_END_MARGIN_S, 0.0))
        self.receiver.send({"type": "seek", "position": new})
        if self.state == "paused":
            if item is not None:
                item.playback_position = new
            if self.menu is not None:
                self.menu.confirming_delete = False
                self._send_menu()
        return _DID_ACTION

    async def delete_current(self) -> None:
        item = self.library.cursor_item()
        if item is None:
            log.info("delete_current: no cursor item, skipping")
            return
        if self.state in ("playing", "paused", "calibrating"):
            await self.stop()
        self.menu = None
        await asyncio.to_thread(self._delete_local, item)
        if item.playlist_item_id and self.youtube is not None:
            try:
                await asyncio.to_thread(self.youtube.delete_playlist_item, item.playlist_item_id)
            except (YouTubeAuthError, HttpError, OSError) as e:
                log.error("YouTube remote delete failed for %s: %s", item.video_id, e)
        elif self.youtube is None:
            log.warning("delete_current: no youtube_client, remote delete skipped")
        else:
            log.warning(
                "delete_current: playlist_item_id missing for %s; remote delete skipped",
                item.video_id,
            )

    def _delete_local(self, item) -> None:
        self.library.remove(item.id)
        encoded = config.cached_encoded_filename(item.video_id)
        for name in (encoded, config.preview_filename(encoded)):
            cache_path = os.path.join(config.TEMP_DIR, name)
            if os.path.isfile(cache_path):
                try:
                    os.unlink(cache_path)
                except OSError as e:
                    log.warning("Failed to unlink cache %s: %s", cache_path, e)

    async def on_playback_finished(self) -> None:
        """Called when the Chromecast reports natural end of the current item.

        Marks the current item as done, advances the cursor to the next
        playable item (skipping encoding/queued items), and casts it.
        Transitions to idle if no playable item is reachable.
        """
        if self._loaded_video_id is not None:
            idx = self._index_of(self._loaded_video_id)
        else:
            idx = self._cursor_index()
        if idx is None:
            return
        self.library.items[idx].status = "done"
        self.library.items[idx].playback_position = 0.0

        n = len(self.library.items)
        start = idx + 1
        if start >= n:
            if not self.library.loop_mode:
                self._end_of_playlist()
                return
            start = 0
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            self._end_of_playlist()
            return
        # The found item validated as cache-present, but if its file vanished in
        # the find→cast window (TOCTOU) _cast re-queues and casts nothing
        # — go idle rather than leave a phantom "playing" state.
        if not self._cast(self.library.items[new_idx]).did_action:
            self._end_of_playlist()

    def _end_of_playlist(self) -> None:
        """Nothing left to play: black screen, like after Stop."""
        self._blank()
        self.state = "idle"

    # ------------------------------------------------------------------
    # Cast helpers
    # ------------------------------------------------------------------

    def _cast(self, item: QueueItem) -> ActionResult:
        """Load `item` on the page and make it the cursor.

        Never waits: if the page is not open, the state is set anyway and the
        page is launched in the background (reason "launching"); it loads the
        video when it connects. The cursor moves only when a video is loaded,
        so a failed pick leaves the menu and the pause on the video on screen.
        Returns _NO_PLAYABLE when the encoded file is missing (issue #6).
        """
        if not item.cache_file_present():
            if item.filename:
                # Stale filename — encoded file gone from disk (issue #10).
                # Re-queue so the pipeline rebuilds it instead of casting a
                # dead URL that 404s.
                self._requeue_missing_cache(item)
            else:
                log.info(
                    "Item %s has no cache file (status=%s); waiting",
                    item.video_id,
                    item.status,
                )
            return _NO_PLAYABLE

        # Save where the outgoing video is (next/prev while playing, a pick
        # from the menu).
        outgoing = self._loaded_item()
        if outgoing is not None and outgoing.status in ("playing", "paused"):
            self._snapshot_position(outgoing)

        if self.state == "calibrating":
            self._cancel_calibration_timer()
            self.receiver.send({"type": "calibration", "url": None})

        # Any item currently labelled playing/casting/paused but that's no longer
        # the cursor is stale — revert to "ready" so a future cursor move back
        # doesn't trip the "already playing" guard.
        for other in self.library.items:
            if other is not item and other.status in ("playing", "casting", "paused"):
                other.status = "ready"

        # Clear a stale "ended" from the previous item so the natural-end
        # watcher doesn't fire immediately for the new one.
        self.receiver.reset_ended()
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({
            "type": "load",
            "url": self._media_url(item.filename),
            "position": item.playback_position,
            "autoplay": True,
        })
        self._loaded_video_id = item.video_id
        self.library.cursor_video_id = item.video_id
        self._load_failed = False
        self.menu = None
        item.status = "playing"
        self.state = "playing"
        if not self._page_ready():
            # Nobody plays this load until the new page connects.
            self.receiver.freeze()
            self._start_launch()
            log.info("Playing %s once the page opens (from %.1fs)", item.video_id, item.playback_position)
            return _LAUNCHING
        log.info("Playing %s (resume from %.1fs)", item.video_id, item.playback_position)
        return _DID_ACTION

    async def watch_natural_end(self) -> None:
        """Background loop: when the page reports the natural end of the
        loaded video, advance the cursor and autoplay. Only acts while
        playing (a late `ended` during pause or calibration is ignored; one
        for a previous video is already filtered by ReceiverChannel).

        Daemon spawns this as a long-running task. Does not return until
        cancelled via task.cancel()."""
        while True:
            src = await self.receiver.wait_ended()
            self.receiver.reset_ended()
            if src != self.receiver.expected_src:
                # A newer load came in between the report and this wake-up.
                log.info("Ignoring natural end of %s: %s loaded since", src, self.receiver.expected_src)
                continue
            if self.state != "playing":
                log.info("Ignoring natural end while %s", self.state)
                continue
            try:
                await self.on_playback_finished()
            except Exception:
                log.exception("on_playback_finished failed")

    # ------------------------------------------------------------------
    # Calibration (a page state, not a playlist item)
    # ------------------------------------------------------------------

    async def prepare_calibration(self) -> None:
        """Render the calibration clip once; the daemon runs this in the
        background at startup (the margins it draws do not change while the
        daemon runs). Until it is done, or if ffmpeg fails, calibrate() only
        says the pattern is not ready."""
        from crt import calibration

        out_path = os.path.join(config.TEMP_DIR, "calibration_pattern.mp4")
        try:
            await calibration.generate_calibration_clip(out_path)
        except Exception:
            log.exception("Rendering the calibration pattern failed")
            return
        self._calibration_clip = out_path
        log.info("Calibration pattern ready: %s", out_path)

    async def calibrate(self) -> None:
        """Show the calibration pattern inside the page.

        Never waits: the clip is rendered at startup (prepare_calibration).
        A video that is playing is paused first with its position saved. The
        pattern stays until any key (toggle/next/prev/seek) or for
        CALIBRATION_TIMEOUT_S, then the player goes back to the paused menu on
        the same video (or to a black screen if nothing was playing). Its end
        never marks a video done and its time never touches a resume position.
        """
        if self.state == "calibrating":
            return
        if self._calibration_clip is None:
            log.warning("Calibration requested but the pattern is not ready")
            if self.menu is not None:
                self._flash_notice(CALIBRATION_NOT_READY_NOTICE)
            return
        if self.state == "playing":
            self._pause(open_menu=False)
        self._calibration_return = "paused" if self.state == "paused" else "idle"
        self.menu = None
        self._calibration_url = self._media_url(os.path.basename(self._calibration_clip))
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({"type": "calibration", "url": self._calibration_url})
        self.state = "calibrating"
        self._cancel_calibration_timer()
        self._calibration_timer = asyncio.get_running_loop().call_later(
            CALIBRATION_TIMEOUT_S,
            lambda: asyncio.ensure_future(self._end_calibration()),
        )
        if not self._page_ready():
            self._start_launch()
        log.info("Calibration pattern shown (returns to %s)", self._calibration_return)

    def _cancel_calibration_timer(self) -> None:
        if self._calibration_timer is not None:
            self._calibration_timer.cancel()
            self._calibration_timer = None

    async def _end_calibration(self) -> None:
        self._cancel_calibration_timer()
        if self.state != "calibrating":
            return
        self.receiver.send({"type": "calibration", "url": None})
        item = self._loaded_item()
        if self._calibration_return == "paused" and item is not None:
            self.state = "paused"
            self.menu = PauseMenu(item.video_id)
            if self._load_failed:
                self.menu.notice = MEDIA_ERROR_NOTICE
            self._send_menu()
        else:
            self._blank()
            self.state = "idle"
        log.info("Calibration ended (now %s)", self.state)

    # ------------------------------------------------------------------
    # Page connection: blips, loss, idle close
    # ------------------------------------------------------------------

    def _on_receiver_connect(self) -> None:
        """A page subscribed (first connection, a reconnect after a blip, a
        page just launched, or a replacement while the old connection is
        still listed). Every new page starts empty, so it gets the player's
        whole state: the loaded video at its position, paused with the menu
        or playing, the calibration pattern, or a black screen."""
        if self._lost_task is not None:
            self._lost_task.cancel()
            self._lost_task = None
        item = self._loaded_item()
        if self.state == "calibrating":
            if self._calibration_return == "paused" and item is not None and item.filename:
                # The video under the pattern, so ending calibration lands
                # on a paused frame that OK can resume.
                self.receiver.send({
                    "type": "load",
                    "url": self._media_url(item.filename),
                    "position": item.playback_position,
                    "autoplay": False,
                })
            self.receiver.send({"type": "calibration", "url": self._calibration_url})
            return
        if self.state in ("playing", "paused") and item is not None and item.filename:
            self._snapshot_position(item)
            self.receiver.send({
                "type": "load",
                "url": self._media_url(item.filename),
                "position": item.playback_position,
                "autoplay": self.state == "playing",
            })
            if self.state == "paused" and self.menu is not None:
                self._send_menu()
            else:
                self.receiver.send({"type": "menu", "view": None})
            log.info("Receiver page connected: restored %s while %s", item.video_id, self.state)
            return
        # Idle (a Stop the page could not hear, or nothing played yet).
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({"type": "blank"})

    def _on_receiver_disconnect(self) -> None:
        if self._lost_task is not None:
            self._lost_task.cancel()
        self._lost_task = asyncio.get_running_loop().create_task(self._lost_after_grace())

    async def _lost_after_grace(self) -> None:
        await asyncio.sleep(RECEIVER_LOST_GRACE_S)
        if self._launch_task is not None and not self._launch_task.done():
            # A launch is under way: judge the page once it is over. A page
            # that connects meanwhile cancels this task.
            await asyncio.wait({self._launch_task})
        self._lost_task = None
        if self.receiver.connected:
            return
        self._drop_page(f"page lost for {RECEIVER_LOST_GRACE_S:.0f}s")

    def _drop_page(self, why: str) -> None:
        """No page to play on (lost past the grace, or a launch that failed):
        go idle with the position saved, so the next play resumes there."""
        if self.state not in ("playing", "paused", "calibrating"):
            return
        item = self._loaded_item()
        if item is not None and item.status in ("playing", "paused"):
            self._snapshot_position(item)
            item.status = "ready"
        self._cancel_calibration_timer()
        self.menu = None
        self._loaded_video_id = None
        self.state = "idle"
        log.warning(
            "Receiver page unavailable (%s): player idle (%s saved at %.1fs)",
            why,
            item.video_id if item else None,
            item.playback_position if item else 0.0,
        )

    def _on_media_error(self) -> None:
        """The page cannot play the loaded file (corrupt, or gone): pause on
        it with the menu and a notice, so the user can pick another video,
        stop, or retry with OK (which reloads it). Under the calibration
        pattern the failure is only recorded; the menu shows it on return."""
        self._load_failed = True
        if self.state == "playing":
            self._pause()
        if self.menu is not None:
            self.menu.notice = MEDIA_ERROR_NOTICE
            self._send_menu()

    async def watch_idle(self, close_after_s: float, poll_s: float = 30.0) -> None:
        """Background loop: after `close_after_s` without playback (idle or
        paused), save the position and close the DashCast app. The next play
        reopens it (logo) and resumes from the saved position."""
        while True:
            await asyncio.sleep(poll_s)
            try:
                await self._close_if_idle(close_after_s)
            except Exception:
                log.exception("idle close check failed")

    async def _close_if_idle(self, close_after_s: float) -> None:
        if self.state not in ("idle", "paused") or self._inactive_since is None:
            return
        if self._clock() - self._inactive_since < close_after_s:
            return
        if not (self.receiver.connected or self.chromecast.receiver_app_active):
            return
        if self.state == "paused":
            item = self._loaded_item()
            if item is not None:
                self._snapshot_position(item)
                item.status = "ready"
        self.menu = None
        self._loaded_video_id = None
        self.state = "idle"
        log.info("No playback for %.0fs: closing the receiver app", close_after_s)
        await asyncio.to_thread(self.chromecast.quit_app)
        self._inactive_since = None
