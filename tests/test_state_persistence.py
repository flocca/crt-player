import json
import os

import pytest

from crt.library_store import QueueItem, LibraryStore


def test_queue_item_to_dict():
    item = QueueItem(url="https://youtube.com/watch?v=abc", id="test-id",
                     title="Test", status="playing", progress=50.0,
                     error=None, filename="abc_pal.mp4")
    d = item.to_dict()
    assert d["url"] == "https://youtube.com/watch?v=abc"
    assert d["id"] == "test-id"
    assert d["title"] == "Test"
    assert d["status"] == "playing"
    assert d["filename"] == "abc_pal.mp4"


def test_queue_item_from_dict():
    d = {"url": "https://youtube.com/watch?v=abc", "id": "test-id",
         "title": "Test", "status": "queued", "filename": "abc_pal.mp4"}
    item = QueueItem.from_dict(d)
    assert item.url == "https://youtube.com/watch?v=abc"
    assert item.id == "test-id"
    assert item.title == "Test"
    assert item.status == "queued"
    assert item.filename == "abc_pal.mp4"
    assert item.progress == 0.0
    assert item.error is None


def test_queue_item_roundtrip():
    original = QueueItem(url="https://youtube.com/watch?v=x", title="Round",
                         status="queued", filename="x_pal.mp4")
    restored = QueueItem.from_dict(original.to_dict())
    assert restored.url == original.url
    assert restored.id == original.id
    assert restored.title == original.title
    assert restored.status == original.status
    assert restored.filename == original.filename


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")

    qm = LibraryStore()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.title = "Video 1"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.title = "Video 2"
    item2.status = "done"
    qm.push_to_history(item2)

    qm.save_state(path)

    qm2 = LibraryStore()
    pos = qm2.load_state(path)

    assert pos == 0.0
    assert len(qm2.items) == 2
    assert qm2.items[0].title == "Video 1"
    assert qm2.items[0].status == "queued"
    assert len(qm2.history) == 1
    assert qm2.history[0].title == "Video 2"


def test_load_fixup_downloading_to_queued(tmp_path):
    path = str(tmp_path / "state.json")
    data = {
        "version": 2,
        "cursor_video_id": None,
        "loop_mode": False,
        "items": [
            {"url": "http://a", "id": "1", "title": "A",
             "status": "downloading", "progress": 55.0, "filename": "a.mp4"},
            {"url": "http://b", "id": "2", "title": "B",
             "status": "encoding", "progress": 30.0, "filename": "b_pal.mp4"},
            {"url": "http://c", "id": "3", "title": "C",
             "status": "casting", "progress": 0.0, "filename": "c_pal.mp4"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = LibraryStore()
    qm.load_state(path)

    for item in qm.items:
        assert item.status == "queued"
        assert item.progress == 0.0
        assert item.filename is None


def test_load_fixup_playing_with_file(tmp_path, monkeypatch):
    # Create encoded file in TEMP_DIR
    temp_dir = str(tmp_path / "media")
    os.makedirs(temp_dir)
    open(os.path.join(temp_dir, "vid_pal.mp4"), "w").close()
    monkeypatch.setattr("crt.config.TEMP_DIR", temp_dir)

    path = str(tmp_path / "state.json")
    data = {
        "version": 2,
        "cursor_video_id": None,
        "loop_mode": False,
        "items": [
            {"url": "http://v", "id": "1", "title": "Vid",
             "status": "playing", "progress": 0.0, "filename": "vid_pal.mp4"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = LibraryStore()
    pos = qm.load_state(path)

    assert pos == 0.0
    assert qm.items[0].status == "ready"
    assert qm.items[0].filename == "vid_pal.mp4"


def test_load_fixup_playing_without_file(tmp_path, monkeypatch):
    temp_dir = str(tmp_path / "media")
    os.makedirs(temp_dir)
    monkeypatch.setattr("crt.config.TEMP_DIR", temp_dir)

    path = str(tmp_path / "state.json")
    data = {
        "version": 2,
        "cursor_video_id": None,
        "loop_mode": False,
        "items": [
            {"url": "http://v", "id": "1", "title": "Vid",
             "status": "playing", "progress": 0.0, "filename": "vid_pal.mp4"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = LibraryStore()
    pos = qm.load_state(path)

    assert pos == 0.0
    assert qm.items[0].status == "queued"
    assert qm.items[0].filename is None


def test_load_missing_file():
    qm = LibraryStore()
    pos = qm.load_state("/nonexistent/path/state.json")
    assert pos == 0.0
    assert qm.items == []
    assert qm.history == []


def test_load_corrupt_file(tmp_path):
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        f.write("not json{{{")

    qm = LibraryStore()
    pos = qm.load_state(path)
    assert pos == 0.0
    assert qm.items == []


def test_save_creates_directories(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "state.json")
    qm = LibraryStore()
    qm.add("http://x")
    qm.save_state(path)
    assert os.path.isfile(path)
    with open(path) as f:
        data = json.load(f)
    assert len(data["items"]) == 1


def test_next_pending_returns_ready_items():
    qm = LibraryStore()
    item = qm.add("http://x")
    item.status = "ready"
    item.filename = "x_pal.mp4"
    assert qm.next_pending() is item


def test_save_state_writes_version_3(tmp_path):
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    ls.add("https://youtube.com/watch?v=1")
    ls.save_state(path)

    with open(path) as f:
        data = json.load(f)
    assert data["version"] == 3


def test_queue_item_to_dict_includes_playlist_item_id():
    item = QueueItem(url="u/A", video_id="A", playlist_item_id="PLITEM_42")
    d = item.to_dict()
    assert d["playlist_item_id"] == "PLITEM_42"


def test_queue_item_from_dict_reads_playlist_item_id():
    d = {"url": "u/A", "playlist_item_id": "PLITEM_42"}
    item = QueueItem.from_dict(d)
    assert item.playlist_item_id == "PLITEM_42"


def test_queue_item_from_dict_missing_playlist_item_id_defaults_none():
    d = {"url": "u/A"}
    item = QueueItem.from_dict(d)
    assert item.playlist_item_id is None


def test_save_state_applies_playback_position_to_cursor_item(tmp_path):
    """Issue #7b: the live playback position passed by the daemon must be
    written onto the cursor item before serializing, so a restart resumes from
    the pause point instead of 0."""
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    a = ls.add("u/A")
    a.video_id = "A"
    b = ls.add("u/B")
    b.video_id = "B"
    ls.cursor_video_id = "B"

    ls.save_state(path, playback_position=1232.5)

    with open(path) as f:
        data = json.load(f)
    items = {i["video_id"]: i for i in data["items"]}
    assert items["B"]["playback_position"] == 1232.5
    assert items["A"]["playback_position"] == 0.0


def test_save_state_ignores_zero_playback_position(tmp_path):
    """A zero/negative position must not clobber an item's stored position."""
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    a = ls.add("u/A")
    a.video_id = "A"
    a.playback_position = 99.0
    ls.cursor_video_id = "A"

    ls.save_state(path, playback_position=0.0)

    with open(path) as f:
        data = json.load(f)
    assert data["items"][0]["playback_position"] == 99.0


def test_save_state_no_cursor_does_not_raise(tmp_path):
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    ls.add("u/A")
    ls.cursor_video_id = None

    ls.save_state(path, playback_position=50.0)  # must not raise

    with open(path) as f:
        data = json.load(f)
    assert data["items"][0]["playback_position"] == 0.0


def test_playback_position_survives_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    a = ls.add("u/A")
    a.video_id = "A"
    a.status = "done"  # so load keeps it as-is rather than resetting
    ls.cursor_video_id = "A"
    ls.save_state(path, playback_position=420.0)

    ls2 = LibraryStore()
    ls2.load_state(path)
    assert ls2.items[0].playback_position == 420.0


def test_done_and_error_items_preserved(tmp_path):
    path = str(tmp_path / "state.json")
    data = {
        "version": 2,
        "cursor_video_id": None,
        "loop_mode": False,
        "items": [
            {"url": "http://d", "id": "1", "title": "D", "status": "done"},
            {"url": "http://e", "id": "2", "title": "E", "status": "error",
             "error": "something broke"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = LibraryStore()
    qm.load_state(path)
    assert qm.items[0].status == "done"
    assert qm.items[1].status == "error"
    assert qm.items[1].error == "something broke"
