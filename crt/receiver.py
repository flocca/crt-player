"""ReceiverChannel — daemon side of the link with the receiver page.

The page (crt/receiver_page/index.html, opened on the Chromecast through
DashCast) subscribes to Server-Sent Events from GET /receiver/events and
reports back with POST /receiver/report. This module keeps the subscriber
queues and a local model of the page's playback (position, duration, paused)
so the daemon can answer "where are we now" without a round trip.

Messages daemon→page: config{margins}, load{url, position, autoplay}, play{},
pause{}, seek{position}, blank{}, menu{view}, calibration{url}.
Reports page→daemon: {event: hello|status|ended|error, position, duration,
paused, src, error}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Callable

log = logging.getLogger(__name__)


class ReceiverChannel:
    def __init__(
        self,
        margins: dict[str, int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.margins = dict(margins)
        self._clock = clock
        self._subscribers: list[asyncio.Queue] = []
        self._on_connect: Callable[[], None] | None = None
        self._on_disconnect: Callable[[], None] | None = None
        self._connected_event = asyncio.Event()
        self._ended_event = asyncio.Event()
        # Local playback model. _position is None until something is loaded
        # or the page reports.
        self._expected_src: str | None = None
        self._position: float | None = None
        self._timestamp: float = 0.0
        self.duration: float = 0.0
        self.paused: bool = True

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return bool(self._subscribers)

    def set_callbacks(
        self,
        on_connect: Callable[[], None] | None,
        on_disconnect: Callable[[], None] | None,
    ) -> None:
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait({"type": "config", "margins": dict(self.margins)})
        was_connected = self.connected
        self._subscribers.append(q)
        self._connected_event.set()
        if not was_connected:
            log.info("Receiver page connected")
            if self._on_connect is not None:
                self._on_connect()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q not in self._subscribers:
            return
        self._subscribers.remove(q)
        if not self._subscribers:
            self._connected_event.clear()
            log.info("Receiver page disconnected")
            if self._on_disconnect is not None:
                self._on_disconnect()

    async def wait_connected(self, timeout_s: float) -> bool:
        if self.connected:
            return True
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout_s)
        except asyncio.TimeoutError:
            return False
        return self.connected

    # ------------------------------------------------------------------
    # Daemon → page
    # ------------------------------------------------------------------

    def send(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "load":
            self._expected_src = msg["url"]
            self._position = float(msg["position"] or 0.0)
            self._timestamp = self._clock()
            self.paused = not msg["autoplay"]
            self.duration = 0.0
        elif kind == "seek":
            self._position = float(msg["position"])
            self._timestamp = self._clock()
        elif kind == "pause":
            self._position = self.live_position
            self._timestamp = self._clock()
            self.paused = True
        elif kind == "play":
            self._position = self.live_position
            self._timestamp = self._clock()
            self.paused = False
        for q in self._subscribers:
            q.put_nowait(msg)

    # ------------------------------------------------------------------
    # Page → daemon
    # ------------------------------------------------------------------

    def _src_matches(self, src) -> bool:
        return self._expected_src is None or src == self._expected_src

    def handle_report(self, report: dict) -> None:
        event = report.get("event")
        src = report.get("src")
        if event in ("status", "hello"):
            if not self._src_matches(src):
                return
            self._position = float(report.get("position") or 0.0)
            self.duration = float(report.get("duration") or 0.0)
            self.paused = bool(report.get("paused"))
            self._timestamp = self._clock()
        elif event == "ended":
            if self._expected_src is not None and src == self._expected_src:
                self._ended_event.set()
            else:
                log.info("Ignoring ended report for stale src %s", src)
        elif event == "error":
            log.warning("Receiver page error (src=%s): %s", src, report.get("error"))

    @property
    def live_position(self) -> float | None:
        if self._position is None:
            return None
        if self.paused:
            return self._position
        pos = self._position + (self._clock() - self._timestamp)
        if self.duration > 0:
            pos = min(pos, self.duration)
        return pos

    async def wait_ended(self) -> None:
        await self._ended_event.wait()

    def reset_ended(self) -> None:
        self._ended_event.clear()


async def sse_events(channel: ReceiverChannel, ping_s: float = 5.0) -> AsyncIterator[str]:
    """Subscribe to the channel and yield SSE frames; pings keep the link alive."""
    q = channel.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), ping_s)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
    finally:
        channel.unsubscribe(q)
