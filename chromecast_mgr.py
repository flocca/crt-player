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

    def set_status_callback(self, callback: Callable) -> None:
        self._on_status_change = callback

    def set_connection_callback(self, callback: Callable) -> None:
        self._on_connection_change = callback

    async def discover(self) -> bool:
        return await asyncio.to_thread(self._discover_sync)

    def _discover_sync(self) -> bool:
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
        self._previous_state = self.player_state
        self.player_state = status.player_state or "UNKNOWN"
        self.current_time = status.current_time or 0.0
        self.duration = status.duration or 0.0
        if status.volume_level is not None:
            self.volume = status.volume_level
        if self._on_status_change:
            self._on_status_change()

    def _notify_connection(self) -> None:
        if self._on_connection_change:
            self._on_connection_change()

    @property
    def playback_ended(self) -> bool:
        return (
            self.player_state == "IDLE"
            and self._previous_state in ("PLAYING", "BUFFERING")
        )

    def cast_url(self, url: str) -> None:
        if not self.cast:
            raise RuntimeError("Chromecast not connected")
        mc = self.cast.media_controller
        mc.play_media(url, "video/mp4")
        mc.block_until_active()

    def stop(self) -> None:
        if self.cast:
            self.cast.media_controller.stop()

    def pause(self) -> None:
        if self.cast:
            self.cast.media_controller.pause()

    def resume(self) -> None:
        if self.cast:
            self.cast.media_controller.play()

    def pause_or_resume(self) -> None:
        if self.player_state == "PAUSED":
            self.resume()
        elif self.player_state == "PLAYING":
            self.pause()

    def adjust_volume(self, delta: int) -> None:
        if not self.cast:
            return
        new_vol = max(0.0, min(1.0, self.volume + delta / 100.0))
        self.cast.set_volume(new_vol)
        self.volume = new_vol

    def shutdown(self) -> None:
        if self.browser:
            self.browser.stop_discovery()
