"""ReceiverChannel — daemon side of the link with the receiver page.

The page (crt/receiver_page/index.html, opened on the Chromecast through
DashCast) subscribes to Server-Sent Events from GET /receiver/events and
reports back with POST /receiver/report. This module keeps the subscriber
queues and a local model of the page's playback (position, duration, paused)
so the daemon can answer "where are we now" without a round trip.

Messages daemon→page: config{margins}, load{url, position, autoplay}, play{},
pause{}, seek{position}, blank{}, screen{view}, calibration{url}.
`screen{view}` carries the whole teletext screen (Screen.view), or view null
to hide it; `blank{}` only unloads the main video and stops the calibration.
Reports page→daemon: {event: hello|status|ended|error, position, duration,
paused, src, error, media}; `media` is true on an error of the <video>
element itself (bad file), not on a script or play() error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Callable

log = logging.getLogger(__name__)

# Seconds the position may run ahead of the last report or command.
MAX_EXTRAPOLATION_S = 3.0


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
        self._on_media_error: Callable[[], None] | None = None
        self._connected_event = asyncio.Event()
        self._ended_event = asyncio.Event()
        self._ended_src: str | None = None
        # Total subscribe() calls ever: lets a caller that just launched the
        # page wait for a NEW connection, not a stale one still hanging on.
        self.subscriptions: int = 0
        self._subscribed_event = asyncio.Event()
        # Local playback model. _position is None until something is loaded
        # or the page reports.
        self._expected_src: str | None = None
        self._position: float | None = None
        self._timestamp: float = 0.0
        self.duration: float = 0.0
        self.paused: bool = True
        # True after the last subscriber left: nobody is playing anything we
        # can hear about, so the position stays where it was.
        self._frozen: bool = False

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
        on_media_error: Callable[[], None] | None = None,
    ) -> None:
        """on_connect fires on every new subscription, not only the first: a
        replacement page can subscribe before the old connection is gone, and
        it starts empty. on_disconnect fires when the last subscriber leaves.
        on_media_error fires on a media error for the loaded src."""
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_media_error = on_media_error

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait({"type": "config", "margins": dict(self.margins)})
        was_connected = self.connected
        self._subscribers.append(q)
        self.subscriptions += 1
        self._connected_event.set()
        self._subscribed_event.set()
        log.info("Receiver page connected (%s)", "another subscriber" if was_connected else "first")
        if self._on_connect is not None:
            self._on_connect()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q not in self._subscribers:
            return
        self._subscribers.remove(q)
        if not self._subscribers:
            if self._position is not None:
                self._position = self.live_position
                self._timestamp = self._clock()
            self._frozen = True
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

    async def wait_new_subscriber(self, after: int, timeout_s: float) -> bool:
        """Wait until `subscriptions` exceeds `after` (a value read earlier)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while self.subscriptions <= after:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            self._subscribed_event.clear()
            try:
                await asyncio.wait_for(self._subscribed_event.wait(), remaining)
            except asyncio.TimeoutError:
                return self.subscriptions > after
        return True

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
        if kind in ("load", "seek", "play"):
            # A command nobody receives doesn't start anything on the TV.
            self._frozen = not self._subscribers
        for q in self._subscribers:
            q.put_nowait(msg)

    def freeze(self) -> None:
        """Stop extrapolating until the page reports: nobody is playing the
        last load yet (a launch is under way; a subscriber still listed may
        be a page another app replaced)."""
        if self._position is not None:
            self._position = self.live_position
            self._timestamp = self._clock()
        self._frozen = True

    @property
    def expected_src(self) -> str | None:
        """The URL of the last load."""
        return self._expected_src

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
            self._frozen = False
        elif event == "ended":
            if self._expected_src is not None and src == self._expected_src:
                self._ended_src = src
                self._ended_event.set()
            else:
                log.info("Ignoring ended report for stale src %s", src)
        elif event == "error":
            log.warning("Receiver page error (src=%s): %s", src, report.get("error"))
            if (
                report.get("media")
                and self._expected_src is not None
                and src == self._expected_src
                and self._on_media_error is not None
            ):
                self._on_media_error()

    @property
    def live_position(self) -> float | None:
        if self._position is None:
            return None
        if self.paused or self._frozen:
            return self._position
        # The page reports every second while playing: past a few seconds of
        # silence it is stalled or gone, so stop running ahead of it.
        elapsed = min(self._clock() - self._timestamp, MAX_EXTRAPOLATION_S)
        pos = self._position + elapsed
        if self.duration > 0:
            pos = min(pos, self.duration)
        return pos

    async def wait_ended(self) -> str | None:
        """Wait for a natural end; returns the src that ended, which the
        caller compares with `expected_src` when it acts (a newer load may
        have come in between)."""
        await self._ended_event.wait()
        return self._ended_src

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
