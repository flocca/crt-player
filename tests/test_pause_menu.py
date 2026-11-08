from crt.library_store import QueueItem
from crt.pause_menu import COMMANDS, Entry, PauseMenu, status_symbol


def items(*specs):  # specs: (video_id, status)
    return [QueueItem(url=f"u/{v}", video_id=v, title=f"T{v}", status=s) for v, s in specs]


def test_menu_opens_on_current_video():
    m = PauseMenu("b")
    assert m.selected == Entry("video", "b")


def test_move_walks_videos_then_commands_and_wraps():
    m = PauseMenu("c")
    ids = ["a", "b", "c"]
    m.move(ids, +1)
    assert m.selected == Entry("command", "stop")
    m.move(ids, +5)
    assert m.selected == Entry("video", "a")
    m.move(ids, -1)
    assert m.selected == Entry("command", "sync")


def test_move_clears_confirm_and_notice():
    m = PauseMenu("a")
    m.confirming_delete = True
    m.notice = "x"
    m.move(["a"], +1)
    assert not m.confirming_delete and m.notice is None


def test_view_rows_window_is_seven_centered_on_selection():
    lib = items(*[(str(i), "ready") for i in range(19)])
    m = PauseMenu("10")
    v = m.view(lib, "10")
    assert [r["title"] for r in v["rows"]] == [f"T{i}" for i in range(7, 14)]
    assert [r["selected"] for r in v["rows"]].index(True) == 3


def test_view_window_clamps_at_the_start():
    lib = items(*[(str(i), "ready") for i in range(19)])
    v = PauseMenu("1").view(lib, "1")
    assert [r["title"] for r in v["rows"]] == [f"T{i}" for i in range(0, 7)]


def test_view_window_on_command_shows_last_rows():
    lib = items(*[(str(i), "ready") for i in range(19)])
    m = PauseMenu("18")
    m.move([i.video_id for i in lib], +1)
    v = m.view(lib, "18")
    assert v["rows"][-1]["title"] == "T18"
    assert len(v["rows"]) == 7
    assert [c["selected"] for c in v["commands"]] == [True, False, False, False, False]
    assert [c["label"] for c in v["commands"]] == [label for _, label in COMMANDS]


def test_view_title_is_cursor_title_and_rows_carry_symbols():
    lib = items(("a", "done"), ("b", "paused"), ("c", "ready"))
    v = PauseMenu("b").view(lib, "b")
    assert v["title"] == "Tb"
    assert [(r["symbol"], r["dim"]) for r in v["rows"]] == [("✓", True), ("▶", False), ("○", False)]


def test_view_title_empty_without_cursor():
    assert PauseMenu("a").view(items(("a", "ready")), None)["title"] == ""


def test_view_confirm_and_notice():
    m = PauseMenu("a")
    m.confirming_delete = True
    m.notice = "Loop attivo"
    v = m.view(items(("a", "paused")), "a")
    assert v["confirm"] == "Confermi? OK sì" and v["notice"] == "Loop attivo"


def test_view_without_confirm_has_none():
    v = PauseMenu("a").view(items(("a", "paused")), "a")
    assert v["confirm"] is None and v["notice"] is None


def test_selected_video_removed_from_library_falls_back_to_cursor():
    m = PauseMenu("a")
    m.move(["a", "b"], +1)  # on "b"
    v = m.view(items(("a", "paused")), "a")
    assert m.selected == Entry("video", "a")
    assert v["rows"][0]["selected"]


def test_selection_falls_back_to_first_entry_when_cursor_gone_too():
    m = PauseMenu("a")
    m.view(items(("z", "ready")), None)
    assert m.selected == Entry("video", "z")


def test_move_after_selected_video_removed_starts_from_fallback():
    m = PauseMenu("b")
    # "b" vanished; cursor is "a" → fallback to "a", then +1 → "c"
    m.move(["a", "c"], +1)
    assert m.selected == Entry("video", "c")


def test_empty_library_selects_first_command():
    m = PauseMenu("a")
    v = m.view([], None)
    assert m.selected == Entry("command", "stop")
    assert v["rows"] == [] and v["commands"][0]["selected"]


def test_status_symbols():
    a, b, c, d, e, f = items(("a", "ready"), ("b", "done"), ("c", "queued"), ("d", "encoding"), ("e", "error"), ("f", "ready"))
    d.progress = 43.7
    assert status_symbol(a, False) == "○"
    assert status_symbol(b, False) == "✓"
    assert status_symbol(c, False) == "…"
    assert status_symbol(d, False) == "… 43%"
    assert status_symbol(e, False) == "!"
    assert status_symbol(f, True) == "▶"
