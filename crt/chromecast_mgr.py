from __future__ import annotations

import asyncio
import logging
from typing import Callable

import pychromecast
from pychromecast.config import APP_DASHCAST
from pychromecast.controllers.dashcast import DashCastController

from crt import config

log = logging.getLogger(__name__)


class ChromecastManager:
    """Discovery, connection and app lifecycle of the Chromecast.

    Playback itself happens in our receiver page (crt/receiver_page), which
    the daemon opens through DashCast and drives over ReceiverChannel; this
    class only launches and quits that app.
    """

    def __init__(self) -> None:
        self.cast: pychromecast.Chromecast | None = None
        self.browser: pychromecast.CastBrowser | None = None
        self.connected: bool = False
        self.device_name: str = ""
        self.volume: float = 1.0
        self._on_connection_change: Callable | None = None
        self._connected_event = asyncio.Event()
        self._dashcast: DashCastController | None = None

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
        self._dashcast = DashCastController()
        self.cast.register_handler(self._dashcast)
        self.device_name = self.cast.name
        self.connected = True
        self._connected_event.set()
        log.info(
            "Connected to Chromecast '%s' at %s (model=%s)",
            self.device_name, getattr(self.cast, "uri", "?"), self.cast.model_name,
        )
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

    def _notify_connection(self) -> None:
        if self._on_connection_change:
            self._on_connection_change()

    async def wait_for_connection(self) -> None:
        await self._connected_event.wait()

    def _safe_cmd(self, fn: object) -> None:
        try:
            fn()
        except Exception as e:
            log.warning("Chromecast command failed: %s", e)

    # ─── Receiver page through DashCast ────────────────────────────

    def launch_receiver(self, url: str) -> None:
        """Open DashCast on our page. Blocking (pychromecast I/O): call it via
        asyncio.to_thread. force=True makes the receiver navigate to the page
        instead of iframing it in DashCast's HTTPS shell (mixed content blocks
        an HTTP page there). Every call restarts the app and shows its logo."""
        if self.cast is None or self._dashcast is None:
            raise RuntimeError("Chromecast not connected")
        log.info("launch_receiver: url=%s pre_app_id=%s", url, self.cast.app_id)
        self._dashcast.load_url(url, force=True)

    @property
    def receiver_app_active(self) -> bool:
        return self.cast is not None and self.cast.app_id == APP_DASHCAST

    def quit_app(self) -> None:
        if self.cast is not None:
            log.info("chromecast: quit_app (app_id=%s)", self.cast.app_id)
            self._safe_cmd(self.cast.quit_app)

    def adjust_volume(self, delta: int) -> None:
        if not self.cast:
            return
        new_vol = max(0.0, min(1.0, self.volume + delta / 100.0))
        self._safe_cmd(lambda: self.cast.set_volume(new_vol))
        self.volume = new_vol

    def shutdown(self) -> None:
        if self.browser:
            self.browser.stop_discovery()
