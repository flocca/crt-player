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
