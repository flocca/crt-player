from __future__ import annotations

import asyncio
import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
)

import config
from chromecast_mgr import ChromecastManager
from pipeline import PipelineWorker
from queue_manager import ACTIVE_STATUSES, QueueItem, QueueManager


def _format_time(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class NowPlayingWidget(Static):
    title = reactive("", layout=True)
    status = reactive("", layout=True)
    error_msg = reactive("", layout=True)

    def render(self) -> str:
        if not self.title:
            return "  No video playing"

        max_title = max(self.size.width - 6, 10)
        title = self.title if len(self.title) <= max_title else self.title[:max_title - 1] + "…"

        if self.status == "error":
            return f'  "{title}"\n  ERROR: {self.error_msg}'
        if self.status == "casting":
            return f'  "{title}"\n  Connessione al Chromecast...'
        return f'  "{title}"'


class QueueListItem(ListItem):
    def __init__(self, item: QueueItem, index: int) -> None:
        super().__init__()
        self.queue_item = item
        self.index = index

    def compose(self) -> ComposeResult:
        title = self.queue_item.title or self.queue_item.url
        status = self.queue_item.status
        if status in ("casting", "playing"):
            prefix = "[green]▶[/green]"
        elif status in ("downloading", "encoding"):
            prefix = "[yellow]↓[/yellow]"
        elif status == "ready":
            prefix = "[cyan]✓[/cyan]"
        elif status == "error":
            prefix = "[red]✕[/red]"
        elif status == "done":
            prefix = "[dim]✓[/dim]"
        else:
            prefix = " "
        if status in ("downloading", "encoding"):
            pct = self.queue_item.progress
            bar_width = 12
            filled = int(pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            label = "DL" if status == "downloading" else "ENC"
            suffix = f"  [yellow]{bar} {label} {pct:.0f}%[/yellow]"
        else:
            suffix = ""
        yield Label(f"  {prefix} {self.index + 1}. {title}{suffix}")


class CRTCastApp(App):
    CSS = """
    Screen {
        background: $surface;
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
    #now-playing-header {
        text-style: bold;
        margin: 0 1;
    }
    #now-playing-section {
        margin: 1 2;
        border: solid $accent;
        height: auto;
    }
    #now-playing {
        padding: 0 1;
        height: auto;
    }
    #playback-row {
        height: 1;
        padding: 0 1;
        display: none;
    }
    #np-progress {
        width: 1fr;
    }
    #playback-row Button {
        min-width: 6;
        height: 1;
        border: none;
        background: transparent;
        color: $text;
        padding: 0 1;
    }
    #playback-row Button:hover {
        background: $accent;
    }
    #playback-row Button:disabled {
        color: $text-disabled;
    }
    #queue-header {
        text-style: bold;
        margin: 0 1;
    }
    #queue-list {
        height: 1fr;
        border: solid $accent;
        margin: 1 2;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "stop", "Stop", show=True, priority=True),
        Binding("ctrl+p", "pause", "Pause", show=True, priority=True),
        Binding("ctrl+left", "seek_back", "↺15s", show=True, priority=True),
        Binding("ctrl+right", "seek_forward", "↻30s", show=True, priority=True),
        Binding("ctrl+n", "next_video", "Next", show=True, priority=True),
        Binding("ctrl+b", "prev_video", "Prev", show=True, priority=True),
        Binding("plus,equal", "volume_up", "Vol+", show=True, priority=True),
        Binding("minus", "volume_down", "Vol-", show=True, priority=True),
        Binding("backspace", "remove_item", "Remove", show=True),
        Binding("ctrl+k", "move_up", "Up", show=True, priority=True),
        Binding("ctrl+j", "move_down", "Down", show=True, priority=True),
        Binding("escape", "quit", "Quit", show=True, priority=True),
    ]

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
        with Vertical(id="now-playing-section"):
            yield NowPlayingWidget(id="now-playing")
            with Horizontal(id="playback-row"):
                yield Static("", id="np-progress")
                yield Button("⏮", id="btn-prev", disabled=True)
                yield Button("↺15", id="btn-back")
                yield Button("⏸", id="btn-pause")
                yield Button("30↻", id="btn-fwd")
                yield Button("⏭", id="btn-next", disabled=True)
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
        asyncio.create_task(self.pipeline.run_prepare())
        asyncio.create_task(self.pipeline.run_cast())
        self.set_interval(1, self._poll_playback)
        self.set_interval(60, self._auto_save)
        if self.queue.next_pending():
            self.pipeline.wake()
            self._refresh_all()

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

    def _auto_save(self) -> None:
        self.queue.save_state(config.STATE_FILE, playback_position=self.chromecast.current_time)

    def _poll_playback(self) -> None:
        if self.chromecast.connected:
            asyncio.create_task(self._poll_playback_async())

    async def _poll_playback_async(self) -> None:
        await asyncio.to_thread(self.chromecast.poll_status)
        self._update_playback()

    def _update_playback(self) -> None:
        state = self.chromecast.player_state
        pos = self.chromecast.current_time
        dur = self.chromecast.duration

        state_icon = "▶" if state == "PLAYING" else "⏸"
        progress_widget = self.query_one("#np-progress", Static)
        if dur > 0:
            pos_str = _format_time(pos)
            dur_str = _format_time(dur)
            left = f"  {state_icon} {pos_str} / {dur_str}  "
            avail = max(5, progress_widget.size.width - len(left))
            filled = int(min(pos / dur, 1.0) * avail)
            bar = "█" * filled + "░" * (avail - filled)
            progress_widget.update(left + bar)
        else:
            progress_widget.update(f"  {state_icon}  --:-- / --:--")

        self.query_one("#btn-pause", Button).label = "⏸" if state == "PLAYING" else "▶"

    def _refresh_all(self) -> None:
        error_item = None
        for item in reversed(self.queue.items):
            if item.status == "error":
                error_item = item
                break

        active = self.queue.active_item()
        widget = self.query_one("#now-playing", NowPlayingWidget)
        show = active or error_item
        is_playing = bool(active and active.status == "playing")

        if show:
            widget.title = show.title or show.url
            widget.status = show.status
            widget.error_msg = show.error or ""
        else:
            widget.title = ""
            widget.status = ""
            widget.error_msg = ""

        playback_row = self.query_one("#playback-row")
        playback_row.display = is_playing
        if is_playing:
            self.query_one("#btn-prev", Button).disabled = not bool(self.queue.history)
            self.query_one("#btn-next", Button).disabled = self.queue.next_pending() is None

        self._refresh_queue_list()

    def _refresh_queue_list(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        list_view.clear()
        for i, item in enumerate(self.queue.items):
            list_view.append(QueueListItem(item, i))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        mode_select = self.query_one("#mode-select", Select)
        mode = str(mode_select.value)

        if mode == "now":
            self.pipeline.cancel_cast()
            self.pipeline.cancel_prepare()

        self.queue.add(url, mode=mode)
        self.pipeline.wake()
        event.input.value = ""
        self._refresh_all()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-prev":
            self.action_prev_video()
        elif btn_id == "btn-back":
            await self.action_seek_back()
        elif btn_id == "btn-pause":
            await self.action_pause()
        elif btn_id == "btn-fwd":
            await self.action_seek_forward()
        elif btn_id == "btn-next":
            self.action_next_video()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, QueueListItem):
            return
        target = event.item.queue_item
        if target.status not in ("queued", "ready"):
            return
        active = self.queue.active_item()
        if active:
            if active.status == "playing":
                active.playback_position = self.chromecast.current_time
            active.status = "done"
        self.pipeline.resume_position = target.playback_position
        self.pipeline.cancel_cast()
        if target.status == "queued":
            self.pipeline.cancel_prepare()
        self.queue.move_to_front(target.id)
        self.pipeline.wake()
        self._refresh_all()

    async def action_stop(self) -> None:
        self.pipeline.cancel_cast()
        await asyncio.to_thread(self.chromecast.stop)

    async def action_pause(self) -> None:
        await asyncio.to_thread(self.chromecast.pause_or_resume)

    def action_volume_up(self) -> None:
        self.chromecast.adjust_volume(10)

    def action_volume_down(self) -> None:
        self.chromecast.adjust_volume(-10)

    async def action_remove_item(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is None:
            return
        if not isinstance(list_view.highlighted_child, QueueListItem):
            return
        queue_item = list_view.highlighted_child.queue_item
        if queue_item.status in ("downloading", "encoding"):
            self.pipeline.cancel_prepare()
        elif queue_item.status in ("casting", "playing"):
            self.pipeline.cancel_cast()
            if queue_item.status == "playing":
                await asyncio.to_thread(self.chromecast.stop)
        if queue_item.filename:
            try:
                os.unlink(os.path.join(config.TEMP_DIR, queue_item.filename))
            except OSError:
                pass
        self.queue.remove(queue_item.id)
        self._refresh_all()

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

    async def action_seek_back(self) -> None:
        await asyncio.to_thread(self.chromecast.seek, -15)

    async def action_seek_forward(self) -> None:
        await asyncio.to_thread(self.chromecast.seek, 30)

    def action_next_video(self) -> None:
        self.pipeline.cancel_cast()

    def action_prev_video(self) -> None:
        prev = self.queue.pop_from_history()
        if prev is None:
            return
        active = self.queue.active_item()
        if active:
            active.status = "done"
        self.pipeline.cancel_cast()
        self.pipeline.cancel_prepare()
        new_item = self.queue.add(prev.url, mode="now")
        new_item.title = prev.title
        self.pipeline.wake()
        self._refresh_all()
