import json
import os
from pathlib import Path

import pytest

from crt.library_store import LibraryStore


def test_save_state_writes_v2(tmp_path: Path):
    ls = LibraryStore()
    ls.cursor_video_id = "abc"
    ls.loop_mode = True
    state_file = tmp_path / "state.json"
    ls.save_state(str(state_file))
    data = json.loads(state_file.read_text())
    assert data["version"] == 3
    assert data["cursor_video_id"] == "abc"
    assert data["loop_mode"] is True


def test_load_state_v2_restores_cursor_and_loop(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 2,
        "cursor_video_id": "xyz",
        "loop_mode": True,
        "items": [],
        "history": [],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.cursor_video_id == "xyz"
    assert ls.loop_mode is True


def test_load_state_v1_backs_up_and_starts_empty(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 1,
        "playback_position": 0.0,
        "items": [{"url": "u", "id": "i", "title": "t", "status": "queued"}],
        "history": [],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.items == []
    assert ls.cursor_video_id is None
    backup = tmp_path / "state.json.v1.bak"
    assert backup.exists()
    backup_data = json.loads(backup.read_text())
    assert backup_data["version"] == 1


def test_load_state_no_version_treated_as_v1(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "items": [{"url": "u", "id": "i", "title": "t", "status": "queued"}],
    }))
    ls = LibraryStore()
    ls.load_state(str(state_file))
    assert ls.items == []
    assert (tmp_path / "state.json.v1.bak").exists()


def test_load_state_missing_file_returns_empty(tmp_path: Path):
    ls = LibraryStore()
    state_file = tmp_path / "nonexistent.json"
    ls.load_state(str(state_file))
    assert ls.items == []
    assert ls.cursor_video_id is None


def test_load_state_v2_treated_as_v3_compatible(tmp_path):
    """v2 state files should load without backup (back-compat read), items get playlist_item_id=None."""
    path = tmp_path / "state.json"
    payload = {
        "version": 2,
        "cursor_video_id": "A",
        "loop_mode": False,
        "items": [
            {"url": "u/A", "video_id": "A", "title": "A", "status": "ready", "filename": "A_pal_crop.mp4"},
        ],
        "history": [],
    }
    path.write_text(json.dumps(payload))

    ls = LibraryStore()
    ls.load_state(str(path))

    assert len(ls.items) == 1
    assert ls.items[0].playlist_item_id is None
    # No backup created on the v2→v3 silent upgrade
    assert not (tmp_path / "state.json.v1.bak").exists()
    assert not (tmp_path / "state.json.v2.bak").exists()


def test_load_state_unknown_version_backs_up_and_resets(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 99, "items": []}))

    ls = LibraryStore()
    ls.load_state(str(path))

    assert len(ls.items) == 0
    assert (tmp_path / "state.json.v1.bak").exists()
