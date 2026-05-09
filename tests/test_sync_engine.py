import asyncio
from unittest.mock import MagicMock

import pytest

from crt.library_store import LibraryStore, QueueItem
from crt.sync_engine import SyncEngine
from crt.youtube_client import PlaylistEntry, YouTubeAuthError


def _entry(video_id, title="T", position=0):
    return PlaylistEntry(video_id=video_id, title=title, position=position)


def test_apply_diff_adds_new_items():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [
        _entry("vid1", "Title 1", 0),
        _entry("vid2", "Title 2", 1),
    ]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["vid1", "vid2"]
    assert library.items[0].status == "queued"
    assert library.items[0].title == "Title 1"


def test_apply_diff_removes_items_not_in_playlist():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old", title="old"))
    library.items.append(QueueItem(url="u", video_id="vid1", title="kept"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [_entry("vid1", "kept", 0)]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["vid1"]


def test_apply_diff_reorders_existing_items_to_match_playlist():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="A", title="A"))
    library.items.append(QueueItem(url="u", video_id="B", title="B"))
    library.items.append(QueueItem(url="u", video_id="C", title="C"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [
        _entry("C", "C", 0),
        _entry("A", "A", 1),
        _entry("B", "B", 2),
    ]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()

    assert [i.video_id for i in library.items] == ["C", "A", "B"]


def test_apply_diff_idempotent():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = [_entry("vid1", "T", 0)]
    engine = SyncEngine(library, yt_client, playlist_id="PLxxx")

    engine.run_sync_once()
    snapshot1 = [(i.video_id, i.status) for i in library.items]
    engine.run_sync_once()
    snapshot2 = [(i.video_id, i.status) for i in library.items]

    assert snapshot1 == snapshot2


def test_remove_invokes_on_remove_callback():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old"))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    on_remove = MagicMock()
    engine = SyncEngine(library, yt_client, playlist_id="PL", on_remove=on_remove)

    engine.run_sync_once()

    on_remove.assert_called_once_with("vid_old")


def test_remove_clears_cursor_if_was_pointing_at_removed():
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="vid_old"))
    library.cursor_video_id = "vid_old"
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    engine.run_sync_once()

    assert library.cursor_video_id is None


def test_remove_deletes_cache_files(tmp_path, monkeypatch):
    from crt import config
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))

    encoded_path = tmp_path / "vid_old_pal_crop.mp4"
    encoded_path.write_text("fake mp4")
    download_path = tmp_path / "vid_old.mp4"
    download_path.write_text("fake source")

    library = LibraryStore()
    library.items.append(QueueItem(
        url="u", video_id="vid_old",
        filename="vid_old_pal_crop.mp4",
        downloaded_path=str(download_path),
    ))
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    engine.run_sync_once()

    assert not encoded_path.exists()
    assert not download_path.exists()


def test_run_sync_once_handles_auth_error():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.side_effect = YouTubeAuthError("bad token")
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    engine.run_sync_once()

    assert engine.state == "degraded"
    assert "bad token" in engine.last_error


def test_run_sync_once_records_timestamp_on_success():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    assert engine.last_sync_at is None
    engine.run_sync_once()

    assert engine.last_sync_at is not None
    assert engine.state == "ok"
    assert engine.last_error is None


@pytest.mark.asyncio
async def test_poll_loop_runs_sync_at_interval():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    sync_calls = []
    orig_sync = engine.run_sync_once
    def counting_sync():
        sync_calls.append(1)
        orig_sync()
    engine.run_sync_once = counting_sync

    task = asyncio.create_task(engine.run_loop(interval_s=0.05, initial_delay_s=0))
    await asyncio.sleep(0.18)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(sync_calls) >= 3


@pytest.mark.asyncio
async def test_poll_loop_backs_off_on_error():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.side_effect = RuntimeError("transient")
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    task = asyncio.create_task(engine.run_loop(interval_s=0.05, initial_delay_s=0))
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert engine.state == "degraded"
    assert "transient" in engine.last_error


@pytest.mark.asyncio
async def test_kick_forces_immediate_iteration():
    library = LibraryStore()
    yt_client = MagicMock()
    yt_client.list_playlist_items.return_value = []
    engine = SyncEngine(library, yt_client, playlist_id="PL")

    sync_count = []
    orig = engine.run_sync_once
    def counting():
        sync_count.append(1)
        orig()
    engine.run_sync_once = counting

    # Long interval (60s) but kick should fire next iteration immediately
    task = asyncio.create_task(engine.run_loop(interval_s=60, initial_delay_s=0))
    await asyncio.sleep(0.05)  # let first iteration run
    initial_count = len(sync_count)
    engine.kick()
    await asyncio.sleep(0.05)  # let kicked iteration run
    final_count = len(sync_count)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert final_count > initial_count
