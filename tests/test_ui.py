import pytest
from textual.widgets import Button, Input, ListView, Select

from ui import CRTCastApp, NowPlayingWidget, QueueListItem


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
async def test_keybinding_seek_forward(app, mock_chromecast):
    async with app.run_test() as pilot:
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
