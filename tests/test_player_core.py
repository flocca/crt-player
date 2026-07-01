import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from crt import config
from crt.library_store import LibraryStore, QueueItem
from crt.player_core import PlayerCore


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    """Point TEMP_DIR at an isolated dir so the encoded cache files the helpers
    create (and that cache_file_present() checks — issue #10) live somewhere
    real and per-test. SERVER_PORT is fixed for deterministic media URLs."""
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVER_PORT", 8765)
    return tmp_path


def _write_cache_file(filename):
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    with open(os.path.join(config.TEMP_DIR, filename), "w") as f:
        f.write("dummy")


def _make_library(video_ids):
    ls = LibraryStore()
    for vid in video_ids:
        fname = f"{vid}.mp4"
        _write_cache_file(fname)
        ls.items.append(QueueItem(url=f"u/{vid}", video_id=vid, title=vid, status="ready", filename=fname))
    return ls


def _make_chromecast():
    cc = MagicMock()
    cc.connected = True
    cc.cast_url = MagicMock()
    cc.stop = MagicMock()
    cc.pause_or_resume = MagicMock()
    cc.player_state = "IDLE"
    cc.live_current_time = 0.0
    cc.wait_for_connection = AsyncMock()
    # Default: a live media session exists, so toggle takes the normal
    # pause/resume path rather than the session-loss recast path (issue #7).
    cc.is_session_lost = MagicMock(return_value=False)
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
async def test_stop_saves_live_position_for_resume():
    """Regression (issue #13): stop must snapshot the live position onto the
    cursor item so a later toggle/play resumes near where playback left off,
    instead of relying solely on the 30s periodic save (which can be
    arbitrarily stale, or never have ticked at all for a short session)."""
    library = _make_library(["A"])
    library.items[0].status = "playing"
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.live_current_time = 245.0
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop()

    assert library.items[0].playback_position == 245.0


@pytest.mark.asyncio
async def test_stop_captures_position_before_issuing_chromecast_stop():
    """The live position must be read BEFORE chromecast.stop() tears down the
    media session — reading it after risks an already-reset/stale value."""
    library = _make_library(["A"])
    library.items[0].status = "playing"
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.live_current_time = 245.0

    def _reset_on_stop():
        cc.live_current_time = 0.0

    cc.stop = MagicMock(side_effect=_reset_on_stop)
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.stop()

    assert library.items[0].playback_position == 245.0


@pytest.mark.asyncio
async def test_stop_does_not_touch_position_when_not_playing():
    """A stop issued while the cursor item is already idle/ready must not
    clobber a previously saved resume position with a bogus live read."""
    library = _make_library(["A"])
    library.items[0].status = "ready"
    library.items[0].playback_position = 88.0
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.live_current_time = 3.0
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.stop()

    assert library.items[0].playback_position == 88.0


@pytest.mark.asyncio
async def test_toggle_after_stop_recasts_cursor_item(monkeypatch):
    """Regression: toggle dopo stop deve ricastare l'item corrente."""
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


# ---------------------------------------------------------------------
# Skip-non-playable navigation: next/prev/toggle/on_playback_finished
# devono saltare gli item senza cache (filename=None) e atterrare solo
# su roba castabile. Da remoto un toggle/next/prev su item ancora in
# encoding/queued non deve essere un no-op silenzioso.
# ---------------------------------------------------------------------


def _make_mixed_library(specs):
    """specs: list of (video_id, playable: bool)."""
    ls = LibraryStore()
    for vid, playable in specs:
        if playable:
            fname = f"{vid}.mp4"
            _write_cache_file(fname)
            ls.items.append(QueueItem(
                url=f"u/{vid}", video_id=vid, title=vid,
                status="ready", filename=fname,
            ))
        else:
            ls.items.append(QueueItem(
                url=f"u/{vid}", video_id=vid, title=vid,
                status="encoding", filename=None,
            ))
    return ls


@pytest.mark.asyncio
async def test_next_skips_items_without_filename(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    assert library.cursor_video_id == "C"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_prev_skips_items_without_filename(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "C"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    assert library.cursor_video_id == "A"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_next_no_playable_forward_is_noop():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    # Nessun item playable dopo A, no loop → cursore resta su A, nessun cast.
    assert library.cursor_video_id == "A"
    cc.cast_url.assert_not_called()


@pytest.mark.asyncio
async def test_next_with_loop_wraps_skipping_unready(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = True
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.next()

    # B e C non castabili, loop attivo → wrap su A (l'unico playable).
    assert library.cursor_video_id == "A"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_on_playback_finished_skips_unready_items(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.on_playback_finished()

    # Fine naturale di A → B in encoding viene skippato → atterra su C.
    assert library.items[0].status == "done"
    assert library.cursor_video_id == "C"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_on_playback_finished_no_next_playable_goes_idle():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.on_playback_finished()

    assert library.items[0].status == "done"
    assert pc.state == "idle"
    cc.cast_url.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_idle_with_cursor_on_unready_finds_playable_forward(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "B"  # cursore su item non castabile
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    # Da idle con cursore su B (encoding), toggle deve scendere a C.
    assert library.cursor_video_id == "C"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_idle_nothing_playable_is_noop():
    library = _make_mixed_library([("A", False), ("B", False)])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    cc.cast_url.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_idle_with_cursor_on_ready_casts_cursor(monkeypatch):
    """Cursor item è già playable: toggle lo casta direttamente, senza
    scendere oltre."""
    library = _make_mixed_library([("A", True), ("B", True)])
    library.cursor_video_id = "B"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    await pc.toggle()

    assert library.cursor_video_id == "B"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_seek_relative_calls_chromecast_via_to_thread():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.seek_relative = MagicMock()
    pc = PlayerCore(library, cc)

    await pc.seek_relative(-15)

    cc.seek_relative.assert_called_once_with(-15)


@pytest.mark.asyncio
async def test_delete_current_full_path(tmp_path, monkeypatch):
    import crt.config as cfg
    import os

    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLITEM_A"
    library.items[0].filename = "A_pal_crop.mp4"  # matches cached_encoded_filename("A")
    cache_file = tmp_path / "A_pal_crop.mp4"
    cache_file.write_text("dummy")

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)
    pc.state = "playing"  # prime state so the idle-guard allows stop()

    await pc.delete_current()

    # local removal
    assert all(i.video_id != "A" for i in library.items)
    assert not cache_file.exists()
    # remote removal
    yt.delete_playlist_item.assert_called_once_with("PLITEM_A")
    # stop was called
    cc.stop.assert_called()


@pytest.mark.asyncio
async def test_delete_current_missing_playlist_item_id_skips_remote(tmp_path, monkeypatch):
    import crt.config as cfg
    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = None

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)

    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)
    yt.delete_playlist_item.assert_not_called()


@pytest.mark.asyncio
async def test_delete_current_youtube_failure_keeps_local_removal(tmp_path, monkeypatch):
    import crt.config as cfg
    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLITEM_A"

    cc = _make_chromecast()
    yt = MagicMock()
    yt.delete_playlist_item.side_effect = OSError("network failure")
    pc = PlayerCore(library, cc, youtube_client=yt)

    # Should NOT raise
    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)


@pytest.mark.asyncio
async def test_delete_current_with_no_cursor_is_noop():
    library = _make_library(["A"])
    library.cursor_video_id = None

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)

    await pc.delete_current()  # should not raise

    yt.delete_playlist_item.assert_not_called()
    assert len(library.items) == 1


# ---------------------------------------------------------------------
# Issue #5: prev() recovers from a stale/phantom cursor
# (cursor_video_id points at a video no longer in the library).
# NEXT/TOGGLE already recover; PREV must behave symmetrically.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prev_with_phantom_cursor_recovers_to_last_playable(monkeypatch):
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "GHOST"  # not in library
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    # Symmetric to next/toggle: jump to the last playable item and cast it.
    assert library.cursor_video_id == "C"
    cc.cast_url.assert_called_once()


@pytest.mark.asyncio
async def test_prev_with_phantom_cursor_skips_unready(monkeypatch):
    library = _make_mixed_library([("A", True), ("B", True), ("C", False)])
    library.cursor_video_id = "GHOST"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    await pc.prev()

    # Last item C is still encoding → scan backward lands on B.
    assert library.cursor_video_id == "B"
    cc.cast_url.assert_called_once()


# ---------------------------------------------------------------------
# Issue #6: structured ActionResult ack — did_action / reason.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_returns_did_action_true_on_cast(monkeypatch):
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    result = await pc.next()

    assert result.did_action is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_next_returns_did_action_false_when_no_playable():
    library = _make_mixed_library([("A", True), ("B", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_playable_item"


@pytest.mark.asyncio
async def test_next_returns_no_items_on_empty_library():
    library = LibraryStore()
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_items"


@pytest.mark.asyncio
async def test_prev_at_first_returns_cursor_unchanged():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    result = await pc.prev()

    assert result.did_action is False
    assert result.reason == "cursor_unchanged"


@pytest.mark.asyncio
async def test_prev_phantom_cursor_returns_did_action_true(monkeypatch):
    library = _make_library(["A", "B"])
    library.cursor_video_id = "GHOST"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)

    result = await pc.prev()

    assert result.did_action is True


@pytest.mark.asyncio
async def test_toggle_pause_returns_did_action_true():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.player_state = "PLAYING"
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    result = await pc.toggle()

    assert result.did_action is True


@pytest.mark.asyncio
async def test_toggle_idle_nothing_playable_returns_reason():
    library = _make_mixed_library([("A", False)])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    pc = PlayerCore(library, cc)
    pc.state = "idle"

    result = await pc.toggle()

    assert result.did_action is False
    assert result.reason == "no_playable_item"


# ---------------------------------------------------------------------
# Issue #7a: toggle after a long pause detects the lost media session
# and recasts the cursor item from its saved position, instead of a
# silent no-op that lies about being "playing".
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_with_lost_session_recasts_from_position(monkeypatch):
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.is_session_lost = MagicMock(return_value=True)
    cc.current_time = 1232.5  # real pause position preserved across the loss
    pc = PlayerCore(library, cc)
    pc.state = "paused"  # we believe we are paused

    result = await pc.toggle()

    # Must recast (a plain pause/resume would be a silent no-op) from the
    # preserved pause position.
    cc.cast_url.assert_called_once()
    _, kwargs = cc.cast_url.call_args
    assert kwargs.get("start_position") == 1232.5
    assert result.did_action is True
    assert pc.state == "casting"


@pytest.mark.asyncio
async def test_toggle_lost_session_no_playable_cursor_is_reported(monkeypatch):
    library = _make_mixed_library([("A", False)])  # cursor item not ready
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.is_session_lost = MagicMock(return_value=True)
    pc = PlayerCore(library, cc)
    pc.state = "paused"

    result = await pc.toggle()

    cc.cast_url.assert_not_called()
    assert result.did_action is False
    assert result.reason == "no_playable_item"


@pytest.mark.asyncio
async def test_toggle_with_live_session_does_not_recast():
    """When the session is alive, toggle must pause/resume, never recast."""
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    cc = _make_chromecast()
    cc.is_session_lost = MagicMock(return_value=False)
    cc.player_state = "PLAYING"
    pc = PlayerCore(library, cc)
    pc.state = "playing"

    await pc.toggle()

    cc.pause_or_resume.assert_called_once()
    cc.cast_url.assert_not_called()


@pytest.mark.asyncio
async def test_seek_relative_reports_no_session_when_chromecast_noop():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.seek_relative = MagicMock(return_value=False)  # no active session
    pc = PlayerCore(library, cc)

    result = await pc.seek_relative(-15)

    assert result.did_action is False
    assert result.reason == "no_chromecast_session"


@pytest.mark.asyncio
async def test_seek_relative_reports_did_action_when_seek_issued():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.seek_relative = MagicMock(return_value=True)
    pc = PlayerCore(library, cc)

    result = await pc.seek_relative(30)

    assert result.did_action is True


# ---------------------------------------------------------------------
# Issue #10: a missing encoded file (filename set, MP4 gone from disk) must
# not be cast as a dead URL. The cast/find paths re-queue it and wake the
# pipeline via the on_cache_miss callback instead of silently no-oping.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cast_current_requeues_when_cache_file_missing():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))  # file vanishes
    cc = _make_chromecast()
    woke = MagicMock()
    pc = PlayerCore(library, cc, on_cache_miss=woke)

    await pc._cast_current()

    cc.cast_url.assert_not_called()
    assert library.items[0].status == "queued"
    assert library.items[0].filename is None
    woke.assert_called_once()


@pytest.mark.asyncio
async def test_next_skips_and_requeues_stale_cache_item():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "B.mp4"))  # B's file gone
    cc = _make_chromecast()
    woke = MagicMock()
    pc = PlayerCore(library, cc, on_cache_miss=woke)

    await pc.next()

    # B is skipped (stale cache) and re-queued; cursor lands on C.
    assert library.cursor_video_id == "C"
    assert library.items[1].status == "queued"
    assert library.items[1].filename is None
    woke.assert_called()


@pytest.mark.asyncio
async def test_play_missing_file_returns_no_playable():
    """Issue #1 from review: play() must not report did_action=true when the
    cache file is gone and nothing is cast."""
    library = _make_library(["A"])
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))
    cc = _make_chromecast()
    woke = MagicMock()
    pc = PlayerCore(library, cc, on_cache_miss=woke)

    result = await pc.play("A")

    cc.cast_url.assert_not_called()
    assert result.did_action is False
    assert result.reason == "no_playable_item"
    assert library.items[0].status == "queued"
    woke.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_lost_session_missing_file_requeues():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))
    cc = _make_chromecast()
    cc.is_session_lost = MagicMock(return_value=True)
    woke = MagicMock()
    pc = PlayerCore(library, cc, on_cache_miss=woke)
    pc.state = "paused"

    result = await pc.toggle()

    cc.cast_url.assert_not_called()
    assert result.did_action is False
    assert result.reason == "no_playable_item"
    assert library.items[0].status == "queued"
    woke.assert_called_once()
