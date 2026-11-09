import pytest

from crt import config
from crt.library_store import QueueItem
from crt.screen import (
    COLUMNS,
    CONFIRM_DELETE,
    EMPTY_PLAYLIST,
    Screen,
    preview_window,
    row_for,
)


@pytest.fixture(autouse=True)
def _temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))


def items(*specs):  # specs: (video_id, status)
    return [QueueItem(url=f"u/{v}", video_id=v, title=f"T{v}", status=s) for v, s in specs]


def view(screen, lib, cursor=None, preview_url=lambda i: None, **kw):
    kw.setdefault("loop", False)
    kw.setdefault("sync_at", None)
    kw.setdefault("sync_error", False)
    return screen.view(lib, cursor, preview_url=preview_url, **kw)


def test_rows_symbols_text_right_and_kind():
    lib = items(("a", "ready"), ("b", "done"), ("c", "queued"), ("d", "downloading"),
                ("e", "encoding"), ("f", "error"), ("g", "paused"))
    lib[3].progress = 43.7
    lib[4].progress = 10
    r = {i.video_id: row_for(i, "g") for i in lib}
    assert r["g"]["symbol"] == "▶" and r["g"]["kind"] == "ready"
    assert r["a"]["symbol"] == "○" and r["a"]["kind"] == "ready"
    assert r["a"]["text"] == "Ta"
    assert r["b"]["symbol"] == "✓" and r["b"]["kind"] == "done"
    assert r["c"]["symbol"] == "…" and r["c"]["text"] == "In coda · Tc"
    assert r["c"]["kind"] == "preparing" and r["c"]["right"] == ""
    assert r["d"]["text"] == "Download · Td" and r["d"]["right"] == "43%"
    assert r["e"]["text"] == "Codifica · Te" and r["e"]["right"] == "10%"
    assert r["f"]["symbol"] == "!" and r["f"]["text"] == "Errore nel video · Tf"
    assert r["f"]["kind"] == "error"
    assert row_for(lib[0], "a")["symbol"] == "▶"
    assert row_for(QueueItem(url="u", video_id="x", status="casting"), None)["symbol"] == "○"


def _right(status, *, duration=0.0, position=0.0, progress=0.0, loaded=None):
    item = QueueItem(url="u", video_id="x", status=status, duration=duration,
                     playback_position=position, progress=progress)
    return row_for(item, loaded)["right"]


def test_right_shows_the_duration_of_a_video_not_started():
    assert _right("ready", duration=552) == "09:12"
    assert _right("ready", duration=3930) == "1:05:30"


def test_right_shows_the_duration_of_watched_queued_and_error_videos_once_known():
    assert _right("done", duration=552) == "09:12"
    assert _right("queued", duration=552) == "09:12"
    assert _right("error", duration=552) == "09:12"


def test_right_is_empty_while_the_duration_is_unknown():
    assert _right("queued") == ""
    assert _right("ready") == ""


def test_right_shows_the_time_left_of_a_started_video():
    assert _right("ready", duration=2400, position=420) == "-33:00"
    assert _right("ready", duration=4000, position=100) == "-1:05:00"


def test_right_shows_the_time_left_on_the_paused_row():
    assert _right("paused", duration=552, position=52, loaded="x") == "-08:20"


def test_right_shows_zero_left_when_the_position_passes_the_duration():
    assert _right("ready", duration=552, position=600) == "-00:00"


def test_right_is_empty_for_a_started_video_of_unknown_duration():
    """No remaining time to show; the old position display is gone."""
    assert _right("ready", position=247) == ""


def test_right_percentage_wins_while_downloading_or_encoding():
    assert _right("downloading", duration=552, position=60, progress=43.7) == "43%"
    assert _right("encoding", duration=552, progress=10) == "10%"


def test_subpage_follows_selection():
    lib = items(*[(str(i), "ready") for i in range(1, 20)])
    v = view(Screen("12"), lib, "1")
    assert v["page"] == 2 and v["pages"] == 2 and v["count"] == 19
    assert [r["num"] for r in v["rows"]] == list(range(11, 20))
    assert [r["selected"] for r in v["rows"]].index(True) == 1


def test_move_wraps_and_resets_command():
    lib = items(("a", "ready"), ("b", "ready"))
    s = Screen("b")
    s.command_index = 2
    s.move(lib, +1)
    assert s.selected_video_id == "a" and s.command_index == 0
    s.move(lib, -1)
    assert s.selected_video_id == "b"


def test_move_command_wraps_over_two_rows():
    lib = items(("a", "paused"))
    s = Screen("a", "a")
    assert s.commands(lib) == ["resume", "delete", "stop", "calibrate", "loop", "sync", "power"]
    s.command_index = 6
    s.confirm_video_id = "a"
    s.flash = "x"
    s.move_command(lib, +1)
    assert s.selected_command(lib) == "resume"
    assert s.confirm_video_id is None and s.flash is None
    s.move_command(lib, -1)
    assert s.selected_command(lib) == "power"


def test_commands_without_stop_when_stopped():
    lib = items(("a", "ready"))
    assert Screen("a").commands(lib) == ["resume", "delete", "calibrate", "loop", "sync", "power"]


def test_resume_label():
    lib = items(("a", "ready"), ("b", "ready"))
    assert Screen("a", "a").resume_label(lib) == "Riprendi"
    assert Screen("b", "a").resume_label(lib) == "Riproduci"
    lib[1].playback_position = 5
    assert Screen("b", "a").resume_label(lib) == "Riprendi"


def test_confirm_view():
    lib = items(("a", "ready"), ("b", "ready"))
    s = Screen("a")
    s.confirm_video_id = "b"
    v = view(s, lib, "a")
    assert [r["confirm"] for r in v["rows"]] == [False, True]
    assert v["flash"] == CONFIRM_DELETE and v["flash_kind"] == "confirm"


def test_confirm_dropped_when_video_leaves():
    lib = items(("a", "ready"))
    s = Screen("a")
    s.confirm_video_id = "gone"
    s.flash = "Fine della coda"
    s.flash_kind = "error"
    v = view(s, lib, "a")
    assert s.confirm_video_id is None
    assert v["flash"] == "Fine della coda" and v["flash_kind"] == "error"


def test_selection_falls_back_to_cursor_then_first():
    lib = items(("a", "ready"), ("b", "ready"))
    s = Screen("gone")
    s.fix_selection(lib, "b")
    assert s.selected_video_id == "b"
    s = Screen("gone")
    s.fix_selection(lib, "zzz")
    assert s.selected_video_id == "a"
    s = Screen("gone")
    s.fix_selection([], None)
    assert s.selected_video_id is None


def test_empty_library_view():
    v = view(Screen(None), [])
    assert v["rows"] == [] and v["status"] == EMPTY_PLAYLIST
    assert [c["key"] for c in v["commands"]] == ["calibrate", "loop", "sync", "power"]
    assert v["background"] == "black" and v["preview"] is None
    assert v["help"] == "OK = Calibra" and v["pages"] == 1 and v["count"] == 0


def test_background_kinds():
    lib = items(("a", "ready"), ("b", "queued"), ("c", "ready"), ("d", "ready"))
    lib[2].playback_position = 95
    urls = {"c": "/media/c_prev.mp4"}
    pu = lambda i: urls.get(i.video_id)  # noqa: E731
    assert view(Screen("a", "a"), lib, "a", pu)["background"] == "frame"
    assert view(Screen("b"), lib, "a", pu)["background"] == "snow"
    v = view(Screen("c"), lib, "a", pu)
    assert v["background"] == "preview"
    assert v["preview"] == {"url": "/media/c_prev.mp4", "start": 75, "end": 95}
    v = view(Screen("d"), lib, "a", pu)
    assert v["background"] == "black" and v["preview"] is None


def test_preview_window():
    assert preview_window(0) == (0.0, 20.0)
    assert preview_window(12) == (0.0, 20.0)
    assert preview_window(95) == (75, 95)


def test_paused_position_and_state():
    lib = items(("a", "paused"), ("b", "ready"))
    lib[0].playback_position = 30
    lib[0].duration = 552
    v = view(Screen("a", "a"), lib, "a")
    assert v["state"] == "paused" and v["paused_position"] == 30
    assert v["paused_duration"] == 552
    assert [c["key"] for c in v["commands"]] == [
        "resume", "delete", "stop", "calibrate", "loop", "sync", "power"]
    assert v["commands"][0]["label"] == "Riprendi" and v["commands"][0]["selected"]
    assert v["command_first"] == 0
    assert v["help"] == "OK = Riprendi"
    v = view(Screen("a"), lib, "a")
    assert v["state"] == "stopped" and v["paused_position"] is None
    assert v["paused_duration"] is None


def test_command_index_clamped_when_commands_shrink():
    lib = items(("a", "ready"))
    s = Screen("a", "a")
    s.command_index = 6
    s.loaded_video_id = None
    assert s.selected_command(lib) == "power"
    s.command_index = 99
    assert s.selected_command(lib) == "power"
    assert s.selected_command([]) == "power"
    s.command_index = 0
    assert s.selected_command([]) == "calibrate"


def test_time_left_agrees_with_row_4_on_a_fractional_position():
    """Row 4 floors position and duration apart (00:52 / 09:12): the row
    must say 8:20, not the 8:19 of flooring their difference."""
    assert _right("paused", duration=552, position=52.4, loaded="x") == "-08:20"


def test_time_left_above_10_hours_fits_8_columns():
    """-11:59:00 is 9 characters: above 10 h left the seconds go."""
    assert _right("ready", duration=43200, position=60) == "-11h59"
    assert len(_right("ready", duration=36000, position=1)) <= 8


def test_paused_duration_is_none_while_unknown():
    """The page then falls back to the <video>'s own duration."""
    lib = items(("a", "paused"))
    lib[0].playback_position = 30
    assert view(Screen("a", "a"), lib, "a")["paused_duration"] is None


def _visible(v):
    return [c["label"] for c in v["commands"][v["command_first"]:]]


def _drawn(v):
    """The command row as the page draws it (commandLine in index.html): a
    "…" before the first command when the row is scrolled, and past column
    40 the row is cut with "…" on its last column."""
    row = ("…" if v["command_first"] > 0 else "") + " ".join(_visible(v))
    if len(row) > COLUMNS:
        row = row[:COLUMNS - 1] + "…"
    return row


def _selected_is_whole(v):
    first = v["command_first"]
    sel = next(i for i, c in enumerate(v["commands"]) if c["selected"])
    if sel < first:
        return False
    labels = [c["label"] for c in v["commands"]]
    start = (1 if first > 0 else 0) + sum(len(label) + 1 for label in labels[first:sel])
    return _drawn(v)[start:start + len(labels[sel])] == labels[sel]


def test_command_row_scrolls_only_when_the_selection_runs_past_the_edge():
    """One row of 46 columns: Spegni is past column 40, so reaching it
    scrolls by whole commands; going back left scrolls only at Riprendi."""
    lib = items(("a", "paused"), ("b", "ready"))
    s = Screen("a", "a")
    firsts = []
    for _ in range(6):
        s.move_command(lib, +1)
        firsts.append(view(s, lib, "a")["command_first"])
    assert firsts == [0, 0, 0, 0, 0, 1]
    v = view(s, lib, "a")
    assert _drawn(v) == "…Elimina Stop Calibra Loop Sync Spegni"
    firsts = []
    for _ in range(6):
        s.move_command(lib, -1)
        firsts.append(view(s, lib, "a")["command_first"])
    assert firsts == [1, 1, 1, 1, 1, 0]


def test_command_row_wraps_back_to_the_start():
    lib = items(("a", "paused"))
    s = Screen("a", "a")
    s.move_command(lib, -1)  # from Riprendi to Spegni
    assert view(s, lib, "a")["command_first"] == 1
    s.move_command(lib, +1)  # and back to Riprendi
    assert view(s, lib, "a")["command_first"] == 0


def test_row_change_scrolls_the_command_row_back():
    lib = items(("a", "paused"), ("b", "ready"))
    s = Screen("a", "a")
    s.move_command(lib, -1)
    assert view(s, lib, "a")["command_first"] == 1
    s.move(lib, +1)
    assert view(s, lib, "a")["command_first"] == 0


@pytest.mark.parametrize("loaded, position", [("a", 30), (None, 0), (None, 30)])
def test_selected_command_is_always_whole_on_screen(loaded, position):
    """Paused or stopped, Riprendi or Riproduci, both directions."""
    lib = items(("a", "paused" if loaded else "ready"), ("b", "ready"))
    lib[0].playback_position = position
    s = Screen("a", loaded)
    n = len(s.commands(lib))
    for delta in [+1] * (n + 2) + [-1] * (n + 2):
        s.move_command(lib, delta)
        v = view(s, lib, "a")
        assert len(_drawn(v)) <= COLUMNS
        assert _selected_is_whole(v), _drawn(v)


def test_command_row_marks_the_cut_sides_with_an_ellipsis():
    lib = items(("a", "paused"), ("b", "ready"))
    assert _drawn(view(Screen("a", "a"), lib, "a")) == "Riprendi Elimina Stop Calibra Loop Sync…"
    assert _drawn(view(Screen("b"), lib, "a")) == "Riproduci Elimina Calibra Loop Sync Spe…"
    s = Screen("b")
    s.move_command(lib, -1)
    assert _drawn(view(s, lib, "a")) == "…Elimina Calibra Loop Sync Spegni"


def test_right_ellipsis_never_covers_the_selected_command():
    """Paused on a with b highlighted (Riproduci): Sync ends on column 40,
    where the "…" goes, so selecting it scrolls."""
    lib = items(("a", "paused"), ("b", "ready"))
    s = Screen("b", "a")
    for _ in range(5):
        s.move_command(lib, +1)
    v = view(s, lib, "a")
    assert v["commands"][5]["label"] == "Sync" and v["commands"][5]["selected"]
    assert _drawn(v) == "…Elimina Stop Calibra Loop Sync Spegni"


def test_empty_library_command_row_never_scrolls():
    s = Screen(None)
    for _ in range(4):
        s.move_command([], +1)
        assert view(s, [])["command_first"] == 0
