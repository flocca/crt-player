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
    assert data["version"] == 2
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
