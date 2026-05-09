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
        try:
            list_view = self.query_one("#items_list", ListView)
        except Exception:
            return
        list_view.clear()
        for item in value.get("items", []):
            marker = ">>" if item.get("is_cursor") else "  "
            label = f"{marker} {item['status']:>10s}  {item['title']}"
            list_view.append(ListItem(Label(label)))

    def watch_status_state(self, value: dict) -> None:
        try:
            bar = self.query_one("#status_bar", Static)
        except Exception:
            return
        yt = value.get("youtube", {})
        pl = value.get("player", {})
        bar.update(
            f"YT: {yt.get('state', '?')} (last sync: {yt.get('last_sync_at') or 'never'})  |  "
            f"Player: {pl.get('state', '?')}  |  CC: {pl.get('chromecast', '?')}"
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
