import asyncio
import os
import time
from unittest.mock import MagicMock

import pytest

from crt import daemon


@pytest.mark.asyncio
async def test_periodic_save_loop_saves_repeatedly(tmp_path):
    """Issue #4: state must be persisted on an interval, not only on graceful
    shutdown, so a crash/OOM/reboot loses at most one interval of changes."""
    library = MagicMock()
    chromecast = MagicMock()
    chromecast.live_current_time = 12.0
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.save_state.call_count >= 2
    library.save_state.assert_called_with(state_file, playback_position=12.0)


@pytest.mark.asyncio
async def test_periodic_save_loop_survives_save_error(tmp_path):
    """A failing save must not kill the loop — the next tick retries."""
    library = MagicMock()
    library.save_state.side_effect = OSError("disk full")
    chromecast = MagicMock()
    chromecast.live_current_time = 0.0
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert library.save_state.call_count >= 2  # kept ticking despite errors


@pytest.mark.asyncio
async def test_periodic_save_loop_handles_none_current_time(tmp_path):
    """current_time may be None before the first media-status event."""
    library = MagicMock()
    chromecast = MagicMock()
    chromecast.live_current_time = None
    state_file = str(tmp_path / "state.json")

    task = asyncio.create_task(
        daemon.periodic_save_loop(library, chromecast, state_file, interval_s=0.01)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # None coerced to 0.0 so save_state's "ignore zero" guard applies.
    library.save_state.assert_called_with(state_file, playback_position=0.0)


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
