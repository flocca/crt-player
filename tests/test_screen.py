import pytest

from crt import config
from crt.library_store import QueueItem
from crt.screen import (
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
    lib[0].playback_position = 247
    lib[1].playback_position = 3725
    r = {i.video_id: row_for(i, "g") for i in lib}
    assert r["g"]["symbol"] == "▶" and r["g"]["kind"] == "ready"
    assert r["a"]["symbol"] == "○" and r["a"]["kind"] == "ready"
    assert r["a"]["right"] == "04:07" and r["a"]["text"] == "Ta"
    assert r["b"]["symbol"] == "✓" and r["b"]["kind"] == "done"
    assert r["b"]["right"] == "1:02:05"
    assert r["c"]["symbol"] == "…" and r["c"]["text"] == "In coda · Tc"
    assert r["c"]["kind"] == "preparing" and r["c"]["right"] == ""
    assert r["d"]["text"] == "Download · Td" and r["d"]["right"] == "43%"
    assert r["e"]["text"] == "Codifica · Te" and r["e"]["right"] == "10%"
    assert r["f"]["symbol"] == "!" and r["f"]["text"] == "Errore nel video · Tf"
    assert r["f"]["kind"] == "error"
    assert row_for(lib[0], "a")["symbol"] == "▶"
    assert row_for(QueueItem(url="u", video_id="x", status="casting"), None)["symbol"] == "○"


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
    assert v["commands"][0] == [] and v["background"] == "black" and v["preview"] is None
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
    v = view(Screen("a", "a"), lib, "a")
    assert v["state"] == "paused" and v["paused_position"] == 30
    assert [c["key"] for c in v["commands"][0]] == ["resume", "delete", "stop"]
    assert [c["key"] for c in v["commands"][1]] == ["calibrate", "loop", "sync", "power"]
    assert v["commands"][0][0]["label"] == "Riprendi" and v["commands"][0][0]["selected"]
    assert v["help"] == "OK = Riprendi"
    v = view(Screen("a"), lib, "a")
    assert v["state"] == "stopped" and v["paused_position"] is None


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
