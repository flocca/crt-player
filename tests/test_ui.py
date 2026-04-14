import pytest
from textual.widgets import Button, Input, ListView, Select
from unittest.mock import AsyncMock, MagicMock, patch

from ui import CRTCastApp, NowPlayingWidget, QueueListItem, QueueListView


# --- Compose & mount ---


@pytest.mark.asyncio
async def test_compose_renders_core_widgets(app):
    async with app.run_test() as pilot:
        # query_one raises NoMatches if not found, so just call it
        app.query_one("#url-input", Input)
        app.query_one("#mode-select", Select)
        app.query_one("#now-playing", NowPlayingWidget)
        app.query_one("#queue-list", ListView)
        assert app.query_one("#playback-row").display is False


@pytest.mark.asyncio
async def test_on_mount_starts_async_tasks(app, mock_chromecast, mock_pipeline):
    async with app.run_test() as pilot:
        mock_chromecast.set_status_callback.assert_called_once()
        mock_chromecast.set_connection_callback.assert_called_once()
        mock_pipeline.set_update_callback.assert_called_once()
        mock_chromecast.discover_loop.assert_awaited_once()
        mock_pipeline.run_prepare.assert_awaited_once()
        mock_pipeline.run_cast.assert_awaited_once()


# --- URL submission ---


@pytest.mark.asyncio
async def test_url_submission_adds_to_queue(app, queue, mock_pipeline):
    async with app.run_test() as pilot:
        # Focus the input and type the URL
        await pilot.click("#url-input")
        await pilot.pause()
        app.query_one("#url-input", Input).value = "https://youtube.com/watch?v=abc"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert len(queue.items) == 1
        assert queue.items[0].url == "https://youtube.com/watch?v=abc"
        mock_pipeline.wake.assert_called()
        assert app.query_one("#url-input", Input).value == ""


@pytest.mark.asyncio
async def test_url_submission_empty_does_nothing(app, queue, mock_pipeline):
    async with app.run_test() as pilot:
        await pilot.click("#url-input")
        await pilot.press("enter")
        await pilot.pause()
        assert len(queue.items) == 0


# --- Queue display ---


@pytest.mark.asyncio
async def test_queue_list_displays_items(app, queue):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        await pilot.pause()
        # on_mount calls _refresh_all only if next_pending(), so trigger manually
        app._refresh_all()
        await pilot.pause()
        items = app.query(QueueListItem)
        assert len(items) == 2


@pytest.mark.asyncio
async def test_now_playing_shows_active_item(app, queue):
    item = queue.add("https://youtube.com/watch?v=1")
    item.title = "My Video"
    item.status = "playing"
    async with app.run_test() as pilot:
        # _refresh_all not auto-called for playing (only for pending), trigger it
        app._refresh_all()
        await pilot.pause()
        np = app.query_one("#now-playing", NowPlayingWidget)
        assert np.title == "My Video"


# --- Playback controls ---


@pytest.mark.asyncio
async def test_pause_button(app, queue, mock_chromecast):
    item = queue.add("https://youtube.com/watch?v=1")
    item.title = "Video"
    item.status = "playing"
    async with app.run_test() as pilot:
        # Make playback row visible
        app._refresh_all()
        await pilot.pause()
        await pilot.click("#btn-pause")
        await pilot.pause()
        mock_chromecast.pause_or_resume.assert_called_once()


@pytest.mark.asyncio
async def test_keybinding_seek_forward(app, queue, mock_chromecast):
    item = queue.add("https://youtube.com/watch?v=1")
    item.status = "playing"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        await pilot.press("ctrl+right")
        await pilot.pause()
        mock_chromecast.seek.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_keybinding_volume_up(app, mock_chromecast):
    async with app.run_test() as pilot:
        await pilot.press("plus")
        await pilot.pause()
        mock_chromecast.adjust_volume.assert_called_once_with(10)


# --- Queue manipulation ---


# --- List click vs Enter ---


@pytest.mark.asyncio
async def test_click_item_selects_only_no_play(app, queue, mock_pipeline):
    """Clicking a queued item must highlight it but NOT start playback."""
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "Video 1"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Video 2"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        mock_pipeline.wake.reset_mock()
        # Click the second item
        list_view = app.query_one("#queue-list", QueueListView)
        second_item = list(app.query(QueueListItem))[1]
        await pilot.click(second_item)
        await pilot.pause()
        # Index moves but pipeline is NOT woken (no play triggered)
        assert list_view.index == 1
        mock_pipeline.wake.assert_not_called()
        # Queue order unchanged
        assert queue.items[0].title == "Video 1"


@pytest.mark.asyncio
async def test_enter_on_ready_item_starts_play(app, queue, mock_pipeline):
    """Pressing Enter on a ready item must move it to front and wake the pipeline."""
    item1 = queue.add("https://youtube.com/watch?v=1")
    item1.title = "Playing"
    item1.status = "playing"
    item2 = queue.add("https://youtube.com/watch?v=2")
    item2.title = "Ready"
    item2.status = "ready"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        list_view = app.query_one("#queue-list", QueueListView)
        list_view.focus()
        list_view.index = 1
        await pilot.pause()
        mock_pipeline.wake.reset_mock()
        await pilot.press("enter")
        await pilot.pause()
        # Pipeline woken with the specific item to play; queue order unchanged
        mock_pipeline.wake.assert_called_once()
        assert mock_pipeline._next_item_id == item2.id
        # Queue order preserved (playing item stays first)
        assert queue.items[0].title == "Playing"


@pytest.mark.asyncio
async def test_now_playing_no_blank_when_prev_was_playing(app, queue, mock_pipeline):
    """now-playing must not flash empty when switching away from a playing item."""
    item_a = queue.add("https://youtube.com/watch?v=1")
    item_a.title = "Video A"
    item_a.status = "playing"
    item_b = queue.add("https://youtube.com/watch?v=2")
    item_b.title = "Video B"
    item_b.status = "ready"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        list_view = app.query_one("#queue-list", QueueListView)
        list_view.focus()
        list_view.index = 1
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Pipeline mid-transition: A → done, B not yet casting
        app._refresh_all()
        await pilot.pause()
        np = app.query_one("#now-playing", NowPlayingWidget)
        assert np.title == "Video B", f"Expected 'Video B', got '{np.title}'"


@pytest.mark.asyncio
async def test_now_playing_no_blank_when_prev_was_casting(app, queue, mock_pipeline):
    """now-playing must not flash empty when switching away from a still-casting item.

    This is the tricky case: if the old item was "casting" (not yet "playing"),
    _refresh_all would find it as the active item and clear _pending_display before
    the pipeline sets it to "done", leaving the widget blank.
    """
    item_a = queue.add("https://youtube.com/watch?v=1")
    item_a.title = "Video A"
    item_a.status = "casting"  # Not yet "playing"
    item_b = queue.add("https://youtube.com/watch?v=2")
    item_b.title = "Video B"
    item_b.status = "ready"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        list_view = app.query_one("#queue-list", QueueListView)
        list_view.focus()
        list_view.index = 1
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Pipeline mid-transition: A → done, B not yet casting
        app._refresh_all()
        await pilot.pause()
        np = app.query_one("#now-playing", NowPlayingWidget)
        assert np.title == "Video B", f"Expected 'Video B', got '{np.title}'"


@pytest.mark.asyncio
async def test_enter_on_playing_item_does_nothing(app, queue, mock_pipeline):
    """Pressing Enter on an already-playing item must not trigger any action."""
    item = queue.add("https://youtube.com/watch?v=1")
    item.title = "Playing"
    item.status = "playing"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        list_view = app.query_one("#queue-list", QueueListView)
        list_view.focus()
        list_view.index = 0
        await pilot.pause()
        mock_pipeline.wake.reset_mock()
        await pilot.press("enter")
        await pilot.pause()
        mock_pipeline.wake.assert_not_called()


@pytest.mark.asyncio
async def test_remove_item(app, queue):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "To Remove"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Keep"
    async with app.run_test() as pilot:
        # Populate the list view
        app._refresh_all()
        await pilot.pause()
        list_view = app.query_one("#queue-list", ListView)
        list_view.focus()
        await pilot.pause()
        # Ensure first item is highlighted
        list_view.index = 0
        await pilot.pause()
        await pilot.press("backspace")
        await pilot.pause()
        assert len(queue.items) == 1
        assert queue.items[0].title == "Keep"


# --- Calibration pattern ---


@pytest.mark.asyncio
async def test_ctrl_t_triggers_calibration(app, mock_chromecast):
    """Pressing ctrl+t should generate and cast the calibration pattern."""
    mock_chromecast.connected = True
    mock_chromecast.wait_for_connection = AsyncMock()
    mock_chromecast.cast_url = MagicMock()

    with patch("ui.calibration.generate_calibration_clip", new=AsyncMock()) as gen, \
         patch("ui.get_local_ip", return_value="127.0.0.1"):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await pilot.pause()

    gen.assert_awaited_once()
    mock_chromecast.cast_url.assert_called_once()
    args, _ = mock_chromecast.cast_url.call_args
    assert args[0].endswith("/media/calibration.mp4")


@pytest.mark.asyncio
async def test_ctrl_t_blocked_while_video_playing(app, queue, mock_chromecast):
    """If a queue item is casting/playing, ctrl+t should not cast the pattern."""
    item = queue.add("https://youtube.com/watch?v=abc")
    item.status = "playing"
    mock_chromecast.cast_url = MagicMock()

    with patch("ui.calibration.generate_calibration_clip", new=AsyncMock()) as gen:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await pilot.pause()

    gen.assert_not_awaited()
    mock_chromecast.cast_url.assert_not_called()


@pytest.mark.asyncio
async def test_ctrl_t_aborts_if_video_starts_during_render(app, queue, mock_chromecast):
    """If a video starts playing while the pattern is being rendered, skip the cast."""
    mock_chromecast.connected = True
    mock_chromecast.cast_url = MagicMock()

    async def fake_generate(*_args, **_kwargs):
        # Simulate a video starting mid-render.
        item = queue.add("https://youtube.com/watch?v=xyz")
        item.status = "playing"
        return "/tmp/calibration.mp4"

    with patch("ui.calibration.generate_calibration_clip", new=fake_generate), \
         patch("ui.get_local_ip", return_value="127.0.0.1"):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await pilot.pause()

    mock_chromecast.cast_url.assert_not_called()
