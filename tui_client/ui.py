from __future__ import annotations

import asyncio
import logging

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from tui_client.data_provider import DaemonClient

log = logging.getLogger(__name__)


class CRTCastApp(App):
    """Headless TUI client. Polls the crt-daemon HTTP API and posts commands.

    Designed to run on a separate machine from the daemon (over LAN). All state lives
    on the daemon — the TUI is stateless and refreshes via /library/items + /status.
    """

    CSS = """
    Screen {
        background: $surface;
    }
    #status_bar {
        height: auto;
        padding: 0 1;
        background: $primary 20%;
        color: $text;
    }
    #items_list {
        height: 1fr;
        border: solid $primary;
    }
    """

    BINDINGS = [
        Binding("ctrl+space", "toggle", "Play/Pause", priority=True),
        Binding("ctrl+s", "stop", "Stop", priority=True),
        Binding("ctrl+n", "next", "Next", priority=True),
        Binding("ctrl+b", "prev", "Prev", priority=True),
        Binding("ctrl+t", "calibrate", "Calibrate", priority=True),
        Binding("ctrl+r", "loop_toggle", "Loop", priority=True),
        Binding("ctrl+y", "sync", "Sync now", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True),
    ]

    library_state: reactive[dict] = reactive({})
    status_state: reactive[dict] = reactive({})

    def __init__(self, daemon_url: str):
        super().__init__()
        self._row_video_ids: list = []
        self._row_labels: list = []
        self._row_widgets: list = []
        self.client = DaemonClient(daemon_url)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Connecting to daemon...", id="status_bar"),
            ListView(id="items_list"),
        )
        yield Footer()

    async def on_mount(self) -> None:
        # Initial fetch + periodic polling
        await self._refresh_library()
        await self._refresh_status()
        self.set_interval(1.0, self._refresh_library)
        self.set_interval(2.0, self._refresh_status)

    async def _refresh_library(self) -> None:
        try:
            self.library_state = await asyncio.to_thread(self.client.fetch_library)
        except Exception as e:
            log.warning("fetch_library failed: %s", e)

    async def _refresh_status(self) -> None:
        try:
            self.status_state = await asyncio.to_thread(self.client.fetch_status)
        except Exception as e:
            log.warning("fetch_status failed: %s", e)

    def watch_library_state(self, value: dict) -> None:
        self._schedule_rows_sync()
        # loop_mode lives in library_state; refresh the status bar to reflect it.
        self._render_status_bar()

    def watch_status_state(self, value: dict) -> None:
        # The TV highlight marker depends on the status: sync the rows too.
        self._schedule_rows_sync()
        self._render_status_bar()

    def _schedule_rows_sync(self) -> None:
        # Exclusive worker: rebuilds never overlap, the last one wins.
        self.run_worker(self._sync_rows, exclusive=True, group="rows")

    def _row_label(self, item: dict, selected_id) -> str:
        cursor = ">>" if item.get("is_cursor") else "  "
        tv = "*" if selected_id and item.get("video_id") == selected_id else " "
        return f"{cursor}{tv} {item['status']:>10s}  {item['title']}"

    async def _sync_rows(self) -> None:
        try:
            list_view = self.query_one("#items_list", ListView)
        except Exception:
            return
        items = (self.library_state or {}).get("items", [])
        selected_id = (self.status_state or {}).get("player", {}).get("selected_video_id")
        ids = [item.get("video_id") for item in items]
        labels = [self._row_label(item, selected_id) for item in items]
        if ids == self._row_video_ids and len(self._row_labels) == len(labels):
            # Same rows: update the text in place, keeping the highlight.
            for widget, old, new in zip(self._row_widgets, self._row_labels, labels):
                if old != new:
                    widget.update(new)
            self._row_labels = labels
            return
        previous_index = list_view.index
        await list_view.clear()
        self._row_widgets = [Label(text) for text in labels]
        await list_view.extend(ListItem(w) for w in self._row_widgets)
        self._row_video_ids = ids
        self._row_labels = labels
        if previous_index is not None and items:
            # clear() resets the highlight: restore the user's row (clamped).
            list_view.index = min(previous_index, len(items) - 1)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self._row_video_ids):
            return
        video_id = self._row_video_ids[index]
        try:
            await asyncio.to_thread(self.client.play, video_id)
        except Exception as e:
            log.warning("play failed: %s", e)

    def _render_status_bar(self) -> None:
        try:
            bar = self.query_one("#status_bar", Static)
        except Exception:
            return
        status = self.status_state or {}
        library = self.library_state or {}
        yt = status.get("youtube", {})
        pl = status.get("player", {})
        loop_label = "loop ON" if library.get("loop_mode") else "loop off"
        tv_label = ""
        screen = pl.get("screen")
        if screen in ("paused", "stopped"):
            ids = [i.get("video_id") for i in library.get("items", [])]
            selected = pl.get("selected_video_id")
            number = f"{ids.index(selected) + 1:02d}" if selected in ids else "--"
            word = "in pausa" if screen == "paused" else "fermo"
            tv_label = f"  |  TV: {word} \u00b7 evidenziato {number}"
        bar.update(
            f"YT: {yt.get('state', '?')} (last sync: {yt.get('last_sync_at') or 'never'})  |  "
            f"Player: {pl.get('state', '?')}  |  CC: {pl.get('chromecast', '?')}  |  "
            f"{loop_label}{tv_label}"
        )

    async def action_toggle(self) -> None:
        try:
            await asyncio.to_thread(self.client.toggle)
        except Exception as e:
            log.warning("toggle failed: %s", e)

    async def action_stop(self) -> None:
        try:
            await asyncio.to_thread(self.client.stop)
        except Exception as e:
            log.warning("stop failed: %s", e)

    async def action_next(self) -> None:
        try:
            await asyncio.to_thread(self.client.next)
        except Exception as e:
            log.warning("next failed: %s", e)

    async def action_prev(self) -> None:
        try:
            await asyncio.to_thread(self.client.prev)
        except Exception as e:
            log.warning("prev failed: %s", e)

    async def action_calibrate(self) -> None:
        try:
            await asyncio.to_thread(self.client.calibrate)
        except Exception as e:
            log.warning("calibrate failed: %s", e)

    async def action_loop_toggle(self) -> None:
        try:
            await asyncio.to_thread(self.client.loop_toggle)
        except Exception as e:
            log.warning("loop_toggle failed: %s", e)

    async def action_sync(self) -> None:
        try:
            await asyncio.to_thread(self.client.trigger_sync)
        except Exception as e:
            log.warning("sync failed: %s", e)
