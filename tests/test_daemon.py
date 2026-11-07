import asyncio
import os
import time
from unittest.mock import MagicMock

import pytest

from crt import daemon


async def _run_loop(library, position, state_file, wait_s):
    task = asyncio.create_task(
        daemon.periodic_save_loop(library, position, state_file, interval_s=0.01)
    )
    await asyncio.sleep(wait_s)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_periodic_save_loop_saves_repeatedly(tmp_path):
    """Issue #4: state must be persisted on an interval, not only on graceful
    shutdown, so a crash/OOM/reboot loses at most one interval of changes.
    The position comes from the player (the receiver page), read per tick."""
    library = MagicMock()
    positions = iter([12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
    state_file = str(tmp_path / "state.json")

    await _run_loop(library, lambda: next(positions, 20.0), state_file, 0.05)

    assert library.save_state.call_count >= 2
    first = library.save_state.call_args_list[0]
    second = library.save_state.call_args_list[1]
    assert first.kwargs["playback_position"] == 12.0
    assert second.kwargs["playback_position"] == 13.0


@pytest.mark.asyncio
async def test_periodic_save_loop_survives_save_error(tmp_path):
    """A failing save must not kill the loop — the next tick retries."""
    library = MagicMock()
    library.save_state.side_effect = OSError("disk full")
    state_file = str(tmp_path / "state.json")

    await _run_loop(library, lambda: 0.0, state_file, 0.05)

    assert library.save_state.call_count >= 2  # kept ticking despite errors


@pytest.mark.asyncio
async def test_periodic_save_loop_zero_position_leaves_stored_position(tmp_path):
    """position() is 0.0 while idle or calibrating: the stored resume point
    must survive (save_state ignores a zero position)."""
    from crt.library_store import LibraryStore, QueueItem

    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="A", status="ready", playback_position=88.0))
    library.cursor_video_id = "A"
    state_file = str(tmp_path / "state.json")

    await _run_loop(library, lambda: 0.0, state_file, 0.03)

    assert library.items[0].playback_position == 88.0
    assert os.path.exists(state_file)


def _old_file(path, hours=48):
    path.write_text("x")
    t = time.time() - hours * 3600
    os.utime(path, (t, t))
    return path


def test_cleanup_temp_files_keeps_files_of_library_items(monkeypatch, tmp_path):
    """Issue #16: the startup TTL deleted the cache of videos still in the
    playlist, so every restart re-downloaded and re-encoded everything."""
    from crt import config
    from crt.library_store import LibraryStore, QueueItem
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    encoded = config.cached_encoded_filename("A")
    _old_file(tmp_path / encoded)
    _old_file(tmp_path / "B.mp4")  # source of an item not encoded yet
    _old_file(tmp_path / config.cached_encoded_filename("C"))  # queued, file reusable
    library = LibraryStore()
    library.items += [
        QueueItem(url="u", video_id="A", status="ready", filename=encoded),
        QueueItem(url="u", video_id="B", status="queued", downloaded_path=str(tmp_path / "B.mp4")),
        QueueItem(url="u", video_id="C", status="queued"),
    ]

    daemon.cleanup_temp_files(library)

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [encoded, "B.mp4", config.cached_encoded_filename("C")]
    )


def test_cleanup_temp_files_removes_old_orphans(monkeypatch, tmp_path):
    from crt import config
    from crt.library_store import LibraryStore, QueueItem
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    _old_file(tmp_path / config.cached_encoded_filename("gone"))
    _old_file(tmp_path / "gone.mp4")
    (tmp_path / "fresh_orphan.mp4").write_text("x")  # younger than the TTL
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="A"))

    daemon.cleanup_temp_files(library)

    assert [p.name for p in tmp_path.iterdir()] == ["fresh_orphan.mp4"]
