from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

import pychromecast
from pychromecast.controllers.media import MediaStatusListener

import config

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
        listener = StatusListener(self._on_media_status)
        self.cast.media_controller.register_status_listener(listener)
        self._notify_connection()
        return True

    async def discover_loop(self) -> None:
        while not self.connected:
            log.info("Searching for Chromecast '%s'...", config.CHROMECAST_NAME)
            found = await self.discover()
            if not found:
                await asyncio.sleep(10)

    def _on_media_status(self, status) -> None:
        previous = self.player_state
        self._previous_state = previous
        self.player_state = status.player_state or "UNKNOWN"
        self.current_time = status.current_time or 0.0
        self.duration = status.duration or 0.0
        if status.volume_level is not None:
            self.volume = status.volume_level
        # Only treat IDLE as "playback ended" when the chromecast reports
        # idle_reason=FINISHED. Other reasons (CANCELLED, INTERRUPTED, ERROR)
        # fire during media-to-media transitions and would falsely end the
        # next item's playback if processed after reset_playback_ended().
        idle_reason = getattr(status, "idle_reason", None)
        if (
            self.player_state == "IDLE"
            and previous in ("PLAYING", "BUFFERING")
            and idle_reason == "FINISHED"
        ):
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
        try:
            mc.update_status()
        except Exception as e:
            log.debug("update_status failed: %s", e)
        if mc.status:
            status = mc.status
            self.player_state = status.player_state or "UNKNOWN"
            self.current_time = status.current_time or 0.0
            self.duration = status.duration or 0.0
            if status.volume_level is not None:
                self.volume = status.volume_level

    def cast_url(self, url: str, start_position: float = 0.0) -> None:
        if not self.cast:
            raise RuntimeError("Chromecast not connected")
        mc = self.cast.media_controller
        mc.play_media(url, "video/mp4", current_time=start_position)
        mc.block_until_active()

    def _safe_cmd(self, fn: object) -> None:
        try:
            fn()
        except Exception as e:
            log.warning("Chromecast command failed: %s", e)

    def stop(self) -> None:
        if self.cast:
            self._safe_cmd(self.cast.media_controller.stop)

    def pause(self) -> None:
        if self.cast:
            self._safe_cmd(self.cast.media_controller.pause)

    def resume(self) -> None:
        if self.cast:
            self._safe_cmd(self.cast.media_controller.play)

    def pause_or_resume(self) -> None:
        if self.player_state == "PAUSED":
            self.resume()
        elif self.player_state == "PLAYING":
            self.pause()

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

    def adjust_volume(self, delta: int) -> None:
        if not self.cast:
            return
        new_vol = max(0.0, min(1.0, self.volume + delta / 100.0))
        self._safe_cmd(lambda: self.cast.set_volume(new_vol))
        self.volume = new_vol

    def shutdown(self) -> None:
        if self.browser:
            self.browser.stop_discovery()
