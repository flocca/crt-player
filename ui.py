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

import calibration
import config
from chromecast_mgr import ChromecastManager
from pipeline import PipelineWorker, get_local_ip
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

    def _compose_content(self) -> str:
        if not self.title:
            return "  Nessun video in riproduzione\n "

        max_title = max(self.size.width - 6, 10)
        title = self.title if len(self.title) <= max_title else self.title[:max_title - 1] + "…"

        if self.status == "error":
            return f'  "{title}"\n  [red]ERRORE: {self.error_msg}[/red]'
        if self.status == "casting":
            return f'  "{title}"\n  [dim]Connessione al Chromecast...[/dim]'
        return f'  "{title}"\n '

    def watch_title(self) -> None:
        self.update(self._compose_content())

    def watch_status(self) -> None:
        self.update(self._compose_content())

    def watch_error_msg(self) -> None:
        self.update(self._compose_content())

    def on_resize(self) -> None:
        # Re-render when width changes so long titles truncate correctly.
        self.update(self._compose_content())


class QueueListView(ListView):
    """ListView that highlights on click but does not select (Enter selects)."""

    _from_mouse: bool = False

    def _on_list_item__child_clicked(self, event: ListItem._ChildClicked) -> None:
        self._from_mouse = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._from_mouse:
            self._from_mouse = False
            event.stop()
            return
        # Keyboard-triggered: let the event bubble to the App.


class QueueListItem(ListItem):
    def __init__(self, item: QueueItem, index: int, can_up: bool = True, can_down: bool = True) -> None:
        super().__init__()
        self.queue_item = item
        self.index = index
        self._can_up = can_up
        self._can_down = can_down

    def _build_label(self) -> str:
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
        return f"  {prefix} {self.index + 1}. {title}{suffix}"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="queue-row"):
            yield Label(self._build_label(), classes="queue-title")
            with Horizontal(classes="queue-actions"):
                yield Button(
                    "↑",
                    id=f"up-{self.queue_item.id}",
                    classes="queue-action-btn",
                    disabled=not self._can_up,
                )
                yield Button(
                    "↓",
                    id=f"down-{self.queue_item.id}",
                    classes="queue-action-btn",
                    disabled=not self._can_down,
                )

    def refresh_label(self) -> None:
        self.query_one(".queue-title", Label).update(self._build_label())

    def update_buttons(self, can_up: bool, can_down: bool) -> None:
        self.query_one(f"#up-{self.queue_item.id}", Button).disabled = not can_up
        self.query_one(f"#down-{self.queue_item.id}", Button).disabled = not can_down


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
    .queue-row {
        height: 1;
    }
    .queue-title {
        width: 1fr;
    }
    .queue-actions {
        width: auto;
        height: 1;
    }
    .queue-action-btn {
        min-width: 3;
        height: 1;
        border: none;
        background: transparent;
        color: $text;
        padding: 0;
        margin: 0 0 0 1;
    }
    .queue-action-btn:hover {
        background: $accent;
    }
    .queue-action-btn:disabled {
        color: $text-disabled;
    }
    """

    BINDINGS = [
        Binding("enter", "play_selected", "Riproduci", show=True),
        Binding("ctrl+s", "stop", "Stop", show=True, priority=True),
        Binding("ctrl+p", "pause", "Pause", show=True, priority=True),
        Binding("ctrl+left", "seek_back", "↺15s", show=True, priority=True),
        Binding("ctrl+right", "seek_forward", "↻30s", show=True, priority=True),
        Binding("ctrl+n", "next_video", "Next", show=True, priority=True),
        Binding("ctrl+b", "prev_video", "Prev", show=True, priority=True),
        Binding("ctrl+t", "calibrate", "Calibra", show=True, priority=True),
        Binding("plus,equal", "volume_up", "Vol+", show=True, priority=True),
        Binding("minus", "volume_down", "Vol-", show=True, priority=True),
        Binding("backspace", "remove_item", "Rimuovi", show=True),
        Binding("ctrl+k", "move_up", "Su", show=True, priority=True),
        Binding("ctrl+j", "move_down", "Giù", show=True, priority=True),
        Binding("ctrl+r", "toggle_loop", "Loop", show=True, priority=True),
        Binding("escape", "quit", "Esci", show=True, priority=True),
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
        self._pending_display: QueueItem | None = None
        self.loop_mode: bool = config.LOOP_MODE_DEFAULT

    def compose(self) -> ComposeResult:
        yield Header()
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
        yield QueueListView(id="queue-list")
        with Horizontal(id="input-row"):
            yield Input(placeholder="YouTube URL...", id="url-input")
            yield Select(
                [("Accoda", "queue"), ("Prossimo", "next"), ("Subito", "now")],
                value="queue",
                id="mode-select",
                allow_blank=False,
            )
        yield Footer()

    def _highlighted_status(self) -> str | None:
        """Status of the currently highlighted queue item, or None."""
        lv = self.query_one("#queue-list", ListView)
        if isinstance(lv.highlighted_child, QueueListItem):
            return lv.highlighted_child.queue_item.status
        return None

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        playing = bool(self.queue.active_item() and self.queue.active_item().status in ("casting", "playing"))
        sel = self._highlighted_status()
        has_sel = sel is not None
        if action == "play_selected":
            return True if sel in ("queued", "ready", "done", "error") else False
        if action in ("stop", "pause", "seek_back", "seek_forward", "next_video", "prev_video"):
            return True if playing else False
        if action in ("remove_item", "move_up", "move_down"):
            return True if has_sel else False
        if action == "calibrate":
            return True
        return True

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.refresh_bindings()

    def action_play_selected(self) -> None:
        """No-op: real logic lives in on_list_view_selected (fired by Enter on QueueListView)."""

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
        if self.queue.items:
            self._refresh_all()
            list_view = self.query_one("#queue-list", ListView)
            list_view.focus()
            list_view.index = 0
            if self.queue.next_pending():
                self.pipeline.wake_prepare()
        else:
            self.query_one("#url-input", Input).focus()
        self._refresh_loop_indicator()

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
        playing = next((i for i in self.queue.items if i.status == "playing"), None)
        if playing:
            playing.playback_position = self.chromecast.current_time
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
        playing_item = next(
            (i for i in self.queue.items if i.status in ("casting", "playing")), None
        )
        # Only clear the pending hint once the item is actually playing, not just
        # casting. If cast_url fails the item goes to "error" and playing_item
        # becomes None; keeping _pending_display prevents a blank NowPlaying flash.
        if playing_item and playing_item.status == "playing":
            self._pending_display = None
        # Discard pending hint if the item was removed from the queue.
        if self._pending_display and not any(
            i.id == self._pending_display.id for i in self.queue.items
        ):
            self._pending_display = None

        show = playing_item or self._pending_display
        # During automatic transitions (current item just ended, next not yet casting),
        # keep showing the first ready item in the queue instead of going blank.
        if not show:
            show = self.queue.first_ready()

        widget = self.query_one("#now-playing", NowPlayingWidget)
        is_playing = bool(playing_item and playing_item.status == "playing")

        if show:
            widget.title = show.title or show.url
            if playing_item:
                widget.status = playing_item.status
            elif show.status == "error":
                widget.status = "error"
                widget.error_msg = show.error or ""
            else:
                widget.status = "casting"
                widget.error_msg = ""
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
        existing = list(list_view.query(QueueListItem))
        queue_ids = [item.id for item in self.queue.items]
        existing_ids = [li.queue_item.id for li in existing]

        n = len(self.queue.items)
        if queue_ids == existing_ids:
            for i, (li, item) in enumerate(zip(existing, self.queue.items)):
                li.queue_item = item
                li.index = i
                li.refresh_label()
                li.update_buttons(can_up=i > 0, can_down=i < n - 1)
            return

        prev_index = list_view.index
        had_focus = list_view.has_focus
        list_view.clear()
        for i, item in enumerate(self.queue.items):
            list_view.append(QueueListItem(
                item, i,
                can_up=i > 0,
                can_down=i < n - 1,
            ))
        if self.queue.items:
            list_view.index = min(prev_index or 0, n - 1)
        if had_focus:
            list_view.focus()

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
        btn_id = event.button.id or ""
        # Queue reorder buttons (↑/↓ on each row)
        if btn_id.startswith("up-") or btn_id.startswith("down-"):
            direction, _, item_id = btn_id.partition("-")
            if self.queue.move(item_id, direction):
                self._refresh_queue_list()
                self.pipeline.wake_prepare()
            event.stop()
            return
        # Playback control buttons
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
        if target.status not in ("queued", "ready", "done", "error"):
            return
        self.queue.prepare_for_play(target)
        # Only interrupt actual playback; leave downloading/encoding untouched.
        # playback_position is already kept current by _poll_playback_async.
        # Must search directly — active_item() could return target itself (now "ready")
        # if it sits before the active item. Must include "casting": if the item hasn't
        # reached "playing" yet, it would survive here and prematurely clear
        # _pending_display in _refresh_all before transitioning to "done".
        active = next(
            (i for i in self.queue.items if i.status in ("casting", "playing")), None
        )
        if active:
            active.status = "done"
        self.pipeline.resume_position = target.playback_position
        self._pending_display = target
        self.pipeline._next_item_id = target.id
        self.pipeline.cancel_cast()
        if target.status == "queued":
            self.pipeline.cancel_prepare()
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
            if self.queue.move(queue_item.id, "up"):
                self._refresh_queue_list()
                self.pipeline.wake_prepare()

    def action_move_down(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            if self.queue.move(queue_item.id, "down"):
                self._refresh_queue_list()
                self.pipeline.wake_prepare()

    def _refresh_loop_indicator(self) -> None:
        text = " CODA ⟳" if self.loop_mode else " CODA"
        self.query_one("#queue-header", Static).update(text)

    def action_toggle_loop(self) -> None:
        self.loop_mode = not self.loop_mode
        self.pipeline.loop_mode = self.loop_mode
        self._refresh_loop_indicator()
        self.notify(f"Loop: {'ON' if self.loop_mode else 'OFF'}")
        if self.loop_mode and self.pipeline._cast_enabled:
            self.pipeline.wake()

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

    async def action_calibrate(self) -> None:
        def _playing() -> bool:
            return any(
                i.status in ("casting", "playing") for i in self.queue.items
            )

        if _playing():
            self.notify("Ferma il video attuale prima di calibrare.", severity="warning")
            return

        self.notify("Generazione pattern di calibrazione…")
        out_path = os.path.join(config.TEMP_DIR, "calibration.mp4")
        try:
            await calibration.generate_calibration_clip(out_path)
        except Exception as e:
            self.notify(f"Errore calibrazione: {e}", severity="error")
            return

        if _playing():
            self.notify(
                "Un video è iniziato durante la generazione del pattern.",
                severity="warning",
            )
            return

        try:
            media_url = (
                f"http://{get_local_ip()}:{config.SERVER_PORT}/media/calibration.mp4"
            )
            if not self.chromecast.connected:
                await asyncio.wait_for(self.chromecast.wait_for_connection(), timeout=30.0)
            await asyncio.to_thread(self.chromecast.cast_url, media_url, 0.0)
        except asyncio.TimeoutError:
            self.notify("Chromecast non trovato.", severity="error")
            return
        except Exception as e:
            self.notify(f"Errore cast pattern: {e}", severity="error")
            return

        self.notify(
            f"Pattern di calibrazione in riproduzione. Margini attuali: "
            f"T:{config.MARGIN_TOP} B:{config.MARGIN_BOTTOM} "
            f"L:{config.MARGIN_LEFT} R:{config.MARGIN_RIGHT}. "
            f"Ctrl+T per rigenerare, Ctrl+S per fermare.",
            timeout=10,
        )
