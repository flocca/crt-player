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


def _page_reports(receiver):
    """Emulate the page's once-a-second status report while playing, so a
    test can jump the fake clock further than ReceiverChannel extrapolates."""
    if receiver._position is None or receiver.paused:
        return
    pos = receiver._position + (receiver._clock() - receiver._timestamp)
    receiver.handle_report({
        "event": "status", "position": pos, "duration": receiver.duration,
        "paused": False, "src": receiver._expected_src,
    })


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
    await _launched(pc)

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
    await _launched(pc)

    cc.launch_receiver.assert_called_once()
    assert result.did_action and pc.state == "playing"


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
    _page_reports(receiver)
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
    assert item.playback_position == 599.5  # SEEK_END_MARGIN_S short of the end
    msgs = drain(q)
    assert {"type": "seek", "position": 599.5} in msgs
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
    _page_reports(receiver)
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
    _page_reports(receiver)

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
    _page_reports(receiver)
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
async def test_cast_requeues_when_cache_file_missing():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    os.unlink(os.path.join(config.TEMP_DIR, "A.mp4"))  # file vanishes
    woke = MagicMock()
    pc, receiver, q, cc = _make_player(library, on_cache_miss=woke)

    assert pc._cast(library.items[0]).did_action is False

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


# ---------------------------------------------------------------------
# Menu selection (OK while paused), commands and delete confirm
# ---------------------------------------------------------------------


async def _select_command(pc, key):
    """Move the highlight onto command `key` with next()."""
    for _ in range(50):
        if pc.menu.selected == Entry("command", key):
            return
        await pc.next()
    raise AssertionError(f"command {key} not reachable")


@pytest.mark.asyncio
async def test_ok_on_current_video_resumes():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)

    r = await pc.toggle()

    msgs = drain(q)
    assert {"type": "menu", "view": None} in msgs and {"type": "play"} in msgs
    assert not loads(msgs), "resume must not reload the video"
    assert r.did_action
    assert pc.state == "playing" and pc.menu is None
    assert library.items[1].status == "playing"


@pytest.mark.asyncio
async def test_ok_on_other_ready_video_plays_it():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)

    await pc.next()
    r = await pc.toggle()

    assert r.did_action
    assert library.cursor_video_id == "c"
    assert loads(drain(q))[-1]["url"].endswith("/media/c.mp4")
    assert pc.state == "playing" and pc.menu is None
    assert library.items[1].status == "ready"
    assert library.items[1].playback_position == 30.0


@pytest.mark.asyncio
async def test_ok_on_done_video_with_cache_plays_it():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0)
    library.items[0].status = "done"

    await pc.prev()
    await pc.toggle()

    assert library.cursor_video_id == "a"
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_ok_on_unready_video_shows_notice_and_keeps_cursor():
    library = _make_mixed_library([("a", True), ("b", True), ("c", False)])
    library.items[2].status = "queued"
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("b")
    await pc.toggle()

    await pc.next()
    drain(q)
    r = await pc.toggle()

    assert r.did_action is False and r.reason == "no_playable_item"
    assert library.cursor_video_id == "b"
    assert pc.state == "paused"
    assert menus(drain(q))[-1]["view"]["notice"] == "non ancora pronto"


@pytest.mark.asyncio
async def test_notice_clears_after_notice_s(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()

    await asyncio.sleep(0.05)

    assert menus(drain(q))[-1]["view"]["notice"] is None
    assert pc.menu.notice is None


@pytest.mark.asyncio
async def test_newer_notice_cancels_pending_clear(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.05)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()
    await asyncio.sleep(0.03)
    await pc.toggle()  # second notice restarts the timer
    await asyncio.sleep(0.03)

    assert pc.menu.notice == "Loop disattivato"


@pytest.mark.asyncio
async def test_notice_clear_after_menu_closed_is_harmless(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()
    await pc.stop()
    drain(q)

    await asyncio.sleep(0.05)

    assert menus(drain(q)) == []


@pytest.mark.asyncio
async def test_delete_needs_two_oks_and_targets_paused_video():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    await _select_command(pc, "delete")  # highlight passes over "c" on the way

    await pc.toggle()
    assert pc.menu.confirming_delete and library.cursor_video_id == "b"
    assert menus(drain(q))[-1]["view"]["confirm"] == "Confermi? OK sì"
    assert [i.video_id for i in library.items] == ["a", "b", "c"]

    await pc.toggle()

    assert [i.video_id for i in library.items] == ["a", "c"]
    assert pc.state == "idle" and pc.menu is None


@pytest.mark.asyncio
async def test_delete_confirm_cancelled_by_other_key():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0)
    await _select_command(pc, "delete")
    await pc.toggle()

    await pc.prev()

    assert not pc.menu.confirming_delete
    assert "b" in [i.video_id for i in library.items]


@pytest.mark.asyncio
async def test_loop_command_toggles_and_keeps_menu_open():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)
    await _select_command(pc, "loop")
    drain(q)

    await pc.toggle()

    assert library.loop_mode is True and pc.state == "paused"
    assert menus(drain(q))[-1]["view"]["notice"] == "Loop attivo"
    await pc.toggle()
    assert library.loop_mode is False
    assert menus(drain(q))[-1]["view"]["notice"] == "Loop disattivato"


@pytest.mark.asyncio
async def test_sync_command_calls_callback():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    kick = MagicMock()
    pc.on_sync_request = kick
    await _select_command(pc, "sync")

    await pc.toggle()

    kick.assert_called_once()
    assert menus(drain(q))[-1]["view"]["notice"] == "Sync avviato"
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_sync_command_without_callback_says_unavailable():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "sync")

    await pc.toggle()

    assert menus(drain(q))[-1]["view"]["notice"] == "Sync non disponibile"


@pytest.mark.asyncio
async def test_stop_command_blanks():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 42.0)
    await _select_command(pc, "stop")

    await pc.toggle()

    assert pc.state == "idle" and {"type": "blank"} in drain(q)
    assert library.items[0].playback_position == 42.0


@pytest.mark.asyncio
async def test_delete_current_from_paused_state_stops_first():
    """/control/delete/current (Flipper, no confirm) while paused."""
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)

    await pc.delete_current()

    assert [i.video_id for i in library.items] == ["b"]
    assert pc.state == "idle" and pc.menu is None
    assert {"type": "blank"} in drain(q)


# ---------------------------------------------------------------------
# Calibration inside the page
# ---------------------------------------------------------------------


@pytest.fixture
def fake_clip(monkeypatch):
    gen = AsyncMock(side_effect=lambda path, *a, **k: path)
    monkeypatch.setattr("crt.calibration.generate_calibration_clip", gen)
    return gen


@pytest.mark.asyncio
async def test_calibrate_while_playing_pauses_saves_position_and_returns_to_menu(fake_clip):
    clock = Clock()
    library = _make_library(["a", "b", "c"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("b")
    clock.t += 50
    _page_reports(receiver)
    drain(q)

    await pc.calibrate()

    item_b = library.items[1]
    assert pc.state == "calibrating" and item_b.playback_position == 50.0
    msgs = drain(q)
    assert {"type": "pause"} in msgs
    assert {"type": "menu", "view": None} in msgs
    cal = [m for m in msgs if m["type"] == "calibration"][-1]
    assert cal["url"] == "http://10.0.0.5:8765/media/calibration_pattern.mp4"
    fake_clip.assert_awaited_once()

    r = await pc.next()  # any key ends it, and does nothing else

    assert r.did_action
    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert not loads(msgs)
    assert pc.state == "paused" and pc.menu.selected == Entry("video", "b")
    assert menus(msgs)[-1]["view"] is not None
    assert library.cursor_video_id == "b" and item_b.status != "done"
    assert item_b.playback_position == 50.0


@pytest.mark.asyncio
async def test_calibrate_from_menu_returns_to_menu(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 12.0)
    await _select_command(pc, "calibrate")

    await pc.toggle()
    assert pc.state == "calibrating" and pc.menu is None

    await pc.seek_relative(30)  # ends calibration, no seek

    msgs = drain(q)
    assert not [m for m in msgs if m["type"] == "seek"]
    assert pc.state == "paused" and pc.menu.selected == Entry("video", "a")
    assert library.items[0].playback_position == 12.0


@pytest.mark.asyncio
async def test_calibrate_from_idle_returns_to_blank(fake_clip):
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)

    await pc.calibrate()
    assert pc.state == "calibrating"
    await pc.toggle()

    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs and {"type": "blank"} in msgs
    assert pc.state == "idle" and pc.menu is None
    assert not loads(msgs)


@pytest.mark.asyncio
async def test_calibration_times_out(monkeypatch, fake_clip):
    monkeypatch.setattr("crt.player_core.CALIBRATION_TIMEOUT_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)
    await _select_command(pc, "calibrate")
    await pc.toggle()

    await asyncio.sleep(0.05)

    assert pc.state == "paused"
    assert {"type": "calibration", "url": None} in drain(q)


@pytest.mark.asyncio
async def test_calibration_timer_cancelled_when_ended_by_key(monkeypatch, fake_clip):
    monkeypatch.setattr("crt.player_core.CALIBRATION_TIMEOUT_S", 0.03)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)
    await _select_command(pc, "calibrate")
    await pc.toggle()
    await pc.toggle()  # ends calibration → paused menu
    await pc.toggle()  # OK on the paused video → resume
    drain(q)

    await asyncio.sleep(0.06)

    assert pc.state == "playing"
    assert drain(q) == []


@pytest.mark.asyncio
async def test_live_position_is_zero_while_calibrating(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 70.0)
    await pc.calibrate()

    assert pc.live_position() == 0.0


@pytest.mark.asyncio
async def test_stop_while_calibrating_blanks_and_keeps_position(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 70.0)
    await pc.calibrate()

    await pc.stop()

    assert pc.state == "idle" and {"type": "blank"} in drain(q)
    assert library.items[0].playback_position == 70.0
    assert library.items[0].status == "ready"


@pytest.mark.asyncio
async def test_natural_end_ignored_during_calibration(fake_clip):
    library = _make_library(["A", "B"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("A")
    await pc.calibrate()
    receiver.handle_report({
        "event": "ended", "position": 9.0, "duration": 9.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/A.mp4",
    })

    await _run_watcher_once(pc)

    assert library.cursor_video_id == "A" and library.items[0].status == "paused"


# ---------------------------------------------------------------------
# Lost page, reconnect, idle close
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receiver_blip_within_grace_keeps_pause_and_resends(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.05)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)

    receiver.unsubscribe(q)
    q2 = receiver.subscribe()
    await asyncio.sleep(0.1)

    msgs = drain(q2)
    assert msgs[0]["type"] == "config"
    load = loads(msgs)[-1]
    assert load["autoplay"] is False and load["position"] == 120.0
    assert load["url"].endswith("/media/b.mp4")
    assert menus(msgs)[-1]["view"] is not None
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_receiver_blip_while_playing_resends_autoplay_load(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.05)
    clock = Clock()
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    clock.t += 30
    _page_reports(receiver)

    receiver.unsubscribe(q)
    q2 = receiver.subscribe()
    await asyncio.sleep(0.1)

    load = loads(drain(q2))[-1]
    assert load["autoplay"] is True and load["position"] == 30.0
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_receiver_blip_while_calibrating_restores_video_and_pattern(monkeypatch, fake_clip):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.05)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 70.0)
    await pc.calibrate()

    receiver.unsubscribe(q)
    q2 = receiver.subscribe()
    await asyncio.sleep(0.1)

    msgs = drain(q2)
    load = loads(msgs)[-1]
    assert load["autoplay"] is False and load["position"] == 70.0
    cal = [m for m in msgs if m["type"] == "calibration"][-1]
    assert cal["url"].endswith("/media/calibration_pattern.mp4")
    assert pc.state == "calibrating"


@pytest.mark.asyncio
async def test_receiver_lost_after_grace_goes_idle_and_keeps_position(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)

    receiver.unsubscribe(q)
    await asyncio.sleep(0.05)

    item_b = library.items[1]
    assert pc.state == "idle" and pc.menu is None
    assert item_b.playback_position == 120.0 and item_b.status == "ready"


@pytest.mark.asyncio
async def test_toggle_after_lost_relaunches_and_resumes_from_saved_position(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)
    receiver.unsubscribe(q)
    await asyncio.sleep(0.05)
    cc.receiver_app_active = False
    pages = []
    cc.launch_receiver = MagicMock(side_effect=lambda url: pages.append(receiver.subscribe()))

    await pc.toggle()
    await _launched(pc)

    cc.launch_receiver.assert_called_once()
    load = loads(drain(pages[0]))[-1]
    assert load["position"] == 120.0 and load["url"].endswith("/media/b.mp4")
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_watch_idle_closes_app_after_timeout():
    clock = Clock()
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 33.0, clock=clock)
    clock.t += 1800

    task = asyncio.create_task(pc.watch_idle(1800, poll_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()

    cc.quit_app.assert_called_once()
    item_b = library.items[1]
    assert pc.state == "idle" and item_b.status == "ready"
    assert item_b.playback_position == 33.0 and pc.menu is None


@pytest.mark.asyncio
async def test_watch_idle_waits_for_the_full_timeout():
    clock = Clock()
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0, clock=clock)
    clock.t += 1799

    task = asyncio.create_task(pc.watch_idle(1800, poll_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()

    cc.quit_app.assert_not_called()
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_watch_idle_leaves_playing_alone():
    clock = Clock()
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    clock.t += 100000

    task = asyncio.create_task(pc.watch_idle(1800, poll_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()

    cc.quit_app.assert_not_called()


@pytest.mark.asyncio
async def test_watch_idle_closes_once_after_stop():
    clock = Clock()
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    await pc.stop()
    clock.t += 1800

    task = asyncio.create_task(pc.watch_idle(1800, poll_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()

    cc.quit_app.assert_called_once()


@pytest.mark.asyncio
async def test_watch_idle_does_nothing_when_app_already_closed():
    clock = Clock()
    library = _make_library(["a"])
    cc = _make_chromecast()
    cc.receiver_app_active = False
    pc = PlayerCore(library, cc, ReceiverChannel(MARGINS), clock=clock)
    clock.t += 5000

    task = asyncio.create_task(pc.watch_idle(1800, poll_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()

    cc.quit_app.assert_not_called()


# ---------------------------------------------------------------------
# Final review fixes
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_removal_after_cursor_moved_still_stops_the_loaded_video():
    """The sync thread removes the item and moves the cursor before the loop
    runs stop_and_remove: the video on the page is still the removed one."""
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    library.items.pop(1)
    library.cursor_video_id = "c"

    await pc.stop_and_remove("b")

    assert pc.state == "idle" and pc.menu is None
    assert {"type": "blank"} in drain(q)


@pytest.mark.asyncio
async def test_menu_delete_refuses_when_cursor_left_the_paused_video():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    await _select_command(pc, "delete")
    await pc.toggle()  # armed on "b"
    library.cursor_video_id = "c"  # sync moved the cursor meanwhile

    await pc.toggle()

    assert [i.video_id for i in library.items] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_next_while_playing_saves_the_outgoing_position():
    clock = Clock()
    library = _make_library(["a", "b"])
    library.items[0].playback_position = 10.0
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    clock.t += 2

    await pc.next()

    assert library.items[0].playback_position == 12.0
    assert library.cursor_video_id == "b"


@pytest.mark.asyncio
async def test_picking_a_video_while_the_page_relaunches_loads_it_from_its_own_position(monkeypatch):
    """Page lost within the grace (another app took the Chromecast): picking
    another video relaunches the page, and the reconnect must not load the
    new video at the old video's position."""
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 120.0)
    receiver.unsubscribe(q)
    cc.receiver_app_active = False
    pages = []
    cc.launch_receiver = MagicMock(side_effect=lambda url: pages.append(receiver.subscribe()))

    await pc.next()  # highlight "c"
    await pc.toggle()
    await _launched(pc)

    page_loads = loads(drain(pages[0]))
    assert [m["url"].rsplit("/", 1)[-1] for m in page_loads] == ["c.mp4"]
    assert page_loads[-1]["position"] == 0.0
    assert library.items[1].playback_position == 120.0
    assert library.cursor_video_id == "c" and pc.state == "playing"


@pytest.mark.asyncio
async def test_resume_while_page_is_gone_relaunches_at_saved_position(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)
    receiver.unsubscribe(q)
    cc.receiver_app_active = False
    pages = []
    cc.launch_receiver = MagicMock(side_effect=lambda url: pages.append(receiver.subscribe()))

    r = await pc.toggle()  # OK on the paused video
    await _launched(pc)

    cc.launch_receiver.assert_called_once()
    load = loads(drain(pages[0]))[-1]
    assert load["position"] == 120.0 and load["autoplay"] is True
    assert r.did_action and pc.state == "playing"


@pytest.mark.asyncio
async def test_seek_past_end_while_paused_stops_short_of_the_end():
    """At exactly `duration` the element resumes from 0 instead of ending."""
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 590.0)
    receiver.handle_report({
        "event": "status", "position": 590.0, "duration": 600.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    await pc.seek_relative(30)

    assert library.items[0].playback_position == 599.5


@pytest.mark.asyncio
async def test_second_calibrate_during_clip_generation_is_ignored(monkeypatch):
    gate = asyncio.Event()

    async def slow_clip(path, *a, **k):
        await gate.wait()
        return path

    gen = AsyncMock(side_effect=slow_clip)
    monkeypatch.setattr("crt.calibration.generate_calibration_clip", gen)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)

    first = asyncio.create_task(pc.calibrate())
    second = asyncio.create_task(pc.calibrate())
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.gather(first, second)
    await pc.next()  # ends calibration

    assert gen.await_count == 1
    assert pc.state == "paused" and pc.menu is not None


@pytest.mark.asyncio
async def test_play_while_calibrating_removes_the_pattern(monkeypatch, fake_clip):
    monkeypatch.setattr("crt.player_core.CALIBRATION_TIMEOUT_S", 0.02)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 5.0)
    await pc.calibrate()
    drain(q)

    await pc.play("b")  # TUI /control/play/{id}
    await asyncio.sleep(0.05)

    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert loads(msgs)[-1]["url"].endswith("/media/b.mp4")
    assert pc.state == "playing"


# ---------------------------------------------------------------------
# Commands never wait for the page: the launch runs in the background and
# the page gets the player's state when it connects.
# ---------------------------------------------------------------------


def _player_without_page(library):
    """PlayerCore whose page is not open: launch_receiver opens a new page
    (subscribes a queue, collected in `pages`)."""
    cc = _make_chromecast()
    receiver = ReceiverChannel(MARGINS)
    pages = []
    cc.launch_receiver = MagicMock(side_effect=lambda url: pages.append(receiver.subscribe()))
    pc = PlayerCore(library, cc, receiver)
    return pc, receiver, pages, cc


async def _launched(pc):
    if pc._launch_task is not None:
        await pc._launch_task


@pytest.mark.asyncio
async def test_play_without_page_acks_at_once_and_launches_in_background():
    library = _make_library(["a"])
    pc, receiver, pages, cc = _player_without_page(library)

    result = await pc.play("a")

    assert result.did_action and result.reason == "launching"
    assert pc.state == "playing"
    cc.launch_receiver.assert_not_called()  # not yet: the ack does not wait

    await _launched(pc)

    cc.launch_receiver.assert_called_once()
    load = loads(drain(pages[0]))[-1]
    assert load["url"].endswith("/media/a.mp4") and load["autoplay"] is True


@pytest.mark.asyncio
async def test_stop_during_launch_wins():
    library = _make_library(["a"])
    pc, receiver, pages, cc = _player_without_page(library)
    await pc.play("a")

    await pc.stop()
    await _launched(pc)

    msgs = drain(pages[0])
    assert not loads(msgs)
    assert {"type": "blank"} in msgs
    assert pc.state == "idle" and library.items[0].status == "ready"


@pytest.mark.asyncio
async def test_second_toggle_during_launch_pauses_without_a_second_launch():
    library = _make_library(["a"])
    library.items[0].playback_position = 40.0
    pc, receiver, pages, cc = _player_without_page(library)
    await pc.toggle()

    await pc.toggle()
    await _launched(pc)

    cc.launch_receiver.assert_called_once()
    msgs = drain(pages[0])
    load = loads(msgs)[-1]
    assert load["autoplay"] is False and load["position"] == 40.0
    assert menus(msgs)[-1]["view"] is not None
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_launch_that_never_connects_goes_idle(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_CONNECT_TIMEOUT_S", 0.01)
    library = _make_library(["a"])
    library.items[0].playback_position = 15.0
    cc = _make_chromecast()
    pc = PlayerCore(library, cc, ReceiverChannel(MARGINS))
    await pc.play("a")

    await _launched(pc)

    assert pc.state == "idle"
    assert library.items[0].status == "ready"
    assert library.items[0].playback_position == 15.0


@pytest.mark.asyncio
async def test_launch_that_raises_goes_idle():
    library = _make_library(["a"])
    cc = _make_chromecast()
    cc.launch_receiver = MagicMock(side_effect=RuntimeError("Chromecast not connected"))
    pc = PlayerCore(library, cc, ReceiverChannel(MARGINS))
    await pc.play("a")

    await _launched(pc)

    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_ended_already_woken_is_dropped_after_a_newer_load():
    library = _make_library(["A", "B", "C"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")
    task = asyncio.create_task(pc.watch_natural_end())
    await asyncio.sleep(0)  # watcher waiting
    receiver.handle_report({
        "event": "ended", "position": 9.0, "duration": 9.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/A.mp4",
    })
    await pc.next()  # B loaded before the watcher runs

    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.cursor_video_id == "B"
    assert library.items[1].status == "playing"


# ---------------------------------------------------------------------
# Every new page subscription gets the player's state
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_after_stop_blanks_the_page(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 20.0)
    receiver.unsubscribe(q)
    await pc.stop()  # the page cannot hear it

    q2 = receiver.subscribe()

    msgs = drain(q2)
    assert {"type": "blank"} in msgs and {"type": "menu", "view": None} in msgs
    assert not loads(msgs)


@pytest.mark.asyncio
async def test_new_page_overlapping_a_stale_subscriber_gets_the_state():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)

    q2 = receiver.subscribe()  # the old connection is still listed

    msgs = drain(q2)
    load = loads(msgs)[-1]
    assert load["url"].endswith("/media/b.mp4") and load["position"] == 120.0
    assert load["autoplay"] is False
    assert menus(msgs)[-1]["view"] is not None


# ---------------------------------------------------------------------
# The cursor moves only when a video is actually loaded
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_play_keeps_cursor_and_pause_targets_the_loaded_video():
    library = _make_mixed_library([("A", True), ("B", False)])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("A")

    result = await pc.play("B")
    await pc.toggle()

    assert result.did_action is False
    assert library.cursor_video_id == "A"
    assert pc.state == "paused" and pc.menu.current_video_id == "A"
    assert library.items[0].status == "paused"
    assert library.items[1].status == "encoding"


# ---------------------------------------------------------------------
# Calibration pauses before rendering the clip
# ---------------------------------------------------------------------


def _gated_clip(monkeypatch):
    gate = asyncio.Event()

    async def slow_clip(path, *a, **k):
        await gate.wait()
        return path

    monkeypatch.setattr("crt.calibration.generate_calibration_clip", AsyncMock(side_effect=slow_clip))
    return gate


@pytest.mark.asyncio
async def test_calibrate_pauses_before_rendering(monkeypatch):
    gate = _gated_clip(monkeypatch)
    clock = Clock()
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    clock.t += 30
    _page_reports(receiver)

    task = asyncio.create_task(pc.calibrate())
    await asyncio.sleep(0.01)

    assert pc.state == "paused" and {"type": "pause"} in drain(q)
    assert library.items[0].playback_position == 30.0
    gate.set()
    await task
    assert pc.state == "calibrating"


@pytest.mark.asyncio
async def test_resume_during_rendering_drops_the_calibration(monkeypatch):
    gate = _gated_clip(monkeypatch)
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    task = asyncio.create_task(pc.calibrate())
    await asyncio.sleep(0.01)
    await pc.toggle()  # resume while ffmpeg renders
    gate.set()
    await task

    assert pc.state == "playing"
    assert not [m for m in drain(q) if m["type"] == "calibration"]


# ---------------------------------------------------------------------
# Media errors and menu refresh
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_error_pauses_with_the_menu_and_a_notice():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    receiver.handle_report({
        "event": "error", "error": "media error 3", "media": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    assert pc.state == "paused" and pc.menu is not None
    assert menus(drain(q))[-1]["view"]["notice"] == "Errore nel video"

    await pc.toggle()  # OK on the failed video reloads it

    assert loads(drain(q))[-1]["url"].endswith("/media/a.mp4")
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_non_media_error_and_stale_src_error_are_only_logged():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    receiver.handle_report({
        "event": "error", "error": "play() failed: AbortError",
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })
    receiver.handle_report({
        "event": "error", "error": "media error 2", "media": True,
        "src": "http://10.0.0.5:8765/media/old.mp4",
    })

    assert pc.state == "playing" and pc.menu is None


@pytest.mark.asyncio
async def test_refresh_menu_sends_only_when_the_view_changes():
    library = _make_mixed_library([("a", True), ("b", False)])
    library.items[1].progress = 43.0
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    await pc.toggle()
    drain(q)

    pc.refresh_menu()
    assert menus(drain(q)) == []

    library.items[1].progress = 80.0
    pc.refresh_menu()
    rows = menus(drain(q))[-1]["view"]["rows"]
    assert "80%" in rows[1]["symbol"]


@pytest.mark.asyncio
async def test_refresh_menu_without_menu_is_noop():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    pc.refresh_menu()

    assert drain(q) == []


# ---------------------------------------------------------------------
# Review round 2
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lost_page_timer_leaves_a_pending_launch_alone(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)
    receiver.unsubscribe(q)  # the grace timer starts
    cc.receiver_app_active = False
    cc.launch_receiver = MagicMock()  # the page connects later, below

    await pc.toggle()  # OK on the paused video: relaunch
    await asyncio.sleep(0.05)  # past the grace, launch still waiting
    page = receiver.subscribe()
    await _launched(pc)

    assert pc.state == "playing"
    load = loads(drain(page))[-1]
    assert load["url"].endswith("/media/b.mp4") and load["position"] == 120.0


@pytest.mark.asyncio
async def test_media_error_under_calibration_is_kept_for_the_menu(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 70.0)
    await pc.calibrate()
    receiver.handle_report({
        "event": "error", "error": "media error 3", "media": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    await pc.next()  # ends calibration
    assert pc.menu.notice == "Errore nel video"
    drain(q)

    await pc.toggle()  # OK reloads instead of sending play

    msgs = drain(q)
    assert loads(msgs) and {"type": "play"} not in msgs


@pytest.mark.asyncio
async def test_failed_render_while_playing_opens_the_menu(monkeypatch):
    gen = AsyncMock(side_effect=RuntimeError("ffmpeg failed"))
    monkeypatch.setattr("crt.calibration.generate_calibration_clip", gen)
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    with pytest.raises(RuntimeError):
        await pc.calibrate()

    assert pc.state == "paused" and pc.menu is not None
    assert pc.menu.notice == "Calibrazione non riuscita"


@pytest.mark.asyncio
async def test_resume_and_pause_again_during_rendering_drops_the_calibration(monkeypatch):
    gate = _gated_clip(monkeypatch)
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    task = asyncio.create_task(pc.calibrate())
    await asyncio.sleep(0.01)
    await pc.toggle()  # resume
    await pc.toggle()  # pause again: a new menu
    gate.set()
    await task

    assert pc.state == "paused" and pc.menu is not None
    assert not [m for m in drain(q) if m["type"] == "calibration"]
