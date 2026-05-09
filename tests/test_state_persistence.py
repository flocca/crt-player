import json
import os

import pytest

from queue_manager import QueueItem, QueueManager


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

    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.title = "Video 1"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.title = "Video 2"
    item2.status = "done"
    qm.push_to_history(item2)

    qm.save_state(path, playback_position=42.5)

    qm2 = QueueManager()
    pos = qm2.load_state(path)

    assert pos == 42.5
    assert len(qm2.items) == 2
    assert qm2.items[0].title == "Video 1"
    assert qm2.items[0].status == "queued"
    assert len(qm2.history) == 1
    assert qm2.history[0].title == "Video 2"


def test_load_fixup_downloading_to_queued(tmp_path):
    path = str(tmp_path / "state.json")
    data = {
        "version": 1,
        "playback_position": 0.0,
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

    qm = QueueManager()
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
        "version": 1,
        "playback_position": 120.0,
        "items": [
            {"url": "http://v", "id": "1", "title": "Vid",
             "status": "playing", "progress": 0.0, "filename": "vid_pal.mp4"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = QueueManager()
    pos = qm.load_state(path)

    assert pos == 120.0
    assert qm.items[0].status == "ready"
    assert qm.items[0].filename == "vid_pal.mp4"


def test_load_fixup_playing_without_file(tmp_path, monkeypatch):
    temp_dir = str(tmp_path / "media")
    os.makedirs(temp_dir)
    monkeypatch.setattr("crt.config.TEMP_DIR", temp_dir)

    path = str(tmp_path / "state.json")
    data = {
        "version": 1,
        "playback_position": 120.0,
        "items": [
            {"url": "http://v", "id": "1", "title": "Vid",
             "status": "playing", "progress": 0.0, "filename": "vid_pal.mp4"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = QueueManager()
    pos = qm.load_state(path)

    assert pos == 120.0
    assert qm.items[0].status == "queued"
    assert qm.items[0].filename is None


def test_load_missing_file():
    qm = QueueManager()
    pos = qm.load_state("/nonexistent/path/state.json")
    assert pos == 0.0
    assert qm.items == []
    assert qm.history == []


def test_load_corrupt_file(tmp_path):
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        f.write("not json{{{")

    qm = QueueManager()
    pos = qm.load_state(path)
    assert pos == 0.0
    assert qm.items == []


def test_save_creates_directories(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "state.json")
    qm = QueueManager()
    qm.add("http://x")
    qm.save_state(path)
    assert os.path.isfile(path)
    with open(path) as f:
        data = json.load(f)
    assert len(data["items"]) == 1


def test_next_pending_returns_ready_items():
    qm = QueueManager()
    item = qm.add("http://x")
    item.status = "ready"
    item.filename = "x_pal.mp4"
    assert qm.next_pending() is item


def test_done_and_error_items_preserved(tmp_path):
    path = str(tmp_path / "state.json")
    data = {
        "version": 1,
        "playback_position": 0.0,
        "items": [
            {"url": "http://d", "id": "1", "title": "D", "status": "done"},
            {"url": "http://e", "id": "2", "title": "E", "status": "error",
             "error": "something broke"},
        ],
        "history": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)

    qm = QueueManager()
    qm.load_state(path)
    assert qm.items[0].status == "done"
    assert qm.items[1].status == "error"
    assert qm.items[1].error == "something broke"
