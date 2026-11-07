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

# Seconds to wait for the page to connect after launching DashCast.
RECEIVER_CONNECT_TIMEOUT_S = 20.0
# Seconds a menu notice stays on screen.
NOTICE_S = 2.0
# Seconds the calibration pattern stays without a key press.
CALIBRATION_TIMEOUT_S = 60.0
# Seconds after the page's last event-stream subscriber leaves before the
# page counts as lost (a short blip reconnects within this window).
RECEIVER_LOST_GRACE_S = 10.0


@dataclass
class ActionResult:
    """Outcome of a transport command, so callers (api.py → bridge / TUI) can
    distinguish "action performed" from "silent no-op" (issue #6).

    reason is one of: None (action performed), "no_items", "no_playable_item",
    "cursor_unchanged", "no_chromecast_session".
    """

    did_action: bool
    reason: str | None = None


# Convenience singletons for the common no-op outcomes.
_NO_ITEMS = ActionResult(False, "no_items")
_NO_PLAYABLE = ActionResult(False, "no_playable_item")
_CURSOR_UNCHANGED = ActionResult(False, "cursor_unchanged")
_NO_CC_SESSION = ActionResult(False, "no_chromecast_session")
_DID_ACTION = ActionResult(True)


def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class PlayerCore:
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
        receiver.set_callbacks(self._on_receiver_connect, self._on_receiver_disconnect)

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
        """Copy the page's live position onto the item (resume point)."""
        live = self.receiver.live_position
        item.playback_position = live or item.playback_position or 0.0

    def _send_menu(self) -> None:
        if self.menu is None:
            return
        self.receiver.send({
            "type": "menu",
            "view": self.menu.view(self.library.items, self.library.cursor_video_id),
        })

    def _video_ids(self) -> list[str]:
        return [i.video_id for i in self.library.items]

    def live_position(self) -> float:
        """Where playback is now, for /status and the periodic save."""
        if self.state in ("playing", "paused"):
            return self.receiver.live_position or 0.0
        return 0.0

    async def _ensure_receiver(self) -> bool:
        """Make sure our page is open on the Chromecast and connected.

        Launching DashCast restarts the app (logo on screen), so it only
        happens when the page is not connected or another app is in front.
        """
        if self.receiver.connected and self.chromecast.receiver_app_active:
            return True
        await self.chromecast.wait_for_connection()
        url = f"http://{_get_local_ip()}:{config.SERVER_PORT}/receiver"
        seen = self.receiver.subscriptions
        try:
            await asyncio.to_thread(self.chromecast.launch_receiver, url)
        except Exception as e:
            log.error("Launching the receiver page failed: %s", e)
            return False
        # Wait for a NEW connection: a stale subscriber left over from a page
        # that another app replaced must not count.
        if not await self.receiver.wait_new_subscriber(seen, RECEIVER_CONNECT_TIMEOUT_S):
            log.error(
                "Receiver page did not connect within %.0fs of launching %s",
                RECEIVER_CONNECT_TIMEOUT_S, url,
            )
            return False
        return True

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
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        if await self._cast_current():
            return _DID_ACTION
        return _NO_PLAYABLE

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
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        if await self._cast_current():
            return _DID_ACTION
        return _NO_PLAYABLE

    async def play(self, video_id: str) -> ActionResult:
        """Jump cursor to a specific video_id and cast.

        Raises KeyError if video_id is not in the library.
        """
        idx = self._index_of(video_id)
        if idx is None:
            raise KeyError(f"video_id not in library: {video_id}")
        self.library.cursor_video_id = video_id
        if await self._cast_current():
            return _DID_ACTION
        return _NO_PLAYABLE

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
            self._resume()
            return _DID_ACTION
        if not self.library.items:
            return _NO_ITEMS
        idx = self._cursor_index()
        start = 0 if idx is None else idx
        new_idx = self._find_playable_index(
            start_idx=start, direction=1, wrap=self.library.loop_mode,
        )
        if new_idx is None:
            return _NO_PLAYABLE
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        if await self._cast_current():
            return _DID_ACTION
        return _NO_PLAYABLE

    def _pause(self, open_menu: bool = True) -> None:
        """Pause the page, save the position on the cursor item, open the menu."""
        item = self.library.cursor_item()
        if item is not None:
            self._snapshot_position(item)
            item.status = "paused"
        self.receiver.send({"type": "pause"})
        self.state = "paused"
        if open_menu:
            self.menu = PauseMenu(self.library.cursor_video_id or "")
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
                self._resume()
                return _DID_ACTION
            item = next((i for i in self.library.items if i.video_id == entry.key), None)
            if item is None or not item.cache_file_present():
                if item is not None and item.filename:
                    self._requeue_missing_cache(item)
                self._flash_notice("non ancora pronto")
                return _NO_PLAYABLE
            self.library.cursor_video_id = item.video_id
            if await self._cast_current():
                return _DID_ACTION
            return _NO_PLAYABLE

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

    def _resume(self) -> None:
        item = self.library.cursor_item()
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({"type": "play"})
        if item is not None:
            item.status = "playing"
        self.menu = None
        self.state = "playing"

    async def stop(self) -> ActionResult:
        """Stop playback and set state to idle. Cursor stays on the current item;
        its status reverts to 'ready' so a subsequent toggle/play re-casts it
        from where it left off.

        The live position is snapshotted onto the item first (issue #13):
        without it, resume relied solely on the 30s periodic save, which can be
        stale by up to a full interval. The page stays open on a black screen
        so the next play starts without the DashCast logo.
        """
        item = self.library.cursor_item()
        if item is not None and item.status in ("playing", "paused"):
            self._snapshot_position(item)
            item.status = "ready"
        self._blank()
        self.state = "idle"
        return _DID_ACTION

    def _blank(self) -> None:
        self._cancel_calibration_timer()
        self.menu = None
        if self.receiver.connected:
            self.receiver.send({"type": "menu", "view": None})
            self.receiver.send({"type": "blank"})

    async def stop_and_remove(self, video_id: str) -> None:
        """If video_id is the currently playing/paused item, stop it.

        The caller is responsible for actually removing the item from the
        library after this returns.
        """
        if (
            self.library.cursor_video_id == video_id
            and self.state in ("playing", "paused", "calibrating")
        ):
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
        item = self.library.cursor_item()
        current = self.receiver.live_position
        if current is None:
            current = item.playback_position if item is not None else 0.0
        new = max(0.0, current + seconds)
        if self.receiver.duration > 0:
            new = min(new, self.receiver.duration)
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
        cache_path = os.path.join(config.TEMP_DIR, config.cached_encoded_filename(item.video_id))
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
        self.library.cursor_video_id = self.library.items[new_idx].video_id
        # The found item validated as cache-present, but if its file vanished in
        # the find→cast window (TOCTOU) _cast_current re-queues and casts nothing
        # — go idle rather than leave a phantom "playing" state.
        if not await self._cast_current():
            self._end_of_playlist()

    def _end_of_playlist(self) -> None:
        """Nothing left to play: black screen, like after Stop."""
        self._blank()
        self.state = "idle"

    # ------------------------------------------------------------------
    # Cast helpers
    # ------------------------------------------------------------------

    async def _cast_current(self) -> bool:
        """Cast the item at the current cursor position.

        Returns True if the item was cast, False if nothing was cast (no cursor,
        or the encoded file is missing). Callers translate False into an honest
        ActionResult (issue #6) instead of reporting a phantom success.
        """
        idx = self._cursor_index()
        if idx is None:
            return False
        item = self.library.items[idx]
        if not item.cache_file_present():
            if item.filename:
                # Stale filename — encoded file gone from disk (issue #10).
                # Re-queue so the pipeline rebuilds it instead of casting a
                # dead URL that 404s and leaves the Chromecast IDLE.
                self._requeue_missing_cache(item)
            else:
                log.info(
                    "Cursor item %s has no cache file (status=%s); waiting",
                    item.video_id,
                    item.status,
                )
            return False

        if not await self._ensure_receiver():
            log.warning("Cannot play %s: receiver page unavailable", item.video_id)
            return False

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
        self.menu = None
        item.status = "playing"
        self.state = "playing"
        log.info("Playing %s (resume from %.1fs)", item.video_id, item.playback_position)
        return True

    async def watch_natural_end(self) -> None:
        """Background loop: when the page reports the natural end of the
        loaded video, advance the cursor and autoplay. Only acts while
        playing (a late `ended` during pause or calibration is ignored; one
        for a previous video is already filtered by ReceiverChannel).

        Daemon spawns this as a long-running task. Does not return until
        cancelled via task.cancel()."""
        while True:
            await self.receiver.wait_ended()
            self.receiver.reset_ended()
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

    async def calibrate(self) -> None:
        """Show the calibration pattern inside the page.

        A video that is playing is paused first with its position saved. The
        pattern stays until any key (toggle/next/prev/seek) or for
        CALIBRATION_TIMEOUT_S, then the player goes back to the paused menu on
        the same video (or to a black screen if nothing was playing). Its end
        never marks a video done and its time never touches a resume position.

        The clip comes from calibration.generate_calibration_clip(out_path),
        an async coroutine that renders an MP4 via ffmpeg into TEMP_DIR, served
        by the media route.
        """
        from crt import calibration

        if self.state == "calibrating":
            return
        if not await self._ensure_receiver():
            log.warning("Calibration skipped: receiver page unavailable")
            return
        out_path = os.path.join(config.TEMP_DIR, "calibration_pattern.mp4")
        await calibration.generate_calibration_clip(out_path)
        # Re-read the state: ffmpeg took a while and a key may have arrived.
        if self.state == "playing":
            self._pause(open_menu=False)
        self._calibration_return = "paused" if self.state == "paused" else "idle"
        self.menu = None
        self._calibration_url = self._media_url(os.path.basename(out_path))
        self.receiver.send({"type": "menu", "view": None})
        self.receiver.send({"type": "calibration", "url": self._calibration_url})
        self.state = "calibrating"
        self._cancel_calibration_timer()
        self._calibration_timer = asyncio.get_running_loop().call_later(
            CALIBRATION_TIMEOUT_S,
            lambda: asyncio.ensure_future(self._end_calibration()),
        )
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
        item = self.library.cursor_item()
        if self._calibration_return == "paused" and item is not None:
            self.state = "paused"
            self.menu = PauseMenu(item.video_id)
            self._send_menu()
        else:
            self._blank()
            self.state = "idle"
        log.info("Calibration ended (now %s)", self.state)

    # ------------------------------------------------------------------
    # Page connection: blips, loss, idle close
    # ------------------------------------------------------------------

    def _on_receiver_connect(self) -> None:
        """The page (re)connected. After a short blip, put it back where it
        was: same video and position, paused with the menu or playing."""
        if self._lost_task is not None:
            self._lost_task.cancel()
            self._lost_task = None
        if self.state == "calibrating":
            item = self.library.cursor_item()
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
        item = self.library.cursor_item()
        if item is None or not item.filename:
            return
        if self.state in ("playing", "paused"):
            self._snapshot_position(item)
            self.receiver.send({
                "type": "load",
                "url": self._media_url(item.filename),
                "position": item.playback_position,
                "autoplay": self.state == "playing",
            })
            if self.state == "paused":
                self._send_menu()
            log.info("Receiver page reconnected: restored %s while %s", item.video_id, self.state)

    def _on_receiver_disconnect(self) -> None:
        if self._lost_task is not None:
            self._lost_task.cancel()
        self._lost_task = asyncio.get_running_loop().create_task(self._lost_after_grace())

    async def _lost_after_grace(self) -> None:
        await asyncio.sleep(RECEIVER_LOST_GRACE_S)
        self._lost_task = None
        if self.receiver.connected:
            return
        if self.state not in ("playing", "paused", "calibrating"):
            return
        item = self.library.cursor_item()
        if item is not None and item.status in ("playing", "paused"):
            self._snapshot_position(item)
            item.status = "ready"
        self._cancel_calibration_timer()
        self.menu = None
        self.state = "idle"
        log.warning(
            "Receiver page lost for %.0fs: player idle (%s saved at %.1fs)",
            RECEIVER_LOST_GRACE_S,
            item.video_id if item else None,
            item.playback_position if item else 0.0,
        )

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
            item = self.library.cursor_item()
            if item is not None:
                self._snapshot_position(item)
                item.status = "ready"
        self.menu = None
        self.state = "idle"
        log.info("No playback for %.0fs: closing the receiver app", close_after_s)
        await asyncio.to_thread(self.chromecast.quit_app)
        self._inactive_since = None
