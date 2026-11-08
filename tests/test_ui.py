from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_daemon_client():
    c = MagicMock()
    c.fetch_library.return_value = {"cursor_video_id": None, "loop_mode": False, "items": []}
    c.fetch_status.return_value = {
        "youtube": {"state": "ok", "last_sync_at": None, "last_error": None, "playlist_id": None},
        "pipeline": {"state": "idle", "current_video_id": None, "queue_depth": 0},
        "player": {"state": "idle", "current_video_id": None, "current_time_s": None, "duration_s": None, "chromecast": "disconnected"},
    }
    c.next.return_value = {"ok": True, "cursor_video_id": "A"}
    c.prev.return_value = {"ok": True, "cursor_video_id": None}
    c.toggle.return_value = {"ok": True, "state": "paused"}
    c.stop.return_value = {"ok": True}
    c.calibrate.return_value = {"ok": True}
    c.loop_toggle.return_value = {"ok": True, "loop_mode": True}
    c.trigger_sync.return_value = {"ok": True}
    c.play.return_value = {"ok": True, "cursor_video_id": "X"}
    return c


@pytest.fixture
def tui_app(mock_daemon_client):
    from tui_client.ui import CRTCastApp
    app = CRTCastApp("http://mock")
    app.client = mock_daemon_client
    return app


@pytest.mark.asyncio
async def test_press_ctrl_n_calls_next(tui_app, mock_daemon_client):
    async with tui_app.run_test() as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
    mock_daemon_client.next.assert_called()


@pytest.mark.asyncio
async def test_press_ctrl_b_calls_prev(tui_app, mock_daemon_client):
    async with tui_app.run_test() as pilot:
        await pilot.press("ctrl+b")
        await pilot.pause()
    mock_daemon_client.prev.assert_called()


@pytest.mark.asyncio
async def test_press_ctrl_s_calls_stop(tui_app, mock_daemon_client):
    async with tui_app.run_test() as pilot:
        await pilot.press("ctrl+s")
        await pilot.pause()
    mock_daemon_client.stop.assert_called()


@pytest.mark.asyncio
async def test_press_ctrl_y_triggers_sync(tui_app, mock_daemon_client):
    async with tui_app.run_test() as pilot:
        await pilot.press("ctrl+y")
        await pilot.pause()
    mock_daemon_client.trigger_sync.assert_called()


@pytest.mark.asyncio
async def test_initial_fetch_populates_library(tui_app, mock_daemon_client):
    mock_daemon_client.fetch_library.return_value = {
        "cursor_video_id": "A",
        "loop_mode": False,
        "items": [
            {"video_id": "A", "id": "x", "title": "Hello", "status": "ready", "progress": 100, "error": None, "is_cursor": True},
        ],
    }
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
    # After mount, the client should have been called
    assert mock_daemon_client.fetch_library.call_count >= 1


def _two_items_library():
    return {
        "cursor_video_id": "A",
        "loop_mode": False,
        "items": [
            {"video_id": "A", "id": "a", "title": "Alpha", "status": "ready", "progress": 100, "error": None, "is_cursor": True},
            {"video_id": "B", "id": "b", "title": "Beta", "status": "ready", "progress": 100, "error": None, "is_cursor": False},
        ],
    }


def _status_with(mock_daemon_client, screen, selected):
    status = mock_daemon_client.fetch_status.return_value
    status["player"] = {**status["player"], "screen": screen, "selected_video_id": selected}


def _row_texts(app):
    from textual.widgets import Label, ListView
    lv = app.query_one("#items_list", ListView)
    return [str(item.query_one(Label).render()) for item in lv.children]


@pytest.mark.asyncio
async def test_list_marks_tv_highlight(tui_app, mock_daemon_client):
    mock_daemon_client.fetch_library.return_value = _two_items_library()
    _status_with(mock_daemon_client, "paused", "B")
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        rows = _row_texts(tui_app)
    assert rows[0].startswith(">> ")
    assert rows[1].startswith("  *")


@pytest.mark.asyncio
async def test_enter_on_row_plays_that_video(tui_app, mock_daemon_client):
    from textual.widgets import ListView
    mock_daemon_client.fetch_library.return_value = _two_items_library()
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        lv = tui_app.query_one("#items_list", ListView)
        lv.focus()
        lv.index = 1
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    mock_daemon_client.play.assert_called_with("B")


@pytest.mark.asyncio
async def test_list_keeps_index_across_refresh(tui_app, mock_daemon_client):
    from textual.widgets import ListView
    mock_daemon_client.fetch_library.return_value = _two_items_library()
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        lv = tui_app.query_one("#items_list", ListView)
        lv.index = 1
        await pilot.pause()
        tui_app.library_state = _two_items_library()
        await pilot.pause()
        await pilot.pause()
        assert lv.index == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("screen,text", [("stopped", "TV: fermo · evidenziato 02"), ("paused", "TV: in pausa · evidenziato 02")])
async def test_status_bar_shows_tv_screen(tui_app, mock_daemon_client, screen, text):
    from textual.widgets import Static
    mock_daemon_client.fetch_library.return_value = _two_items_library()
    _status_with(mock_daemon_client, screen, "B")
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        bar = str(tui_app.query_one("#status_bar", Static).render())
    assert text in bar


@pytest.mark.asyncio
async def test_status_bar_hides_tv_segment_when_none(tui_app, mock_daemon_client):
    from textual.widgets import Static
    mock_daemon_client.fetch_library.return_value = _two_items_library()
    _status_with(mock_daemon_client, "none", "B")
    async with tui_app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        bar = str(tui_app.query_one("#status_bar", Static).render())
    assert "TV:" not in bar
