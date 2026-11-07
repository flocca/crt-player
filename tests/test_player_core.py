import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from crt import config
from crt.library_store import LibraryStore, QueueItem
from crt.pause_menu import Entry
from crt.player_core import PlayerCore
from crt.receiver import ReceiverChannel

MARGINS = {"top": 0, "bottom": 0, "left": 0, "right": 0}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    """Point TEMP_DIR at an isolated dir so the encoded cache files the helpers
    create (and that cache_file_present() checks — issue #10) live somewhere
    real and per-test. SERVER_PORT and the LAN IP are fixed for deterministic
    URLs."""
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVER_PORT", 8765)
    monkeypatch.setattr("crt.player_core._get_local_ip", lambda: "10.0.0.5")
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


def _make_chromecast():
    cc = MagicMock()
    cc.connected = True
    cc.wait_for_connection = AsyncMock()
    cc.launch_receiver = MagicMock()
    cc.receiver_app_active = True
    cc.quit_app = MagicMock()
    return cc


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _make_player(library, cc=None, clock=None, **kwargs):
    """PlayerCore wired to a real ReceiverChannel with one connected page
    (queue `q`, already drained of the config message)."""
    cc = cc or _make_chromecast()
    receiver = ReceiverChannel(MARGINS, clock=clock) if clock else ReceiverChannel(MARGINS)
    q = receiver.subscribe()
    drain(q)
    if clock is not None:
        kwargs["clock"] = clock
    pc = PlayerCore(library, cc, receiver, **kwargs)
    return pc, receiver, q, cc


def loads(msgs):
    return [m for m in msgs if m["type"] == "load"]


def menus(msgs):
    return [m for m in msgs if m["type"] == "menu"]


async def _paused_on(library_ids, cursor, position, clock=None, **kwargs):
    """Library with every id ready, `cursor` playing from `position`, then
    paused through toggle(). Returns (pc, receiver, q, cc, library)."""
    library = _make_library(library_ids)
    library.cursor_video_id = cursor
    library.items[library_ids.index(cursor)].playback_position = position
    pc, receiver, q, cc = _make_player(library, clock=clock or Clock(), **kwargs)
    await pc.play(cursor)
    await pc.toggle()
    drain(q)
    return pc, receiver, q, cc, library


# ---------------------------------------------------------------------
# Navigation while playing / idle (unchanged behaviour)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_with_no_cursor_advances_to_first_item():
    library = _make_library(["A", "B", "C"])
    pc, *_ = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_advances_cursor_by_one():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_prev_moves_cursor_back():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "B"
    pc, *_ = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_prev_at_first_item_is_no_op():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_at_end_no_loop_stops_at_last():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    pc, *_ = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_next_at_end_with_loop_wraps_to_first():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    pc, *_ = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_play_specific_video_id():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    await pc.play("C")

    assert library.cursor_video_id == "C"


@pytest.mark.asyncio
async def test_play_unknown_video_id_raises():
    library = _make_library(["A"])
    pc, *_ = _make_player(library)

    with pytest.raises(KeyError):
        await pc.play("NOPE")


@pytest.mark.asyncio
async def test_toggle_when_idle_with_no_cursor_starts_first_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = None
    pc, *_ = _make_player(library)

    await pc.toggle()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_toggle_when_idle_with_cursor_starts_cursor_item():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    pc, *_ = _make_player(library)

    await pc.toggle()

    assert library.cursor_video_id == "B"


# ---------------------------------------------------------------------
# Casting through the receiver page
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cast_sends_load_with_saved_position():
    library = _make_library(["a"])
    library.items[0].playback_position = 42.0
    pc, receiver, q, cc = _make_player(library)

    result = await pc.play("a")

    msgs = drain(q)
    load = loads(msgs)[-1]
    assert load["url"] == "http://10.0.0.5:8765/media/a.mp4"
    assert load["position"] == 42.0 and load["autoplay"] is True
    assert {"type": "menu", "view": None} in msgs
    assert result.did_action and pc.state == "playing"
    assert library.items[0].status == "playing"
    cc.launch_receiver.assert_not_called()


@pytest.mark.asyncio
async def test_cast_launches_dashcast_when_page_not_connected():
    library = _make_library(["a"])
    cc = _make_chromecast()
    receiver = ReceiverChannel(MARGINS)
    pages = []
    cc.launch_receiver = MagicMock(side_effect=lambda url: pages.append(receiver.subscribe()))
    pc = PlayerCore(library, cc, receiver)

    result = await pc.play("a")

    cc.launch_receiver.assert_called_once()
    assert cc.launch_receiver.call_args.args[0] == "http://10.0.0.5:8765/receiver"
    cc.wait_for_connection.assert_awaited()
    assert result.did_action and pc.state == "playing"
    assert loads(drain(pages[0]))[-1]["url"].endswith("/media/a.mp4")


@pytest.mark.asyncio
async def test_cast_relaunches_when_another_app_took_the_chromecast():
    """A stale page subscriber must not count: launch and wait for a new one."""
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    cc.receiver_app_active = False
    cc.launch_receiver = MagicMock(side_effect=lambda url: receiver.subscribe())

    result = await pc.play("a")

    cc.launch_receiver.assert_called_once()
    assert result.did_action


@pytest.mark.asyncio
async def test_cast_fails_cleanly_when_page_never_connects(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_CONNECT_TIMEOUT_S", 0.01)
    library = _make_library(["a"])
    cc = _make_chromecast()
    pc = PlayerCore(library, cc, ReceiverChannel(MARGINS))

    result = await pc.play("a")

    assert result.did_action is False and pc.state == "idle"
    assert library.items[0].status == "ready"


@pytest.mark.asyncio
async def test_cast_fails_cleanly_when_launch_raises(monkeypatch):
    library = _make_library(["a"])
    cc = _make_chromecast()
    cc.launch_receiver = MagicMock(side_effect=RuntimeError("Chromecast not connected"))
    pc = PlayerCore(library, cc, ReceiverChannel(MARGINS))

    result = await pc.play("a")

    assert result.did_action is False and pc.state == "idle"


@pytest.mark.asyncio
async def test_prev_recasts_item_previously_playing():
    """Regression: prev su item che era stato in 'playing' deve ri-castare,
    non bloccare con 'cursor not ready'. Il file è ancora in cache."""
    library = _make_library(["A", "B"])
    library.items[0].status = "playing"  # A era playing prima del next
    library.items[1].status = "playing"  # B sta playing ora
    library.cursor_video_id = "B"
    pc, receiver, q, cc = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "A"
    assert len(loads(drain(q))) == 1
    assert library.items[0].status == "playing"  # ora è di nuovo current
    assert library.items[1].status == "ready"  # de-staled


@pytest.mark.asyncio
async def test_toggle_after_stop_recasts_cursor_item():
    """Regression: toggle dopo stop deve ricastare l'item corrente."""
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, receiver, q, cc = _make_player(library)

    await pc.toggle()

    assert len(loads(drain(q))) == 1
    assert pc.state == "playing"
    assert library.cursor_video_id == "A"


# ---------------------------------------------------------------------
# Pause opens the menu; next/prev/seek while paused
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_snapshots_position_and_opens_menu_on_current_video():
    clock = Clock()
    library = _make_library(["a", "b", "c"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("b")
    clock.t += 77
    drain(q)

    result = await pc.toggle()

    msgs = drain(q)
    assert {"type": "pause"} in msgs
    view = menus(msgs)[-1]["view"]
    assert [r["selected"] for r in view["rows"]] == [False, True, False]
    assert result.did_action
    assert pc.state == "paused" and library.items[1].playback_position == 77.0
    assert library.items[1].status == "paused"
    assert pc.menu.selected == Entry("video", "b")


@pytest.mark.asyncio
async def test_next_and_prev_move_highlight_while_paused():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 0.0)

    r = await pc.next()

    assert r.did_action and library.cursor_video_id == "b"
    msgs = drain(q)
    assert not loads(msgs)
    assert pc.menu.selected == Entry("video", "c")
    assert [r["selected"] for r in menus(msgs)[-1]["view"]["rows"]] == [False, False, True]

    await pc.prev()
    await pc.prev()
    assert pc.menu.selected == Entry("video", "a")
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_seek_while_paused_clamps_and_updates_saved_position():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 590.0)
    item = library.items[1]
    receiver.handle_report({
        "event": "status", "position": 590.0, "duration": 600.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/b.mp4",
    })

    r = await pc.seek_relative(30)
    assert r.did_action
    assert item.playback_position == 600.0
    msgs = drain(q)
    assert {"type": "seek", "position": 600.0} in msgs
    assert menus(msgs), "the menu view is re-sent so the time updates"

    await pc.seek_relative(-1000)
    assert item.playback_position == 0.0
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_seek_while_paused_without_duration_has_no_upper_clamp():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 100.0)

    await pc.seek_relative(30)

    assert library.items[0].playback_position == 130.0


@pytest.mark.asyncio
async def test_seek_while_paused_clears_delete_confirm():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 100.0)
    pc.menu.confirming_delete = True

    await pc.seek_relative(-15)

    assert pc.menu.confirming_delete is False


@pytest.mark.asyncio
async def test_seek_while_playing_sends_seek_from_live_position():
    clock = Clock()
    library = _make_library(["a"])
    library.items[0].playback_position = 10.0
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    clock.t += 20
    drain(q)

    r = await pc.seek_relative(30)

    assert r.did_action
    assert drain(q) == [{"type": "seek", "position": 60.0}]
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_seek_when_idle_reports_no_session():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)

    result = await pc.seek_relative(-15)

    assert result.did_action is False
    assert result.reason == "no_chromecast_session"
    assert drain(q) == []


@pytest.mark.asyncio
async def test_live_position_reads_receiver_only_when_active():
    clock = Clock()
    library = _make_library(["a"])
    library.items[0].playback_position = 5.0
    pc, receiver, q, cc = _make_player(library, clock=clock)
    assert pc.live_position() == 0.0
    await pc.play("a")
    clock.t += 3
    assert pc.live_position() == 8.0


# ---------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_blanks_page_and_keeps_app_open():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    result = await pc.stop()

    msgs = drain(q)
    assert {"type": "blank"} in msgs and {"type": "menu", "view": None} in msgs
    cc.quit_app.assert_not_called()
    assert result.did_action and pc.state == "idle" and pc.menu is None


@pytest.mark.asyncio
async def test_stop_resets_current_item_to_ready():
    """Regression: dopo stop il cursor item torna 'ready' così un toggle/play
    successivo lo ri-casta invece di skipparlo."""
    library = _make_library(["A"])
    pc, *_ = _make_player(library)
    await pc.play("A")

    await pc.stop()

    assert pc.state == "idle"
    assert library.items[0].status == "ready"


@pytest.mark.asyncio
async def test_stop_saves_live_position_for_resume():
    """Regression (issue #13): stop must snapshot the live position onto the
    cursor item so a later toggle/play resumes near where playback left off."""
    clock = Clock()
    library = _make_library(["A"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("A")
    clock.t += 245

    await pc.stop()

    assert library.items[0].playback_position == 245.0


@pytest.mark.asyncio
async def test_stop_while_paused_saves_paused_position():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 120.0)

    await pc.stop()

    assert library.items[0].playback_position == 120.0
    assert library.items[0].status == "ready" and pc.menu is None


@pytest.mark.asyncio
async def test_stop_does_not_touch_position_when_not_playing():
    """A stop issued while the cursor item is already idle/ready must not
    clobber a previously saved resume position with a bogus live read."""
    library = _make_library(["A"])
    library.items[0].playback_position = 88.0
    library.cursor_video_id = "A"
    pc, receiver, q, cc = _make_player(library)
    receiver.handle_report({"event": "status", "position": 3.0, "duration": 9.0, "paused": False, "src": None})

    await pc.stop()

    assert library.items[0].playback_position == 88.0


@pytest.mark.asyncio
async def test_resume_after_stop_loads_saved_position():
    clock = Clock()
    library = _make_library(["A"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("A")
    clock.t += 245
    await pc.stop()
    drain(q)

    await pc.toggle()

    assert loads(drain(q))[-1]["position"] == 245.0


@pytest.mark.asyncio
async def test_stop_and_remove_stops_if_video_id_is_current():
    library = _make_library(["A", "B"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")
    drain(q)

    await pc.stop_and_remove("A")

    assert {"type": "blank"} in drain(q)
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_stop_and_remove_stops_if_current_is_paused():
    pc, receiver, q, cc, library = await _paused_on(["A", "B"], "A", 3.0)

    await pc.stop_and_remove("A")

    assert {"type": "blank"} in drain(q)
    assert pc.state == "idle" and pc.menu is None


@pytest.mark.asyncio
async def test_stop_and_remove_no_op_if_video_id_is_not_current():
    library = _make_library(["A", "B"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")
    drain(q)

    await pc.stop_and_remove("B")

    assert drain(q) == []
    assert pc.state == "playing"


# ---------------------------------------------------------------------
# Natural end
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_playback_finished_advances_cursor():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_no_loop_stops():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    pc, receiver, q, cc = _make_player(library)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "B"
    assert pc.state == "idle"
    assert {"type": "blank"} in drain(q)


@pytest.mark.asyncio
async def test_on_playback_finished_at_end_with_loop_wraps():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    pc, *_ = _make_player(library)

    await pc.on_playback_finished()

    assert library.cursor_video_id == "A"


async def _run_watcher_once(pc):
    task = asyncio.create_task(pc.watch_natural_end())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_watch_natural_end_advances_cursor_on_ended_report():
    library = _make_library(["A", "B"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")
    receiver.handle_report({
        "event": "ended", "position": 9.0, "duration": 9.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/A.mp4",
    })

    await _run_watcher_once(pc)

    assert library.items[0].status == "done"
    assert library.cursor_video_id == "B"
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_natural_end_ignored_unless_playing():
    pc, receiver, q, cc, library = await _paused_on(["A", "B"], "A", 3.0)
    receiver.handle_report({
        "event": "ended", "position": 9.0, "duration": 9.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/A.mp4",
    })

    await _run_watcher_once(pc)

    assert library.cursor_video_id == "A"
    assert pc.state == "paused"
    assert library.items[0].status == "paused"


@pytest.mark.asyncio
async def test_late_ended_for_previous_video_does_not_advance():
    library = _make_library(["A", "B", "C"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")
    await pc.next()  # now B
    receiver.handle_report({
        "event": "ended", "position": 9.0, "duration": 9.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/A.mp4",
    })

    await _run_watcher_once(pc)

    assert library.cursor_video_id == "B"


# ---------------------------------------------------------------------
# Skip-non-playable navigation: next/prev/toggle/on_playback_finished
# devono saltare gli item senza cache (filename=None) e atterrare solo
# su roba castabile.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_skips_items_without_filename():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "A"
    pc, receiver, q, cc = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_prev_skips_items_without_filename():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "C"
    pc, receiver, q, cc = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "A"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_next_no_playable_forward_is_noop():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    pc, receiver, q, cc = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "A"
    assert loads(drain(q)) == []


@pytest.mark.asyncio
async def test_next_with_loop_wraps_skipping_unready():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = True
    pc, receiver, q, cc = _make_player(library)

    await pc.next()

    assert library.cursor_video_id == "A"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_on_playback_finished_skips_unready_items():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "A"
    pc, receiver, q, cc = _make_player(library)

    await pc.on_playback_finished()

    assert library.items[0].status == "done"
    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_on_playback_finished_no_next_playable_goes_idle():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    pc, receiver, q, cc = _make_player(library)
    pc.state = "playing"

    await pc.on_playback_finished()

    assert library.items[0].status == "done"
    assert pc.state == "idle"
    assert loads(drain(q)) == []


@pytest.mark.asyncio
async def test_toggle_idle_with_cursor_on_unready_finds_playable_forward():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "B"
    pc, receiver, q, cc = _make_player(library)

    await pc.toggle()

    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_toggle_idle_nothing_playable_is_noop():
    library = _make_mixed_library([("A", False), ("B", False)])
    library.cursor_video_id = "A"
    pc, receiver, q, cc = _make_player(library)

    await pc.toggle()

    assert loads(drain(q)) == []


@pytest.mark.asyncio
async def test_toggle_idle_with_cursor_on_ready_casts_cursor():
    library = _make_mixed_library([("A", True), ("B", True)])
    library.cursor_video_id = "B"
    pc, receiver, q, cc = _make_player(library)

    await pc.toggle()

    assert library.cursor_video_id == "B"
    assert len(loads(drain(q))) == 1


# ---------------------------------------------------------------------
# delete_current
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_current_full_path(tmp_path):
    library = _make_library(["A", "B"])
    library.items[0].playlist_item_id = "PLITEM_A"
    library.items[0].filename = "A_pal_crop.mp4"  # matches cached_encoded_filename("A")
    cache_file = tmp_path / "A_pal_crop.mp4"
    cache_file.write_text("dummy")
    yt = MagicMock()
    pc, receiver, q, cc = _make_player(library, youtube_client=yt)
    await pc.play("A")
    drain(q)

    await pc.delete_current()

    # local removal
    assert all(i.video_id != "A" for i in library.items)
    assert not cache_file.exists()
    # remote removal
    yt.delete_playlist_item.assert_called_once_with("PLITEM_A")
    # stopped first
    assert {"type": "blank"} in drain(q)
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_delete_current_missing_playlist_item_id_skips_remote():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = None
    yt = MagicMock()
    pc, *_ = _make_player(library, youtube_client=yt)

    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)
    yt.delete_playlist_item.assert_not_called()


@pytest.mark.asyncio
async def test_delete_current_youtube_failure_keeps_local_removal():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLITEM_A"
    yt = MagicMock()
    yt.delete_playlist_item.side_effect = OSError("network failure")
    pc, *_ = _make_player(library, youtube_client=yt)

    # Should NOT raise
    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)


@pytest.mark.asyncio
async def test_delete_current_with_no_cursor_is_noop():
    library = _make_library(["A"])
    library.cursor_video_id = None
    yt = MagicMock()
    pc, *_ = _make_player(library, youtube_client=yt)

    await pc.delete_current()  # should not raise

    yt.delete_playlist_item.assert_not_called()
    assert len(library.items) == 1


# ---------------------------------------------------------------------
# Issue #5: prev() recovers from a stale/phantom cursor.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prev_with_phantom_cursor_recovers_to_last_playable():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "GHOST"  # not in library
    pc, receiver, q, cc = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_prev_with_phantom_cursor_skips_unready():
    library = _make_mixed_library([("A", True), ("B", True), ("C", False)])
    library.cursor_video_id = "GHOST"
    pc, receiver, q, cc = _make_player(library)

    await pc.prev()

    assert library.cursor_video_id == "B"
    assert len(loads(drain(q))) == 1


# ---------------------------------------------------------------------
# Issue #6: structured ActionResult ack — did_action / reason.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_returns_did_action_true_on_cast():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    result = await pc.next()

    assert result.did_action is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_next_returns_did_action_false_when_no_playable():
    library = _make_mixed_library([("A", True), ("B", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    pc, *_ = _make_player(library)

    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_playable_item"


@pytest.mark.asyncio
async def test_next_returns_no_items_on_empty_library():
    pc, *_ = _make_player(LibraryStore())

    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_items"


@pytest.mark.asyncio
async def test_prev_at_first_returns_cursor_unchanged():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    result = await pc.prev()

    assert result.did_action is False
    assert result.reason == "cursor_unchanged"


@pytest.mark.asyncio
async def test_prev_phantom_cursor_returns_did_action_true():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "GHOST"
    pc, *_ = _make_player(library)

    result = await pc.prev()

    assert result.did_action is True


@pytest.mark.asyncio
async def test_toggle_idle_nothing_playable_returns_reason():
    library = _make_mixed_library([("A", False)])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    result = await pc.toggle()

    assert result.did_action is False
    assert result.reason == "no_playable_item"


# ---------------------------------------------------------------------
# Issue #10: a missing encoded file must not be cast as a dead URL.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cast_current_requeues_when_cache_file_missing():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))  # file vanishes
    woke = MagicMock()
    pc, receiver, q, cc = _make_player(library, on_cache_miss=woke)

    await pc._cast_current()

    assert loads(drain(q)) == []
    assert library.items[0].status == "queued"
    assert library.items[0].filename is None
    woke.assert_called_once()


@pytest.mark.asyncio
async def test_next_skips_and_requeues_stale_cache_item():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "B.mp4"))  # B's file gone
    woke = MagicMock()
    pc, *_ = _make_player(library, on_cache_miss=woke)

    await pc.next()

    assert library.cursor_video_id == "C"
    assert library.items[1].status == "queued"
    assert library.items[1].filename is None
    woke.assert_called()


@pytest.mark.asyncio
async def test_play_missing_file_returns_no_playable():
    library = _make_library(["A"])
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))
    woke = MagicMock()
    pc, receiver, q, cc = _make_player(library, on_cache_miss=woke)

    result = await pc.play("A")

    assert loads(drain(q)) == []
    assert result.did_action is False
    assert result.reason == "no_playable_item"
    assert library.items[0].status == "queued"
    woke.assert_called_once()
