from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

import pychromecast
from pychromecast.controllers.media import MediaStatusListener

from crt import config

log = logging.getLogger(__name__)


class StatusListener(MediaStatusListener):
    def __init__(self, callback: Callable) -> None:
        self._callback = callback

    def new_media_status(self, status) -> None:
        self._callback(status)

    def load_media_failed(self, item, error_code) -> None:
        log.error("Load media failed: item=%s error=%s", item, error_code)


class ChromecastManager:
    def __init__(self) -> None:
        self.cast: pychromecast.Chromecast | None = None
        self.browser: pychromecast.CastBrowser | None = None
        self.connected: bool = False
        self.device_name: str = ""
        self.player_state: str = "UNKNOWN"
        self.current_time: float = 0.0
        self.duration: float = 0.0
        self.volume: float = 1.0
        self._on_status_change: Callable | None = None
        self._on_connection_change: Callable | None = None
        self._previous_state: str = "UNKNOWN"
        self._playback_ended_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._last_logged_app_id: str | None = "__sentinel__"

    def set_status_callback(self, callback: Callable) -> None:
        self._on_status_change = callback

    def set_connection_callback(self, callback: Callable) -> None:
        self._on_connection_change = callback

    async def discover(self) -> bool:
        return await asyncio.to_thread(self._discover_sync)

    def _discover_sync(self) -> bool:
        if self.browser:
            self.browser.stop_discovery()
        chromecasts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[config.CHROMECAST_NAME]
        )
        self.browser = browser
        if not chromecasts:
            self.connected = False
            self._notify_connection()
            return False
        self.cast = chromecasts[0]
        self.cast.wait()
        self.device_name = self.cast.name
        self.connected = True
        self._connected_event.set()
        log.info(
            "Connected to Chromecast '%s' at %s (model=%s)",
            self.device_name, getattr(self.cast, "uri", "?"), self.cast.model_name,
        )
        listener = StatusListener(self._on_media_status)
        self.cast.media_controller.register_status_listener(listener)
        self._notify_connection()
        return True

    async def discover_loop(self) -> None:
        attempt = 1
        while not self.connected:
            log.info(
                "Searching for Chromecast '%s' (attempt %d)...",
                config.CHROMECAST_NAME, attempt,
            )
            found = await self.discover()
            if not found:
                log.warning(
                    "Chromecast '%s' not found; retrying in 10s",
                    config.CHROMECAST_NAME,
                )
                await asyncio.sleep(10)
                attempt += 1

    def _on_media_status(self, status) -> None:
        previous = self.player_state
        self._previous_state = previous
        self.player_state = status.player_state or "UNKNOWN"
        # Only trust current_time in real playback states. When the media
        # session is torn down (e.g. after a long pause), pychromecast reports
        # current_time=0 together with player_state=UNKNOWN/IDLE — clobbering
        # the real pause position we need for recast resume.
        if self.player_state in ("PLAYING", "PAUSED", "BUFFERING"):
            self.current_time = status.current_time or 0.0
            self.duration = status.duration or 0.0
        if status.volume_level is not None:
            self.volume = status.volume_level
        # Only treat IDLE as "playback ended" when the chromecast reports
        # idle_reason=FINISHED. Other reasons (CANCELLED, INTERRUPTED, ERROR)
        # fire during media-to-media transitions and would falsely end the
        # next item's playback if processed after reset_playback_ended().
        idle_reason = getattr(status, "idle_reason", None)
        if previous != self.player_state:
            app_id = self.cast.app_id if self.cast else None
            log.debug(
                "media status: %s -> %s (idle_reason=%s app_id=%s current_time=%.1f)",
                previous,
                self.player_state,
                idle_reason,
                app_id,
                self.current_time,
            )
        if (
            self.player_state == "IDLE"
            and previous in ("PLAYING", "BUFFERING")
            and idle_reason == "FINISHED"
        ):
            log.info("playback_ended_event SET (natural end)")
            self._playback_ended_event.set()
        if self._on_status_change:
            self._on_status_change()

    def _notify_connection(self) -> None:
        if self._on_connection_change:
            self._on_connection_change()

    async def wait_for_connection(self) -> None:
        await self._connected_event.wait()

    def reset_playback_ended(self) -> None:
        self._playback_ended_event.clear()

    async def wait_for_playback_end(self) -> None:
        await self._playback_ended_event.wait()

    def poll_status(self) -> None:
        if not self.cast:
            return
        mc = self.cast.media_controller
        prev_state = self.player_state
        prev_app = self._last_logged_app_id
        try:
            mc.update_status()
        except Exception as e:
            log.debug("update_status failed: %s", e)
        if mc.status:
            status = mc.status
            self.player_state = status.player_state or "UNKNOWN"
            # Same rationale as _on_media_status: keep the last known good
            # current_time when the session is torn down so recast can resume
            # from the actual pause position.
            if self.player_state in ("PLAYING", "PAUSED", "BUFFERING"):
                self.current_time = status.current_time or 0.0
                self.duration = status.duration or 0.0
            if status.volume_level is not None:
                self.volume = status.volume_level
        app_id = self.cast.app_id if self.cast else None
        if app_id != prev_app:
            log.info(
                "poll_status: app_id %s -> %s (player_state=%s is_idle=%s)",
                prev_app,
                app_id,
                self.player_state,
                self.cast.is_idle if self.cast else None,
            )
            self._last_logged_app_id = app_id
        elif prev_state != self.player_state:
            log.debug(
                "poll_status: player_state %s -> %s (app_id=%s)",
                prev_state,
                self.player_state,
                app_id,
            )

    def cast_url(self, url: str, start_position: float = 0.0) -> None:
        if not self.cast:
            raise RuntimeError("Chromecast not connected")
        log.info(
            "cast_url: url=%s start_position=%.1f pre_app_id=%s pre_state=%s",
            url,
            start_position,
            self.cast.app_id,
            self.player_state,
        )
        mc = self.cast.media_controller
        mc.play_media(url, "video/mp4", current_time=start_position)
        mc.block_until_active()
        log.info(
            "cast_url: block_until_active returned (app_id=%s player_state=%s)",
            self.cast.app_id,
            self.player_state,
        )

    def _safe_cmd(self, fn: object) -> None:
        try:
            fn()
        except Exception as e:
            log.warning("Chromecast command failed: %s", e)

    def stop(self) -> None:
        if self.cast:
            log.info("chromecast: send stop (app_id=%s state=%s)", self.cast.app_id, self.player_state)
            self._safe_cmd(self.cast.media_controller.stop)

    def pause(self) -> None:
        if self.cast:
            log.info("chromecast: send pause (app_id=%s state=%s)", self.cast.app_id, self.player_state)
            self._safe_cmd(self.cast.media_controller.pause)

    def resume(self) -> None:
        if self.cast:
            log.info("chromecast: send play/resume (app_id=%s state=%s)", self.cast.app_id, self.player_state)
            self._safe_cmd(self.cast.media_controller.play)

    def pause_or_resume(self) -> None:
        log.info("pause_or_resume: player_state=%s", self.player_state)
        if self.player_state == "PAUSED":
            self.resume()
        elif self.player_state == "PLAYING":
            self.pause()
        else:
            log.warning(
                "pause_or_resume: unexpected state=%s — neither pause nor play issued",
                self.player_state,
            )

    def is_session_lost(self) -> bool:
        """True when any active media session is gone and pause/play would
        be silent no-ops. Two windows to catch:

        1. app_id is None / IDLE_APP_ID (E8C28D3C): receiver unloaded, backdrop
           showing.
        2. app_id is back to CC1AD845 but the media controller carries no
           loaded media — happens after a long-pause timeout: pychromecast
           auto-relaunches the Default Media Receiver, so app_id recovers
           quickly, but player_state stays UNKNOWN (or IDLE) because nothing
           is loaded. Only a fresh play_media revives playback.
        """
        if not self.cast:
            log.info("is_session_lost: no cast object -> False")
            return False
        app_id = self.cast.app_id
        if app_id is None or app_id == pychromecast.IDLE_APP_ID:
            log.info(
                "is_session_lost: app_id=%s (backdrop/unloaded) -> True",
                app_id,
            )
            return True
        if self.player_state in ("UNKNOWN", "IDLE"):
            log.info(
                "is_session_lost: app_id=%s player_state=%s (receiver alive "
                "but no media loaded) -> True",
                app_id,
                self.player_state,
            )
            return True
        log.info(
            "is_session_lost: app_id=%s player_state=%s -> False",
            app_id,
            self.player_state,
        )
        return False

    def seek(self, delta: float) -> None:
        if not self.cast:
            return
        new_pos = max(0.0, self.current_time + delta)
        self._safe_cmd(lambda: self.cast.media_controller.seek(new_pos))
        self.current_time = new_pos

    def seek_to(self, position: float) -> None:
        if not self.cast:
            return
        self._safe_cmd(lambda: self.cast.media_controller.seek(position))
        self.current_time = position

    def seek_relative(self, delta_seconds: float) -> bool:
        """Seek by delta_seconds relative to the current position.

        Returns True if a seek was issued, False if there is no active session
        to seek within (no cast / no known position) — issue #6 needs this so
        the API can report a no-op.

        Before computing the target, refresh from a live status read: after a
        fresh play_media (e.g. session-loss recovery) the cached current_time
        may still be stale from the previous, torn-down session, which would
        make the seek land at stale_pos+delta instead of real_pos+delta
        (issue #7 seek drift). Only trust the live read in real playback
        states, mirroring _on_media_status / poll_status.
        """
        if not self.cast:
            return False
        try:
            mc = self.cast.media_controller
            mc.update_status()
            st = mc.status
            if (
                st is not None
                and st.player_state in ("PLAYING", "PAUSED", "BUFFERING")
                and st.current_time is not None
            ):
                self.current_time = st.current_time
        except Exception as e:
            log.debug("seek_relative: status refresh failed: %s", e)
        if self.current_time is None:
            log.debug("seek_relative: no current_time, skipping")
            return False
        new_pos = max(0.0, self.current_time + delta_seconds)
        self._safe_cmd(lambda: self.cast.media_controller.seek(new_pos))
        self.current_time = new_pos
        return True

    def adjust_volume(self, delta: int) -> None:
        if not self.cast:
            return
        new_vol = max(0.0, min(1.0, self.volume + delta / 100.0))
        self._safe_cmd(lambda: self.cast.set_volume(new_vol))
        self.volume = new_vol

    def shutdown(self) -> None:
        if self.browser:
            self.browser.stop_discovery()
