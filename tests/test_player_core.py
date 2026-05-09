from unittest.mock import AsyncMock, MagicMock

import pytest

from crt.library_store import LibraryStore, QueueItem
from crt.player_core import PlayerCore


def _make_library(video_ids):
    ls = LibraryStore()
    for vid in video_ids:
        ls.items.append(QueueItem(url=f"u/{vid}", video_id=vid, title=vid, status="ready", filename=f"{vid}.mp4"))
    return ls


def _make_chromecast():
    cc = MagicMock()
    cc.connected = True
    cc.cast_url = MagicMock()
    cc.stop = MagicMock()
    cc.pause_or_resume = MagicMock()
    cc.player_state = "IDLE"
    cc.wait_for_connection = AsyncMock()
    return cc


@pytest.mark.asyncio
async def test_next_with_no_cursor_advances_to_first_item():
    library = _make_library(["A", "B", "C"])
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_advances_cursor_by_one():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_prev_moves_cursor_back():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "B"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_prev_at_first_item_is_no_op():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_at_end_no_loop_stops_at_last():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_next_at_end_with_loop_wraps_to_first():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_play_specific_video_id():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.play("C")

    assert library.cursor_video_id == "C"


@pytest.mark.asyncio
async def test_play_unknown_video_id_raises():
    library = _make_library(["A"])
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    with pytest.raises(KeyError):
        await pc.play("NOPE")


@pytest.mark.asyncio
async def test_stop_calls_chromecast_stop():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop()

    cc.stop.assert_called_once()
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_toggle_when_playing_pauses():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.player_state = "PLAYING"
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.toggle()

    cc.pause_or_resume.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_when_idle_with_no_cursor_starts_first_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = None
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.toggle()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_toggle_when_idle_with_cursor_starts_cursor_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_stop_and_remove_stops_if_video_id_is_current():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop_and_remove("A")

    cc.stop.assert_called_once()
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_stop_and_remove_no_op_if_video_id_is_not_current():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop_and_remove("B")

    cc.stop.assert_not_called()


@pytest.mark.asyncio
async def test_cast_current_calls_chromecast_cast_url(monkeypatch):
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", "/tmp")
    monkeypatch.setattr(config, "SERVER_PORT", 8765)

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].status = "ready"
    library.items[0].filename = "A.mp4"
    library.items[0].playback_position = 42.0

    cc = _make_chromecast()
    cc.connected = True
    pc = PlayerCore(library, cc)

    await pc._cast_current()

    cc.wait_for_connection.assert_awaited()
    args, kwargs = cc.cast_url.call_args
    assert "A.mp4" in args[0]
    assert kwargs.get("start_position") == 42.0


@pytest.mark.asyncio
async def test_on_playback_finished_advances_cursor():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_no_loop_stops():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_with_loop_wraps():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_prev_recasts_item_previously_playing(monkeypatch):
    """Regression: prev su item che era stato in 'playing' deve ri-castare,
    non bloccare con 'cursor not ready'. Il file è ancora in cache."""
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", "/tmp")
    monkeypatch.setattr(config, "SERVER_PORT", 8765)

    library = _make_library(["A", "B"])
    library.items[0].status = "playing"  # A era playing prima del next
    library.items[1].status = "playing"  # B sta playing ora
    library.cursor_video_id = "B"

    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"
    cc.cast_url.assert_called_once()
    assert library.items[0].status == "playing"  # ora è di nuovo current
    assert library.items[1].status == "ready"  # de-staled


@pytest.mark.asyncio
async def test_stop_resets_current_item_to_ready():
    """Regression: dopo stop il cursor item torna 'ready' così un toggle/play
    successivo lo ri-casta invece di skipparlo."""
    library = _make_library(["A"])
    library.items[0].status = "playing"
    library.cursor_video_id = "A"

    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop()

    assert pc.state == "idle"
    assert library.items[0].status == "ready"


@pytest.mark.asyncio
async def test_toggle_after_stop_recasts_cursor_item(monkeypatch):
    """Regression: toggle dopo stop deve ricastare l'item corrente."""
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", "/tmp")
    monkeypatch.setattr(config, "SERVER_PORT", 8765)

    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].status = "ready"  # post-stop

    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    cc.cast_url.assert_called_once()
    assert pc.state == "casting"
    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_watch_natural_end_advances_cursor_on_finished_event():
    """Regression: il watcher deve invocare on_playback_finished quando
    chromecast.wait_for_playback_end() ritorna."""
    import asyncio

    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].status = "playing"

    cc = _make_chromecast()
    cc.connected = True
    # Simula natural end. L'event viene set una sola volta; reset_playback_ended
    # lo cancella, così la 2a iterazione del watcher si blocca in attesa.
    end_event = asyncio.Event()
    end_event.set()

    async def _wait_end():
        await end_event.wait()

    cc.wait_for_playback_end = _wait_end
    cc.reset_playback_ended = MagicMock(side_effect=end_event.clear)

    pc = PlayerCore(library, cc)
    task = asyncio.create_task(pc.watch_natural_end())
    # Lascia girare una sola iterazione: dopo reset, l'event è clear → la 2a
    # iterazione si blocca su await end_event.wait(). Cancelliamo il task.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.items[0].status == "done"
    assert library.cursor_video_id == "B"
    cc.reset_playback_ended.assert_called()
