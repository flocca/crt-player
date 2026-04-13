from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Select,
    Static,
)

from chromecast_mgr import ChromecastManager
from pipeline import PipelineWorker
from queue_manager import QueueItem, QueueManager


class NowPlayingWidget(Static):
    title = reactive("")
    status = reactive("")
    progress = reactive(0.0)
    playback_position = reactive(0.0)
    playback_duration = reactive(0.0)
    player_state = reactive("")
    error_msg = reactive("")

    def render(self) -> str:
        if not self.title:
            return "  No video playing"

        lines = [f'  "{self.title}"']

        if self.status == "error":
            lines.append(f"  ERROR: {self.error_msg}")
            return "\n".join(lines)

        if self.status in ("downloading", "encoding"):
            bar_width = 30
            filled = int(self.progress / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            label = self.status.capitalize()
            lines.append(f"  {bar} {label} {self.progress:.0f}%")

        if self.status == "playing" and self.playback_duration > 0:
            pos = self._format_time(self.playback_position)
            dur = self._format_time(self.playback_duration)
            bar_width = 30
            frac = self.playback_position / self.playback_duration
            filled = int(frac * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            state_icon = "▶" if self.player_state == "PLAYING" else "⏸"
            lines.append(f"  {state_icon} {pos} / {dur}  {bar}")

        if self.status == "casting":
            lines.append("  Connecting to Chromecast...")

        return "\n".join(lines)

    @staticmethod
    def _format_time(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


class QueueListItem(ListItem):
    def __init__(self, item: QueueItem, index: int) -> None:
        super().__init__()
        self.queue_item = item
        self.index = index

    def compose(self) -> ComposeResult:
        yield Label(f"  {self.index + 1}. {self.queue_item.title or self.queue_item.url}")


class CRTCastApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #header-bar {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        content-align: center middle;
    }
    #chromecast-status {
        dock: right;
        width: auto;
        padding: 0 1;
    }
    #url-input {
        margin: 1 1 1 2;
        width: 1fr;
    }
    #mode-select {
        width: 22;
        margin: 1 2 1 0;
    }
    #input-row {
        height: 5;
        margin: 0 1;
    }
    #now-playing {
        height: auto;
        min-height: 5;
        margin: 1 2;
        border: solid $accent;
        padding: 0 1;
    }
    #now-playing-header {
        text-style: bold;
        margin: 0 1;
    }
    #queue-section {
        margin: 1 2;
        border: solid $accent;
        height: 1fr;
    }
    #queue-header {
        text-style: bold;
        margin: 0 1;
    }
    #queue-list {
        height: 1fr;
    }
    #controls-row {
        height: 1;
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("s", "stop", "Stop", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("plus,equal", "volume_up", "Vol+", show=True),
        Binding("minus", "volume_down", "Vol-", show=True),
        Binding("d", "remove_item", "Remove", show=True),
        Binding("k", "move_up", "Move Up", show=True),
        Binding("j", "move_down", "Move Down", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    chromecast_connected = reactive(False)
    chromecast_device = reactive("")

    def __init__(
        self,
        queue: QueueManager,
        pipeline: PipelineWorker,
        chromecast: ChromecastManager,
    ) -> None:
        super().__init__()
        self.queue = queue
        self.pipeline = pipeline
        self.chromecast = chromecast

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="input-row"):
            yield Input(placeholder="YouTube URL...", id="url-input")
            yield Select(
                [("Accoda", "queue"), ("Prossimo", "next"), ("Subito", "now")],
                value="queue",
                id="mode-select",
                allow_blank=False,
            )
        yield Static(" IN RIPRODUZIONE", id="now-playing-header")
        yield NowPlayingWidget(id="now-playing")
        yield Static(" CODA", id="queue-header")
        yield ListView(id="queue-list")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "CRT Cast"
        self.sub_title = "Disconnected"
        self.chromecast.set_status_callback(self._on_chromecast_status)
        self.chromecast.set_connection_callback(self._on_chromecast_connection)
        self.pipeline.set_update_callback(self._on_pipeline_update)
        asyncio.create_task(self.chromecast.discover_loop())
        asyncio.create_task(self.pipeline.run())

    def _on_chromecast_connection(self) -> None:
        self._safe_call(self._update_connection)

    def _update_connection(self) -> None:
        if self.chromecast.connected:
            self.sub_title = f"Chromecast: {self.chromecast.device_name} ●"
        else:
            self.sub_title = "Chromecast: Disconnected"

    def _safe_call(self, callback: object) -> None:
        try:
            self.call_from_thread(callback)
        except RuntimeError:
            callback()

    def _on_chromecast_status(self) -> None:
        self._safe_call(self._update_playback)

    def _on_pipeline_update(self) -> None:
        self._safe_call(self._refresh_all)

    def _update_playback(self) -> None:
        widget = self.query_one("#now-playing", NowPlayingWidget)
        widget.playback_position = self.chromecast.current_time
        widget.playback_duration = self.chromecast.duration
        widget.player_state = self.chromecast.player_state

    def _refresh_all(self) -> None:
        # Show the most recent error or the active item
        error_item = None
        for item in reversed(self.queue.items):
            if item.status == "error":
                error_item = item
                break

        active = self.queue.active_item()
        widget = self.query_one("#now-playing", NowPlayingWidget)
        show = active or error_item
        if show:
            widget.title = show.title or show.url
            widget.status = show.status
            widget.progress = show.progress
            widget.error_msg = show.error or ""
        else:
            widget.title = ""
            widget.status = ""
            widget.progress = 0.0
            widget.error_msg = ""

        self._refresh_queue_list()

    def _refresh_queue_list(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        list_view.clear()
        for i, item in enumerate(self.queue.items):
            if item.status == "queued":
                list_view.append(QueueListItem(item, i))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        mode_select = self.query_one("#mode-select", Select)
        mode = str(mode_select.value)

        if mode == "now":
            self.pipeline.cancel_current()

        self.queue.add(url, mode=mode)
        self.pipeline.wake()
        event.input.value = ""
        self._refresh_all()

    def action_stop(self) -> None:
        self.pipeline.cancel_current()
        self.chromecast.stop()

    def action_pause(self) -> None:
        self.chromecast.pause_or_resume()

    def action_volume_up(self) -> None:
        self.chromecast.adjust_volume(10)

    def action_volume_down(self) -> None:
        self.chromecast.adjust_volume(-10)

    def action_remove_item(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.remove(queue_item.id)
            self._refresh_queue_list()

    def action_move_up(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.move(queue_item.id, "up")
            self._refresh_queue_list()

    def action_move_down(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.move(queue_item.id, "down")
            self._refresh_queue_list()
