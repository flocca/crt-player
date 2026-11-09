import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from crt import config
from crt.library_store import LibraryStore, QueueItem
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


def _as_playing(pc):
    """next/prev from idle open the stopped screen: tests of the cursor walk
    put the player in "playing" first."""
    pc.state = "playing"


def loads(msgs):
    return [m for m in msgs if m["type"] == "load"]


def screens(msgs):
    return [m for m in msgs if m["type"] == "screen"]


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

    _as_playing(pc)
    await pc.next()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_advances_cursor_by_one():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    _as_playing(pc)
    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_prev_moves_cursor_back():
    library = _make_library(["A", "B", "C"])
    library.cursor_video_id = "B"
    pc, *_ = _make_player(library)

    _as_playing(pc)
    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_prev_at_first_item_is_no_op():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    _as_playing(pc)
    await pc.prev()

    assert library.cursor_video_id == "A"


@pytest.mark.asyncio
async def test_next_at_end_no_loop_stops_at_last():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = False
    pc, *_ = _make_player(library)

    _as_playing(pc)
    await pc.next()

    assert library.cursor_video_id == "B"


@pytest.mark.asyncio
async def test_next_at_end_with_loop_wraps_to_first():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "B"
    library.loop_mode = True
    pc, *_ = _make_player(library)

    _as_playing(pc)
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
    assert {"type": "screen", "view": None} in msgs
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

    _as_playing(pc)
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
# Pause opens the teletext screen; up/down/left/right while paused
# ---------------------------------------------------------------------


def _selected_row(view):
    return next(r for r in view["rows"] if r["selected"])


def _labels(view, line=0):
    return [c["label"] for c in view["commands"][line]]


@pytest.mark.asyncio
async def test_pause_opens_screen_on_loaded_video():
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
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "paused"
    assert [r["selected"] for r in view["rows"]] == [False, True, False]
    assert _selected_row(view)["symbol"] == "▶"
    assert _labels(view) == ["Riprendi", "Elimina", "Stop"]
    assert _labels(view, 1) == ["Calibra", "Loop", "Sync", "Spegni"]
    assert view["background"] == "frame"
    assert view["help"] == "OK = Riprendi"
    assert result.did_action
    assert pc.state == "paused" and library.items[1].playback_position == 77.0
    assert library.items[1].status == "paused"
    assert pc.screen.selected_video_id == "b" and pc.screen.loaded_video_id == "b"


@pytest.mark.asyncio
async def test_up_down_move_highlight_with_wrap_while_paused():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 0.0)

    r = await pc.next()

    assert r.did_action and library.cursor_video_id == "b"
    msgs = drain(q)
    assert not loads(msgs)
    assert pc.screen.selected_video_id == "c"
    assert [r["selected"] for r in screens(msgs)[-1]["view"]["rows"]] == [False, False, True]

    await pc.next()  # wraps to the first video
    assert pc.screen.selected_video_id == "a"
    await pc.prev()  # and back to the last
    assert pc.screen.selected_video_id == "c"
    await pc.prev()
    await pc.prev()
    assert pc.screen.selected_video_id == "a"
    assert pc.state == "paused" and not loads(drain(q))


@pytest.mark.asyncio
async def test_left_right_move_commands_while_paused():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 100.0)
    receiver.handle_report({
        "event": "status", "position": 100.0, "duration": 600.0, "paused": True,
        "src": "http://10.0.0.5:8765/media/b.mp4",
    })

    r = await pc.seek_relative(30)

    assert r.did_action and pc.screen.command_index == 1
    msgs = drain(q)
    assert screens(msgs)[-1]["view"]["help"] == "OK = Elimina"
    assert not [m for m in msgs if m["type"] == "seek"]

    await pc.seek_relative(-15)
    await pc.seek_relative(-15)  # wraps to the last command

    assert pc.screen.selected_command(library.items) == "power"
    assert not [m for m in drain(q) if m["type"] == "seek"]
    assert library.items[1].playback_position == 100.0
    assert pc.state == "paused"

    await pc.next()  # a row change brings the selection back to the first command
    assert pc.screen.command_index == 0


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
async def test_stop_while_playing_shows_stopped_screen_on_that_video():
    clock = Clock()
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("b")
    clock.t += 40
    _page_reports(receiver)
    drain(q)

    result = await pc.stop()

    msgs = drain(q)
    blank = msgs.index({"type": "blank"})
    view = screens(msgs)[-1]["view"]
    assert blank < msgs.index(screens(msgs)[-1])
    assert view["state"] == "stopped" and view["status"] is None
    assert _selected_row(view)["num"] == 2 and _selected_row(view)["symbol"] == "○"
    assert view["help"] == "OK = Riprendi"
    cc.quit_app.assert_not_called()
    cc.launch_receiver.assert_not_called()
    assert result.did_action and pc.state == "idle"
    assert pc.screen.selected_video_id == "b" and pc.screen.loaded_video_id is None
    assert pc._loaded_video_id is None


@pytest.mark.asyncio
async def test_stop_when_stopped_is_already_stopped():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)

    first = await pc.stop()  # closed page, nothing loaded

    await pc.play("a")
    await pc.stop()
    drain(q)
    second = await pc.stop()  # on the stopped screen

    for r in (first, second):
        assert r.did_action is False and r.reason == "already_stopped"
    assert drain(q) == []
    assert pc.state == "idle"


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
    assert library.items[0].status == "ready"
    assert pc.screen_state == "stopped"


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
    assert pc.state == "idle" and pc.screen_state == "stopped"


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

    _as_playing(pc)
    await pc.next()

    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_prev_skips_items_without_filename():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "C"
    pc, receiver, q, cc = _make_player(library)

    _as_playing(pc)
    await pc.prev()

    assert library.cursor_video_id == "A"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_next_no_playable_forward_is_noop():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    pc, receiver, q, cc = _make_player(library)

    _as_playing(pc)
    await pc.next()

    assert library.cursor_video_id == "A"
    assert loads(drain(q)) == []


@pytest.mark.asyncio
async def test_next_with_loop_wraps_skipping_unready():
    library = _make_mixed_library([("A", True), ("B", False), ("C", False)])
    library.cursor_video_id = "A"
    library.loop_mode = True
    pc, receiver, q, cc = _make_player(library)

    _as_playing(pc)
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
async def test_toggle_idle_with_cursor_on_unready_does_not_skip_forward():
    library = _make_mixed_library([("A", True), ("B", False), ("C", True)])
    library.cursor_video_id = "B"
    pc, receiver, q, cc = _make_player(library)

    await pc.toggle()

    assert library.cursor_video_id == "B"
    assert loads(drain(q)) == []


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
    await _deletions_done(pc)

    # local removal
    assert all(i.video_id != "A" for i in library.items)
    assert not cache_file.exists()
    # remote removal
    yt.delete_playlist_item.assert_called_once_with("PLITEM_A")
    # stopped first
    assert {"type": "blank"} in drain(q)
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_delete_removes_encoded_preview_and_download(tmp_path):
    """Elimina removes the same files as the sync removal: the stored
    encoded name, its preview copy and the original download."""
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].filename = "A_pal_crop_m4-4-0-0.mp4"
    files = [tmp_path / "A_pal_crop_m4-4-0-0.mp4", tmp_path / "A_pal_crop_m4-4-0-0_noaudio.mp4",
             tmp_path / "A.webm"]
    for f in files:
        f.write_text("dummy")
    library.items[0].downloaded_path = str(files[2])
    pc, receiver, q, cc = _make_player(library)

    await pc.delete_current()
    await asyncio.gather(*pc._delete_tasks)

    assert [f.exists() for f in files] == [False, False, False]


@pytest.mark.asyncio
async def test_delete_current_missing_playlist_item_id_skips_remote():
    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = None
    yt = MagicMock()
    pc, *_ = _make_player(library, youtube_client=yt)

    await pc.delete_current()
    await _deletions_done(pc)

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
    await _deletions_done(pc)

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

    _as_playing(pc)
    await pc.prev()

    assert library.cursor_video_id == "C"
    assert len(loads(drain(q))) == 1


@pytest.mark.asyncio
async def test_prev_with_phantom_cursor_skips_unready():
    library = _make_mixed_library([("A", True), ("B", True), ("C", False)])
    library.cursor_video_id = "GHOST"
    pc, receiver, q, cc = _make_player(library)

    _as_playing(pc)
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

    _as_playing(pc)
    result = await pc.next()

    assert result.did_action is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_next_returns_did_action_false_when_no_playable():
    library = _make_mixed_library([("A", True), ("B", False)])
    library.cursor_video_id = "A"
    library.loop_mode = False
    pc, *_ = _make_player(library)

    _as_playing(pc)
    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_playable_item"


@pytest.mark.asyncio
async def test_next_returns_no_items_on_empty_library():
    pc, *_ = _make_player(LibraryStore())

    _as_playing(pc)
    result = await pc.next()

    assert result.did_action is False
    assert result.reason == "no_items"


@pytest.mark.asyncio
async def test_prev_at_first_returns_cursor_unchanged():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    pc, *_ = _make_player(library)

    _as_playing(pc)
    result = await pc.prev()

    assert result.did_action is False
    assert result.reason == "cursor_unchanged"


@pytest.mark.asyncio
async def test_prev_phantom_cursor_returns_did_action_true():
    library = _make_library(["A", "B"])
    library.cursor_video_id = "GHOST"
    pc, *_ = _make_player(library)

    _as_playing(pc)
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

    _as_playing(pc)
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
# Screen commands (OK while paused), delete confirm
# ---------------------------------------------------------------------


async def _select_command(pc, key):
    """Move the command selection onto `key` with Right (seek forward)."""
    for _ in range(20):
        if pc.screen.selected_command(pc.library.items) == key:
            return
        await pc.seek_relative(30)
    raise AssertionError(f"command {key} not reachable")


@pytest.mark.asyncio
async def test_ok_on_resume_resumes():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)

    r = await pc.toggle()

    msgs = drain(q)
    assert {"type": "screen", "view": None} in msgs and {"type": "play"} in msgs
    assert not loads(msgs), "resume must not reload the video"
    assert r.did_action
    assert pc.state == "playing" and pc.screen is None
    assert library.items[1].status == "playing"


@pytest.mark.asyncio
async def test_ok_on_other_ready_video_plays_it_from_its_position():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    library.items[2].playback_position = 55.0

    await pc.next()
    assert screens(drain(q))[-1]["view"]["help"] == "OK = Riprendi"
    r = await pc.toggle()

    assert r.did_action
    assert library.cursor_video_id == "c"
    msgs = drain(q)
    load = loads(msgs)[-1]
    assert load["url"].endswith("/media/c.mp4") and load["position"] == 55.0
    assert {"type": "screen", "view": None} in msgs
    assert pc.state == "playing" and pc.screen is None
    assert library.items[1].status == "ready"
    assert library.items[1].playback_position == 30.0


@pytest.mark.asyncio
async def test_ok_on_done_video_with_cache_plays_it():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0)
    library.items[0].status = "done"

    await pc.prev()
    assert screens(drain(q))[-1]["view"]["help"] == "OK = Riproduci"
    await pc.toggle()

    assert library.cursor_video_id == "a"
    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_ok_on_unready_video_flashes_not_ready():
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
    assert pc.state == "paused" and pc.screen.loaded_video_id == "b"
    view = screens(drain(q))[-1]["view"]
    assert view["flash"] == "Non ancora pronto" and view["flash_kind"] == "info"


@pytest.mark.asyncio
async def test_notice_clears_after_notice_s(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()

    await asyncio.sleep(0.05)

    assert screens(drain(q))[-1]["view"]["flash"] is None
    assert pc.screen.flash is None


@pytest.mark.asyncio
async def test_newer_notice_cancels_pending_clear(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.05)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()
    await asyncio.sleep(0.03)
    await pc.toggle()  # second notice restarts the timer
    await asyncio.sleep(0.03)

    assert pc.screen.flash == "Loop disattivato"


@pytest.mark.asyncio
async def test_notice_clear_after_screen_closed_is_harmless(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await _select_command(pc, "loop")
    await pc.toggle()
    await pc.stop()
    drain(q)

    await asyncio.sleep(0.05)

    assert screens(drain(q)) == []


@pytest.mark.asyncio
async def test_delete_confirm_then_ok_deletes_highlighted_other_video():
    yt = MagicMock()
    pc, receiver, q, cc, library = await _paused_on(
        ["a", "b", "c"], "b", 30.0, youtube_client=yt,
    )
    library.items[2].playlist_item_id = "PLI_c"
    await pc.next()  # highlight "c"
    await _select_command(pc, "delete")
    drain(q)

    await pc.toggle()

    assert pc.screen.confirm_video_id == "c"
    view = screens(drain(q))[-1]["view"]
    assert view["flash"] == "Eliminare dalla playlist? OK = sì"
    assert view["flash_kind"] == "confirm"
    assert next(r for r in view["rows"] if r["confirm"])["num"] == 3
    assert [i.video_id for i in library.items] == ["a", "b", "c"]

    r = await pc.toggle()
    await _deletions_done(pc)

    assert r.did_action
    assert [i.video_id for i in library.items] == ["a", "b"]
    yt.delete_playlist_item.assert_called_once_with("PLI_c")
    assert pc.state == "paused" and pc._loaded_video_id == "b"
    assert library.cursor_video_id == "b" and library.items[1].status == "paused"
    msgs = drain(q)
    assert {"type": "blank"} not in msgs and not loads(msgs)
    view = screens(msgs)[-1]["view"]
    assert view["count"] == 2 and view["flash"] is None


@pytest.mark.asyncio
async def test_delete_confirm_on_paused_video_stops_then_deletes():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    await _select_command(pc, "delete")
    await pc.toggle()
    drain(q)

    await pc.toggle()

    assert [i.video_id for i in library.items] == ["a", "c"]
    assert pc.state == "idle"
    msgs = drain(q)
    assert {"type": "blank"} in msgs
    # The cursor moves to the successor, as when the sync removes it.
    assert library.cursor_video_id == "c"
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "stopped" and view["count"] == 2
    assert _selected_row(view)["text"] == "c"
    assert view["flash"] is None


@pytest.mark.asyncio
async def test_delete_from_screen_cancels_the_preparation_of_that_video():
    library = _make_mixed_library([("a", True), ("b", False)])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    cancelled = MagicMock()
    pc.on_delete = cancelled
    await pc.play("a")
    await pc.toggle()
    await pc.next()  # highlight "b", still encoding
    await _select_command(pc, "delete")
    await pc.toggle()

    await pc.toggle()

    assert [i.video_id for i in library.items] == ["a"]
    cancelled.assert_called_once_with("b")


@pytest.mark.asyncio
async def test_delete_current_cancels_the_preparation_of_that_video():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library)
    cancelled = MagicMock()
    pc.on_delete = cancelled
    await pc.play("a")

    await pc.delete_current()

    cancelled.assert_called_once_with("a")


@pytest.mark.asyncio
async def test_any_key_cancels_confirm_and_does_nothing_else():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "b", 30.0)
    await _select_command(pc, "delete")
    await pc.toggle()
    drain(q)

    r = await pc.next()

    assert r.did_action
    assert pc.screen.confirm_video_id is None
    assert pc.screen.selected_video_id == "b"
    assert pc.screen.selected_command(library.items) == "delete"
    view = screens(drain(q))[-1]["view"]
    assert view["flash"] is None and not any(r["confirm"] for r in view["rows"])

    await pc.toggle()  # arm again, then Right cancels without moving
    await pc.seek_relative(30)

    assert pc.screen.confirm_video_id is None
    assert pc.screen.selected_command(library.items) == "delete"
    assert [i.video_id for i in library.items] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_ok_after_confirmed_video_removed_deletes_nothing():
    yt = MagicMock()
    pc, receiver, q, cc, library = await _paused_on(
        ["a", "b", "c"], "b", 30.0, youtube_client=yt,
    )
    await pc.next()  # highlight "c"
    await _select_command(pc, "delete")
    await pc.toggle()  # armed on "c"
    library.items.pop(2)  # the sync removes "c" meanwhile

    r = await pc.toggle()

    assert r.did_action is False
    assert [i.video_id for i in library.items] == ["a", "b"]
    yt.delete_playlist_item.assert_not_called()
    assert pc.state == "paused" and pc.screen.confirm_video_id is None


@pytest.mark.asyncio
async def test_stop_command_goes_to_stopped_screen_same_row():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 42.0)
    await _select_command(pc, "stop")
    drain(q)

    r = await pc.toggle()

    assert r.did_action
    msgs = drain(q)
    assert pc.state == "idle" and {"type": "blank"} in msgs
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "stopped"
    assert _selected_row(view)["num"] == 2
    assert view["help"] == "OK = Riprendi" and view["background"] != "frame"
    assert library.items[1].playback_position == 42.0
    assert library.items[1].status == "ready"


@pytest.mark.asyncio
async def test_power_command_saves_and_quits_app():
    clock = Clock()
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("b")
    clock.t += 33
    _page_reports(receiver)
    await pc.toggle()
    await _select_command(pc, "power")

    r = await pc.toggle()

    assert r.did_action
    assert pc.screen is None and pc.state == "idle"
    assert pc._loaded_video_id is None and pc._inactive_since is None
    assert library.items[1].status == "ready"
    assert library.items[1].playback_position == 33.0
    await asyncio.sleep(0.05)  # quit_app runs in a thread, not awaited
    cc.quit_app.assert_called_once()


@pytest.mark.asyncio
async def test_loop_and_sync_commands_flash():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)
    await _select_command(pc, "loop")
    drain(q)

    await pc.toggle()

    assert library.loop_mode is True and pc.state == "paused"
    view = screens(drain(q))[-1]["view"]
    assert view["flash"] == "Loop attivo" and view["loop"] is True
    await pc.toggle()
    assert library.loop_mode is False
    assert screens(drain(q))[-1]["view"]["flash"] == "Loop disattivato"

    await _select_command(pc, "sync")
    await pc.toggle()  # no callback wired
    assert screens(drain(q))[-1]["view"]["flash"] == "Sync non disponibile"

    kick = MagicMock()
    pc.on_sync_request = kick
    r = await pc.toggle()

    assert r.did_action
    kick.assert_called_once()
    assert screens(drain(q))[-1]["view"]["flash"] == "Sync avviato"
    assert pc.state == "paused"


@pytest.mark.asyncio
async def test_delete_current_from_paused_state_stops_first():
    """/control/delete/current (Flipper, no confirm) while paused."""
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)

    await pc.delete_current()

    assert [i.video_id for i in library.items] == ["b"]
    assert pc.state == "idle" and pc.screen_state == "stopped"
    msgs = drain(q)
    assert {"type": "blank"} in msgs
    view = screens(msgs)[-1]["view"]
    assert view["count"] == 1 and _selected_row(view)["text"] == "b"


# ---------------------------------------------------------------------
# Calibration inside the page
# ---------------------------------------------------------------------


@pytest.fixture
def fake_clip(monkeypatch):
    """The pattern the daemon renders at startup (prepare_calibration) is
    ready for every PlayerCore of the test."""
    path = os.path.join(config.TEMP_DIR, "calibration_pattern.mp4")
    monkeypatch.setattr(PlayerCore, "_calibration_clip", path)
    return path


@pytest.mark.asyncio
async def test_calibrate_while_playing_pauses_saves_position_and_returns_to_screen(fake_clip):
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
    assert {"type": "screen", "view": None} in msgs
    cal = [m for m in msgs if m["type"] == "calibration"][-1]
    assert cal["url"] == "http://10.0.0.5:8765/media/calibration_pattern.mp4"

    r = await pc.next()  # any key ends it, and does nothing else

    assert r.did_action
    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert not loads(msgs)
    assert pc.state == "paused" and pc.screen.selected_video_id == "b"
    assert screens(msgs)[-1]["view"]["state"] == "paused"
    assert library.cursor_video_id == "b" and item_b.status != "done"
    assert item_b.playback_position == 50.0


@pytest.mark.asyncio
async def test_calibrate_from_screen_returns_to_the_same_screen(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 12.0)
    await pc.next()  # highlight "b"
    await _select_command(pc, "calibrate")
    drain(q)

    await pc.toggle()
    assert pc.state == "calibrating"
    msgs = drain(q)
    assert {"type": "screen", "view": None} in msgs

    await pc.seek_relative(30)  # ends calibration, no seek, no command move

    msgs = drain(q)
    assert not [m for m in msgs if m["type"] == "seek"]
    assert pc.state == "paused" and pc.screen.selected_video_id == "b"
    assert pc.screen.selected_command(library.items) == "calibrate"
    assert screens(msgs)[-1]["view"]["state"] == "paused"
    assert library.items[0].playback_position == 12.0


@pytest.mark.asyncio
async def test_calibrate_from_closed_page_ends_on_stopped_screen(fake_clip):
    library = _make_library(["a", "b"])
    library.cursor_video_id = "b"
    pc, receiver, q, cc = _make_player(library)

    await pc.calibrate()
    assert pc.state == "calibrating"
    await pc.toggle()

    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert pc.state == "idle" and pc.screen_state == "stopped"
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "stopped" and _selected_row(view)["text"] == "b"
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
    await pc.toggle()  # ends calibration → paused screen
    await pc.prev()  # a row move brings back the first command (Riprendi)
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

    msgs = drain(q)
    assert pc.state == "idle" and {"type": "blank"} in msgs
    assert screens(msgs)[-1]["view"]["state"] == "stopped"
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
    assert screens(msgs)[-1]["view"] is not None
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
    assert pc.state == "idle" and pc.screen is None
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
    assert item_b.playback_position == 33.0 and pc.screen is None


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

    assert pc.state == "idle" and pc.screen_state == "stopped"
    msgs = drain(q)
    assert {"type": "blank"} in msgs
    assert _selected_row(screens(msgs)[-1]["view"])["text"] == "c"


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
async def test_seek_past_end_while_playing_stops_short_of_the_end():
    """At exactly `duration` the element resumes from 0 instead of ending."""
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("a")
    receiver.handle_report({
        "event": "status", "position": 590.0, "duration": 600.0, "paused": False,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })
    drain(q)

    await pc.seek_relative(30)

    assert drain(q) == [{"type": "seek", "position": 599.5}]


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
    (subscribes a queue, collected in `pages`). The page subscribes on the
    loop, as a real one does through the SSE endpoint."""
    cc = _make_chromecast()
    receiver = ReceiverChannel(MARGINS)
    pages = []
    loop = asyncio.get_running_loop()
    cc.launch_receiver = MagicMock(
        side_effect=lambda url: loop.call_soon_threadsafe(
            lambda: pages.append(receiver.subscribe()),
        ),
    )
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
    assert screens(msgs)[-1]["view"] is not None
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
async def test_reconnect_after_stop_shows_the_stopped_screen(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 20.0)
    receiver.unsubscribe(q)
    await pc.stop()  # the page cannot hear it

    q2 = receiver.subscribe()

    msgs = drain(q2)
    assert {"type": "blank"} in msgs
    assert screens(msgs)[-1]["view"]["state"] == "stopped"
    assert not loads(msgs)


@pytest.mark.asyncio
async def test_new_page_overlapping_a_stale_subscriber_gets_the_state():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 120.0)

    q2 = receiver.subscribe()  # the old connection is still listed

    msgs = drain(q2)
    load = loads(msgs)[-1]
    assert load["url"].endswith("/media/b.mp4") and load["position"] == 120.0
    assert load["autoplay"] is False
    assert screens(msgs)[-1]["view"] is not None


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
    assert pc.state == "paused" and pc.screen.loaded_video_id == "A"
    assert library.items[0].status == "paused"
    assert library.items[1].status == "encoding"


# ---------------------------------------------------------------------
# Calibration pauses before rendering the clip
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# Media errors and screen refresh
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_error_pauses_with_the_screen_and_a_flash():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    receiver.handle_report({
        "event": "error", "error": "media error 3", "media": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    assert pc.state == "paused" and pc.screen is not None
    view = screens(drain(q))[-1]["view"]
    assert view["flash"] == "Errore nel video" and view["flash_kind"] == "error"

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

    assert pc.state == "playing" and pc.screen is None


@pytest.mark.asyncio
async def test_media_error_flash_stays_until_a_key(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    receiver.handle_report({
        "event": "error", "error": "media error 3", "media": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    await asyncio.sleep(0.05)  # longer than a short notice

    assert pc.screen.flash == "Errore nel video"
    drain(q)

    await pc.next()

    assert pc.screen.flash is None
    assert screens(drain(q))[-1]["view"]["flash"] is None


@pytest.mark.asyncio
async def test_refresh_screen_sends_only_on_change():
    library = _make_mixed_library([("a", True), ("b", False)])
    library.items[1].progress = 43.0
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    await pc.toggle()
    drain(q)

    pc.refresh_screen()
    assert screens(drain(q)) == []

    library.items[1].progress = 80.0
    pc.refresh_screen()
    rows = screens(drain(q))[-1]["view"]["rows"]
    assert rows[1]["right"] == "80%"

    pc.refresh_screen()
    assert screens(drain(q)) == []


@pytest.mark.asyncio
async def test_refresh_screen_without_screen_is_noop():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    pc.refresh_screen()

    assert drain(q) == []


@pytest.mark.asyncio
async def test_refresh_screen_during_calibration_sends_nothing(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)
    await pc.calibrate()
    library.items[0].playback_position = 9.0
    drain(q)

    pc.refresh_screen()

    assert drain(q) == []


@pytest.mark.asyncio
async def test_view_carries_preview_url_and_sync_info():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "a", 5.0)
    _write_cache_file("b_noaudio.mp4")  # "c" has no audio-less copy yet
    library.items[1].playback_position = 50.0
    pc.sync_info = lambda: ("2026-11-08T20:10:00+00:00", True)

    await pc.next()

    view = screens(drain(q))[-1]["view"]
    assert view["background"] == "preview"
    assert view["preview"]["url"] == "http://10.0.0.5:8765/media/b_noaudio.mp4"
    assert (view["preview"]["start"], view["preview"]["end"]) == (30.0, 50.0)
    assert view["sync_at"] == "2026-11-08T20:10:00+00:00"
    assert view["sync_error"] is True

    await pc.next()

    view = screens(drain(q))[-1]["view"]
    assert view["background"] == "black" and view["preview"] is None


@pytest.mark.asyncio
async def test_view_without_sync_info_says_never_synced():
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)

    await pc.next()

    view = screens(drain(q))[-1]["view"]
    assert view["sync_at"] is None and view["sync_error"] is False


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
async def test_media_error_under_calibration_is_kept_for_the_screen(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 70.0)
    await pc.calibrate()
    receiver.handle_report({
        "event": "error", "error": "media error 3", "media": True,
        "src": "http://10.0.0.5:8765/media/a.mp4",
    })

    await pc.next()  # ends calibration
    assert pc.screen.flash == "Errore nel video" and pc.screen.flash_kind == "error"
    drain(q)

    await pc.toggle()  # OK reloads instead of sending play

    msgs = drain(q)
    assert loads(msgs) and {"type": "play"} not in msgs


# ---------------------------------------------------------------------
# Review round 3: no await left in calibrate, one owner for "page lost"
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_calibration_renders_once_and_calibrate_uses_it(monkeypatch):
    gen = AsyncMock(side_effect=lambda path, *a, **k: path)
    monkeypatch.setattr("crt.calibration.generate_calibration_clip", gen)
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)

    await pc.prepare_calibration()
    await pc.calibrate()
    await pc.next()  # ends it
    await pc.calibrate()

    gen.assert_awaited_once()
    assert pc.state == "calibrating"
    cal = [m for m in drain(q) if m["type"] == "calibration"][-1]
    assert cal["url"].endswith("/media/calibration_pattern.mp4")


@pytest.mark.asyncio
async def test_calibrate_before_the_pattern_is_ready_only_says_so(monkeypatch):
    gen = AsyncMock(side_effect=RuntimeError("ffmpeg failed"))
    monkeypatch.setattr("crt.calibration.generate_calibration_clip", gen)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)

    await pc.prepare_calibration()  # failure is logged, not raised
    await pc.calibrate()

    assert pc.state == "paused"
    assert pc.screen.flash == "Calibrazione non pronta"
    assert not [m for m in drain(q) if m["type"] == "calibration"]


@pytest.mark.asyncio
async def test_calibrate_while_playing_without_pattern_keeps_playing():
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    await pc.calibrate()

    assert pc.state == "playing"


@pytest.mark.asyncio
async def test_page_lost_during_a_launch_is_checked_when_the_launch_ends(monkeypatch):
    """The new page connects and drops while launch_receiver still blocks:
    once the launch ends, nobody is connected and the player goes idle."""
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    library = _make_library(["a"])
    cc = _make_chromecast()
    receiver = ReceiverChannel(MARGINS)
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()

    def blip():
        receiver.unsubscribe(receiver.subscribe())  # connects, then drops

    def slow_launch(url):
        loop.call_soon_threadsafe(blip)
        asyncio.run_coroutine_threadsafe(gate.wait(), loop).result()

    cc.launch_receiver = MagicMock(side_effect=slow_launch)
    pc = PlayerCore(library, cc, receiver)
    await pc.play("a")
    await asyncio.sleep(0.05)  # past the grace, launch still blocking
    assert pc.state == "playing"

    gate.set()
    await _launched(pc)
    await asyncio.sleep(0.01)

    assert pc.state == "idle"


# ---------------------------------------------------------------------
# Stopped screen, closed page, night phase
# ---------------------------------------------------------------------


async def _stopped_on(library_ids, video_id, clock=None, **kwargs):
    """Library with every id ready, `video_id` played then stopped: the
    stopped screen highlights it. Returns (pc, receiver, q, cc, library)."""
    library = _make_library(library_ids)
    pc, receiver, q, cc = _make_player(library, clock=clock or Clock(), **kwargs)
    await pc.play(video_id)
    await pc.stop()
    drain(q)
    return pc, receiver, q, cc, library


@pytest.mark.asyncio
async def test_end_of_playlist_highlights_first_not_done_with_notice(monkeypatch):
    monkeypatch.setattr("crt.player_core.ARRIVAL_NOTICE_S", 0.01)
    library = _make_library(["a", "b", "c"])
    library.items[0].status = "done"
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("c")
    drain(q)

    await pc.on_playback_finished()

    msgs = drain(q)
    assert {"type": "blank"} in msgs
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "stopped" and view["status"] == "Fine della coda"
    assert _selected_row(view)["text"] == "b"
    assert pc.state == "idle"

    await asyncio.sleep(0.05)

    assert screens(drain(q))[-1]["view"]["status"] is None
    assert pc.screen.arrival is None


@pytest.mark.asyncio
async def test_end_of_playlist_all_done_highlights_last():
    library = _make_library(["a", "b"])
    library.items[0].status = "done"
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("b")
    drain(q)

    await pc.on_playback_finished()

    view = screens(drain(q))[-1]["view"]
    assert _selected_row(view)["num"] == 2 and view["status"] == "Fine della coda"


@pytest.mark.asyncio
async def test_sync_removing_playing_video_shows_removed_notice():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("a")
    library.items.pop(0)  # the sync thread removed it and moved the cursor
    library.cursor_video_id = "b"
    drain(q)

    await pc.stop_and_remove("a")

    view = screens(drain(q))[-1]["view"]
    assert view["status"] == "Video rimosso dalla playlist"
    assert view["state"] == "stopped" and _selected_row(view)["text"] == "b"
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_calibration_from_stopped_returns_to_same_screen(fake_clip):
    pc, receiver, q, cc, library = await _stopped_on(["a", "b", "c"], "a")
    await pc.next()  # highlight "b"
    await _select_command(pc, "calibrate")

    await pc.toggle()
    assert pc.state == "calibrating"
    drain(q)
    await pc.next()  # ends calibration, does nothing else

    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert pc.state == "idle"
    assert pc.screen.selected_video_id == "b"
    assert pc.screen.selected_command(library.items) == "calibrate"
    assert screens(msgs)[-1]["view"]["state"] == "stopped"
    assert not loads(msgs)


@pytest.mark.asyncio
async def test_calibrate_cancels_a_pending_confirmation(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 5.0)
    await _select_command(pc, "delete")
    await pc.toggle()  # armed on "a"

    await pc.calibrate()  # Flipper Commands → Calibrate
    await pc.next()  # ends it

    assert pc.state == "paused" and pc.screen.confirm_video_id is None
    view = screens(drain(q))[-1]["view"]
    assert view["flash_kind"] != "confirm" and not any(r["confirm"] for r in view["rows"])
    assert [i.video_id for i in library.items] == ["a", "b"]


@pytest.mark.asyncio
async def test_explicit_commands_cancel_a_pending_confirmation():
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")
    kick = MagicMock()
    pc.on_sync_request = kick

    for command in (pc.toggle_loop, pc.request_sync, pc.stop):
        await _select_command(pc, "delete")
        await pc.toggle()
        assert pc.screen.confirm_video_id == "a"
        r = command()
        if asyncio.iscoroutine(r):
            await r
        assert pc.screen.confirm_video_id is None, command

    assert library.loop_mode is True
    kick.assert_called_once()
    await pc.toggle()  # the selection is still on "delete": arms again
    await pc.play("b")
    assert pc.state == "playing"
    assert [i.video_id for i in library.items] == ["a", "b"]


@pytest.mark.asyncio
async def test_reconnect_while_stopped_resends_screen(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")
    await pc.next()  # highlight "b"
    sent = screens(drain(q))[-1]["view"]

    receiver.unsubscribe(q)
    q2 = receiver.subscribe()

    msgs = drain(q2)
    assert {"type": "blank"} in msgs and not loads(msgs)
    assert screens(msgs)[-1]["view"] == sent
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_up_down_while_stopped_move_highlight_no_cast():
    pc, receiver, q, cc, library = await _stopped_on(["a", "b", "c"], "a")

    r = await pc.next()

    assert r.did_action and pc.screen.selected_video_id == "b"
    msgs = drain(q)
    assert not loads(msgs)
    assert _selected_row(screens(msgs)[-1]["view"])["text"] == "b"
    await pc.prev()
    await pc.prev()  # wraps to the last
    assert pc.screen.selected_video_id == "c"
    assert pc.state == "idle" and library.cursor_video_id == "a"
    assert not loads(drain(q))


@pytest.mark.asyncio
async def test_ok_while_stopped_runs_selected_command():
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")
    library.items[1].playback_position = 12.0
    await pc.next()  # highlight "b"
    drain(q)

    r = await pc.toggle()

    assert r.did_action and pc.state == "playing"
    load = loads(drain(q))[-1]
    assert load["url"].endswith("/media/b.mp4") and load["position"] == 12.0
    assert library.cursor_video_id == "b" and pc.screen is None


@pytest.mark.asyncio
async def test_closed_page_up_opens_stopped_screen_from_cursor():
    library = _make_library(["a", "b", "c"])
    library.cursor_video_id = "a"
    pc, receiver, pages, cc = _player_without_page(library)

    r = await pc.next()

    assert r.did_action and r.reason == "launching"
    assert pc.state == "idle" and pc.screen.selected_video_id == "b"
    await _launched(pc)
    cc.launch_receiver.assert_called_once()
    msgs = drain(pages[0])
    assert not loads(msgs)
    view = screens(msgs)[-1]["view"]
    assert view["state"] == "stopped" and _selected_row(view)["text"] == "b"
    assert library.cursor_video_id == "a"


@pytest.mark.asyncio
async def test_closed_page_ok_plays_ready_cursor():
    library = _make_library(["a", "b"])
    library.cursor_video_id = "b"
    library.items[1].playback_position = 7.0
    pc, receiver, pages, cc = _player_without_page(library)

    r = await pc.toggle()
    await _launched(pc)

    assert r.did_action and pc.state == "playing"
    load = loads(drain(pages[0]))[-1]
    assert load["url"].endswith("/media/b.mp4") and load["position"] == 7.0


@pytest.mark.asyncio
async def test_closed_page_ok_on_unready_cursor_shows_not_ready():
    library = _make_mixed_library([("a", True), ("b", False), ("c", True)])
    library.cursor_video_id = "b"
    pc, receiver, pages, cc = _player_without_page(library)

    r = await pc.toggle()

    # The page is being opened: a performed action, not a no-op.
    assert r.did_action and r.reason == "launching"
    assert pc.state == "idle" and library.cursor_video_id == "b"
    assert pc.screen.selected_video_id == "b"
    assert pc.screen.flash == "Non ancora pronto"
    await _launched(pc)
    cc.launch_receiver.assert_called_once()
    msgs = drain(pages[0])
    assert not loads(msgs)
    assert screens(msgs)[-1]["view"]["flash"] == "Non ancora pronto"


@pytest.mark.asyncio
async def test_closed_page_seek_opens_screen_with_command_moved():
    library = _make_library(["a", "b"])
    library.cursor_video_id = "a"
    pc, receiver, pages, cc = _player_without_page(library)

    r = await pc.seek_relative(30)

    assert r.did_action and r.reason == "launching"
    assert pc.screen.selected_video_id == "a" and pc.screen.command_index == 1
    await _launched(pc)
    msgs = drain(pages[0])
    assert not [m for m in msgs if m["type"] == "seek"]
    assert screens(msgs)[-1]["view"]["help"] == "OK = Elimina"


@pytest.mark.asyncio
async def test_night_after_timeout_then_first_key_only_wakes(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")

    await asyncio.sleep(0.05)

    assert pc.screen.night is True
    assert screens(drain(q))[-1]["view"]["night"] is True

    r = await pc.next()

    assert r.did_action and r.reason == "woke"
    assert pc.screen.night is False and pc.screen.selected_video_id == "a"
    view = screens(drain(q))[-1]["view"]
    assert view["night"] is False and _selected_row(view)["text"] == "a"

    await pc.next()  # awake now: the key acts
    assert pc.screen.selected_video_id == "b"


@pytest.mark.asyncio
async def test_night_while_paused_first_ok_only_wakes(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 5.0)

    await asyncio.sleep(0.05)
    r = await pc.toggle()

    assert r.reason == "woke" and pc.state == "paused" and pc.screen.night is False
    assert {"type": "play"} not in drain(q)


@pytest.mark.asyncio
async def test_explicit_command_in_night_wakes_and_acts(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 5.0)
    await asyncio.sleep(0.05)
    assert pc.screen.night is True
    drain(q)

    r = pc.toggle_loop()

    assert r.did_action and library.loop_mode is True
    assert pc.screen.night is False
    view = screens(drain(q))[-1]["view"]
    assert view["night"] is False and view["flash"] == "Loop attivo"


@pytest.mark.asyncio
async def test_refresh_during_night_keeps_night_and_timer(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.1)
    clock = Clock()
    library = _make_mixed_library([("a", True), ("b", False)])
    library.items[1].progress = 10.0
    pc, receiver, q, cc = _make_player(library, clock=clock)
    await pc.play("a")
    await pc.stop()
    inactive_since = pc._inactive_since

    await asyncio.sleep(0.06)
    clock.t += 60
    library.items[1].progress = 20.0
    pc.refresh_screen()  # a redraw is not a key: the timer is not re-armed
    await asyncio.sleep(0.07)

    assert pc.screen.night is True
    assert pc._inactive_since == inactive_since
    drain(q)

    library.items[1].progress = 30.0
    pc.refresh_screen()

    view = screens(drain(q))[-1]["view"]
    assert view["night"] is True and view["rows"][1]["right"] == "30%"
    assert pc.screen.night is True and pc._inactive_since == inactive_since


@pytest.mark.asyncio
async def test_reconnect_resends_night_and_confirm(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 5.0)
    await pc.next()  # highlight "b"
    await _select_command(pc, "delete")
    await pc.toggle()  # armed on "b"
    await asyncio.sleep(0.05)

    receiver.unsubscribe(q)
    q2 = receiver.subscribe()

    view = screens(drain(q2))[-1]["view"]
    assert view["night"] is True and view["flash_kind"] == "confirm"
    assert next(r for r in view["rows"] if r["confirm"])["text"] == "b"


@pytest.mark.asyncio
async def test_idle_close_counts_from_last_key():
    clock = Clock()
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a", clock=clock)

    clock.t += 1500
    await pc.next()  # a key on the screen
    clock.t += 1500  # 50 min from the stop, 25 from the key
    await pc._close_if_idle(1800)

    cc.quit_app.assert_not_called()
    assert pc.screen_state == "stopped"

    clock.t += 300  # 30 min from the key
    await pc._close_if_idle(1800)

    cc.quit_app.assert_called_once()
    assert pc.screen is None and pc.screen_state == "none"


@pytest.mark.asyncio
async def test_screen_state_and_selected_video_id():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    assert (pc.screen_state, pc.selected_video_id) == ("none", None)

    await pc.play("a")
    assert (pc.screen_state, pc.selected_video_id) == ("none", None)

    await pc.toggle()
    assert (pc.screen_state, pc.selected_video_id) == ("paused", "a")

    await pc.next()
    assert pc.selected_video_id == "b"

    await pc.stop()
    assert (pc.screen_state, pc.selected_video_id) == ("stopped", "a")


@pytest.mark.asyncio
async def test_ok_on_empty_library_runs_player_command(fake_clip):
    pc, receiver, q, cc, library = await _stopped_on(["a"], "a")
    library.items.clear()  # the sync removed everything
    library.cursor_video_id = None
    pc.refresh_screen()

    view = screens(drain(q))[-1]["view"]
    assert view["help"] == "OK = Calibra" and view["count"] == 0

    await pc.toggle()

    assert pc.state == "calibrating"
    assert not loads(drain(q))


# ---------------------------------------------------------------------
# Review fixes: notices on a page that opens late, no launch over another
# app, highlighted row removed by a library change
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_page_not_ready_notice_counts_from_the_page_connecting(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.03)
    library = _make_mixed_library([("a", False)])
    library.cursor_video_id = "a"
    cc = _make_chromecast()
    receiver = ReceiverChannel(MARGINS)
    cc.launch_receiver = MagicMock()  # the page connects later, below
    pc = PlayerCore(library, cc, receiver)

    await pc.toggle()
    await asyncio.sleep(0.06)  # longer than the notice, page still opening

    assert pc.screen.flash == "Non ancora pronto"
    page = receiver.subscribe()
    await _launched(pc)
    assert screens(drain(page))[-1]["view"]["flash"] == "Non ancora pronto"

    await asyncio.sleep(0.06)

    assert pc.screen.flash is None
    assert screens(drain(page))[-1]["view"]["flash"] is None


@pytest.mark.asyncio
async def test_stop_with_page_not_ready_does_not_launch(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 5.0)
    library = _make_library(["a"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("a")
    cc.receiver_app_active = False  # another app took the Chromecast
    receiver.unsubscribe(q)

    r = await pc.stop()
    await asyncio.sleep(0)

    assert r.did_action and pc.screen_state == "stopped"
    assert pc._launch_task is None
    cc.launch_receiver.assert_not_called()
    q2 = receiver.subscribe()  # a reconnect within the grace shows it
    assert screens(drain(q2))[-1]["view"]["state"] == "stopped"


@pytest.mark.asyncio
async def test_stop_and_remove_with_page_not_ready_does_not_launch(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("a")
    cc.receiver_app_active = False
    receiver.unsubscribe(q)
    library.items.pop(0)
    library.cursor_video_id = "b"

    await pc.stop_and_remove("a")
    await asyncio.sleep(0.05)  # past the grace

    assert pc._launch_task is None
    cc.launch_receiver.assert_not_called()
    assert pc.state == "idle" and pc.screen is None


@pytest.mark.asyncio
async def test_sync_removing_highlighted_row_moves_to_cursor_with_notice(monkeypatch):
    monkeypatch.setattr("crt.player_core.ARRIVAL_NOTICE_S", 0.02)
    clock = Clock()
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "a", 5.0, clock=clock)
    await pc.next()  # highlight "b"
    inactive_since = pc._inactive_since
    night_timer = pc._night_handle
    clock.t += 60
    drain(q)
    library.items.pop(1)  # the sync removes "b"

    pc.refresh_screen()

    view = screens(drain(q))[-1]["view"]
    assert view["status"] == "Video rimosso dalla playlist"
    assert _selected_row(view)["text"] == "a" and pc.state == "paused"
    assert pc._inactive_since == inactive_since and pc._night_handle is night_timer

    await asyncio.sleep(0.05)

    assert screens(drain(q))[-1]["view"]["status"] is None


@pytest.mark.asyncio
async def test_removed_highlighted_row_on_stopped_screen_during_night_stays_night(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")
    await asyncio.sleep(0.05)
    assert pc.screen.night is True
    drain(q)
    library.items.pop(0)  # the sync removes the highlighted "a"
    library.cursor_video_id = "b"

    pc.refresh_screen()

    view = screens(drain(q))[-1]["view"]
    assert view["night"] is True and view["status"] == "Video rimosso dalla playlist"
    assert _selected_row(view)["text"] == "b" and pc.screen.night is True


@pytest.mark.asyncio
async def test_delete_from_screen_says_the_row_was_removed():
    pc, receiver, q, cc, library = await _stopped_on(["a", "b"], "a")
    await _select_command(pc, "delete")
    await pc.toggle()
    drain(q)

    await pc.toggle()  # confirm

    view = screens(drain(q))[-1]["view"]
    assert view["status"] == "Video rimosso dalla playlist"
    assert _selected_row(view)["text"] == "b"


@pytest.mark.asyncio
async def test_sync_removal_of_paused_video_during_night_stays_night(monkeypatch):
    monkeypatch.setattr("crt.player_core.NIGHT_AFTER_S", 0.01)
    clock = Clock()
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 5.0, clock=clock)
    await asyncio.sleep(0.05)
    assert pc.screen.night is True
    inactive_since = pc._inactive_since
    clock.t += 60
    library.items.pop(0)
    library.cursor_video_id = "b"

    await pc.stop_and_remove("a")

    view = screens(drain(q))[-1]["view"]
    assert view["state"] == "stopped" and view["night"] is True
    assert view["status"] == "Video rimosso dalla playlist"
    assert pc.screen.night is True and pc._inactive_since == inactive_since
    assert pc._night_handle is None


# ---------------------------------------------------------------------
# delete_request (/control/delete/current)
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_request_while_playing_is_immediate():
    library = _make_library(["a", "b"])
    library.cursor_video_id = "a"
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")
    drain(q)

    r = await pc.delete_request()

    assert r.did_action and r.reason is None
    assert [i.video_id for i in library.items] == ["b"]
    assert pc.state == "idle"


@pytest.mark.asyncio
async def test_delete_request_on_screen_asks_confirmation_on_highlighted_row():
    pc, receiver, q, cc, library = await _paused_on(["a", "b", "c"], "a", 3.0)
    await pc.next()  # highlight b
    drain(q)

    r = await pc.delete_request()

    assert r.did_action
    assert pc.screen.confirm_video_id == "b"
    assert [i.video_id for i in library.items] == ["a", "b", "c"]
    view = screens(drain(q))[-1]["view"]
    assert view["flash_kind"] == "confirm"
    assert next(row for row in view["rows"] if row["confirm"])["text"] == "b"


@pytest.mark.asyncio
async def test_second_delete_request_cancels():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)
    await pc.delete_request()

    r = await pc.delete_request()

    assert r.did_action
    assert pc.screen.confirm_video_id is None
    assert [i.video_id for i in library.items] == ["a", "b"]


@pytest.mark.asyncio
async def test_delete_request_wakes_night_and_acts():
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)
    pc._enter_night()
    assert pc.screen.night

    r = await pc.delete_request()

    assert r.did_action and r.reason is None
    assert pc.screen.night is False and pc.screen.confirm_video_id == "a"


@pytest.mark.asyncio
async def test_delete_request_with_closed_page_opens_screen_confirming_cursor():
    library = _make_library(["a", "b", "c"])
    library.cursor_video_id = "b"
    pc, receiver, pages, cc = _player_without_page(library)

    r = await pc.delete_request()

    assert r.did_action and r.reason == "launching"
    assert pc.state == "idle" and pc.screen_state == "stopped"
    assert pc.screen.selected_video_id == "b" and pc.screen.confirm_video_id == "b"
    assert [i.video_id for i in library.items] == ["a", "b", "c"]
    await _launched(pc)
    cc.launch_receiver.assert_called_once()
    view = screens(drain(pages[0]))[-1]["view"]
    assert view["flash_kind"] == "confirm"


@pytest.mark.asyncio
async def test_delete_request_on_empty_library_is_no_items():
    library = _make_library([])
    pc, receiver, q, cc = _make_player(library)

    r = await pc.delete_request()

    assert r.did_action is False and r.reason == "no_items"
    assert pc.screen is None


@pytest.mark.asyncio
async def test_delete_request_during_calibration_ends_it_then_asks(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "a", 3.0)
    await pc.calibrate()
    assert pc.state == "calibrating"

    r = await pc.delete_request()

    assert r.did_action
    assert pc.state == "paused" and pc.screen.confirm_video_id == "a"


@pytest.mark.asyncio
async def test_screen_state_is_none_while_calibrating(fake_clip):
    pc, receiver, q, cc, library = await _paused_on(["a"], "a", 3.0)
    await pc.calibrate()

    assert pc.screen is not None and pc.screen_state == "none"
    assert pc.selected_video_id is None


@pytest.mark.asyncio
async def test_second_delete_request_after_immediate_delete_deletes_nothing_more():
    """Bridge retry of delete/current while playing: the first deletes the
    video on air, the retry lands on the stopped screen and only asks."""
    library = _make_library(["a", "b"])
    library.cursor_video_id = "a"
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    await pc.delete_request()
    r = await pc.delete_request()

    assert [i.video_id for i in library.items] == ["b"]
    assert r.did_action and pc.screen.confirm_video_id == "b"


# ---------------------------------------------------------------------
# Final whole-branch review: background deletion, clean power off,
# unready pick from the TUI
# ---------------------------------------------------------------------


def _finishes_without_suspending(coro):
    """Run `coro` one step: it must return without reaching a suspension
    point (a command that suspends can be run twice by a bridge retry)."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise AssertionError("the command suspended")


async def _deletions_done(pc):
    """Wait for the background part of every deletion (unlinks, YouTube)."""
    await asyncio.gather(*list(pc._delete_tasks))


def _slow_youtube():
    """A YouTube client whose delete blocks until `release` is set."""
    import threading
    release = threading.Event()
    started = threading.Event()
    yt = MagicMock()

    def _delete(_item_id):
        started.set()
        release.wait(5)

    yt.delete_playlist_item.side_effect = _delete
    return yt, started, release


@pytest.mark.asyncio
async def test_confirmed_delete_of_paused_video_acks_before_youtube_and_casts_nothing():
    """A slow YouTube delete used to hold the confirm's toggle past the
    bridge's 5 s timeout; its retry then resumed the highlighted successor."""
    yt, started, release = _slow_youtube()
    pc, receiver, q, cc, library = await _paused_on(
        ["a", "b", "c"], "b", 30.0, youtube_client=yt,
    )
    library.items[1].playlist_item_id = "PLI_b"
    await _select_command(pc, "delete")
    await pc.toggle()  # armed on "b"
    drain(q)

    r = _finishes_without_suspending(pc.toggle())  # confirm

    assert r.did_action
    assert [i.video_id for i in library.items] == ["a", "c"]
    assert pc.state == "idle" and pc.screen_state == "stopped"
    await asyncio.to_thread(started.wait, 2)  # YouTube delete under way
    msgs = drain(q)
    assert not loads(msgs)
    assert screens(msgs)[-1]["view"]["count"] == 2
    release.set()
    await _deletions_done(pc)
    yt.delete_playlist_item.assert_called_once_with("PLI_b")
    assert pc.state == "idle" and not loads(drain(q))


@pytest.mark.asyncio
async def test_delete_current_acks_before_youtube_and_still_deletes_remotely(tmp_path):
    yt, started, release = _slow_youtube()
    library = _make_library(["a", "b"])
    library.items[0].playlist_item_id = "PLI_a"
    library.items[0].filename = "a_pal_crop.mp4"
    cache_file = tmp_path / "a_pal_crop.mp4"
    cache_file.write_text("dummy")
    pc, receiver, q, cc = _make_player(library, youtube_client=yt)
    cancelled = MagicMock()
    pc.on_delete = cancelled
    await pc.play("a")
    drain(q)

    r = _finishes_without_suspending(pc.delete_request())

    assert r.did_action
    cancelled.assert_called_once_with("a")  # synchronous
    assert [i.video_id for i in library.items] == ["b"]
    assert library.cursor_video_id == "b"
    await asyncio.to_thread(started.wait, 2)
    release.set()
    await _deletions_done(pc)
    yt.delete_playlist_item.assert_called_once_with("PLI_a")
    assert not cache_file.exists()
    assert not pc._delete_tasks


@pytest.mark.asyncio
async def test_background_delete_failure_is_logged(caplog):
    library = _make_library(["a", "b"])
    library.items[0].playlist_item_id = "PLI_a"
    yt = MagicMock()
    yt.delete_playlist_item.side_effect = RuntimeError("boom")
    pc, receiver, q, cc = _make_player(library, youtube_client=yt)
    await pc.play("a")

    await pc.delete_current()
    await _deletions_done(pc)

    assert [i.video_id for i in library.items] == ["b"]
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_immediate_delete_reports_the_deleted_video_id():
    library = _make_library(["a", "b"])
    pc, receiver, q, cc = _make_player(library)
    await pc.play("a")

    r = await pc.delete_request()

    assert r.did_action and r.deleted_video_id == "a"
    r = await pc.delete_request()  # stopped screen: only asks
    assert r.deleted_video_id is None


@pytest.mark.asyncio
async def test_power_off_blanks_the_page_before_quitting():
    """quit_app swallows timeouts: if DashCast stays open the page must not
    keep an awake screen the daemon no longer knows about."""
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0)
    await _select_command(pc, "power")
    drain(q)
    sent_at_quit = []
    cc.quit_app.side_effect = lambda: sent_at_quit.extend(drain(q))

    await pc.toggle()
    await pc._quit_task

    assert sent_at_quit[-2:] == [{"type": "blank"}, {"type": "screen", "view": None}]


@pytest.mark.asyncio
async def test_idle_close_blanks_the_page_before_quitting():
    clock = Clock()
    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0, clock=clock)
    drain(q)
    sent_at_quit = []
    cc.quit_app.side_effect = lambda: sent_at_quit.extend(drain(q))
    clock.t += 1800

    await pc._close_if_idle(1800)

    cc.quit_app.assert_called_once()
    assert sent_at_quit[-2:] == [{"type": "blank"}, {"type": "screen", "view": None}]


@pytest.mark.asyncio
async def test_tui_play_of_unready_row_flashes_not_ready_on_the_screen():
    library = _make_mixed_library([("a", True), ("b", False)])
    pc, receiver, q, cc = _make_player(library, clock=Clock())
    await pc.play("a")
    await pc.toggle()  # paused, screen shown
    drain(q)

    r = await pc.play("b")

    assert r.did_action is False and r.reason == "no_playable_item"
    assert pc.state == "paused"
    assert screens(drain(q))[-1]["view"]["flash"] == "Non ancora pronto"


@pytest.mark.asyncio
async def test_power_off_during_a_pending_launch_never_opens_the_page():
    """Spegni while the page is still being opened (here: before discovery):
    the launch must not go on and open DashCast after the power-off."""
    library = _make_library(["a", "b"])
    library.cursor_video_id = "a"
    pc, receiver, pages, cc = _player_without_page(library)
    discovered = asyncio.Event()
    cc.wait_for_connection = AsyncMock(side_effect=discovered.wait)
    r = await pc.next()
    assert r.reason == "launching"
    await _select_command(pc, "power")

    await pc.toggle()
    discovered.set()
    await pc._quit_task
    await _launched(pc)

    cc.quit_app.assert_called_once()
    cc.launch_receiver.assert_not_called()
    assert pc.state == "idle" and pc.screen is None


@pytest.mark.asyncio
async def test_key_during_power_off_waits_for_the_quit_then_relaunches():
    """A key while quit_app is still running: the page that is closing must
    not count as open, and the new launch comes after the quit."""
    import threading

    pc, receiver, q, cc, library = await _paused_on(["a", "b"], "b", 30.0)
    calls = []
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def quit_app():
        release.wait(5)
        calls.append("quit")

    def launch_receiver(url):
        calls.append("launch")
        loop.call_soon_threadsafe(receiver.subscribe)

    cc.quit_app = MagicMock(side_effect=quit_app)
    cc.launch_receiver = MagicMock(side_effect=launch_receiver)
    await _select_command(pc, "power")
    await pc.toggle()

    r = await pc.next()

    assert r.did_action and r.reason == "launching"
    release.set()
    await pc._quit_task
    await _launched(pc)
    assert calls == ["quit", "launch"]
    assert pc.screen is not None and pc.state == "idle"


@pytest.mark.asyncio
async def test_finish_deletes_waits_for_the_youtube_delete():
    """Shutdown right after an acknowledged Elimina: the YouTube delete must
    still run, or the next sync brings the video back."""
    import time

    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLI-A"
    yt = MagicMock()
    yt.delete_playlist_item = MagicMock(side_effect=lambda _id: time.sleep(0.1))
    pc, *_ = _make_player(library, youtube_client=yt)

    await pc.delete_current()
    await pc.finish_deletes(timeout_s=5)

    yt.delete_playlist_item.assert_called_once_with("PLI-A")
    assert not pc._delete_tasks
