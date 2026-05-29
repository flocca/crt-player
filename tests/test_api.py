from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from crt.api import create_app
from crt.library_store import LibraryStore, QueueItem
from crt.player_core import ActionResult


def _make_app(library, player=None, sync_engine=None, pipeline=None):
    return create_app(library=library, player=player, sync_engine=sync_engine, pipeline=pipeline)


# ─── /library/items ──────────────────────────────────────────────

def test_get_library_items_empty():
    library = LibraryStore()
    client = TestClient(_make_app(library))

    resp = client.get("/library/items")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cursor_video_id"] is None
    assert body["loop_mode"] is False
    assert body["items"] == []


def test_get_library_items_with_cursor():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", title="Title A", status="ready"))
    library.items.append(QueueItem(url="u/B", video_id="B", title="Title B", status="queued"))
    library.cursor_video_id = "A"
    client = TestClient(_make_app(library))

    resp = client.get("/library/items")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cursor_video_id"] == "A"
    assert len(body["items"]) == 2
    assert body["items"][0]["video_id"] == "A"
    assert body["items"][0]["is_cursor"] is True
    assert body["items"][1]["is_cursor"] is False


# ─── /status ──────────────────────────────────────────────

def test_get_status_includes_youtube_pipeline_player():
    library = LibraryStore()
    sync_engine = MagicMock()
    sync_engine.state = "ok"
    sync_engine.last_sync_at = "2026-04-21T12:00:00+00:00"
    sync_engine.last_error = None
    sync_engine.playlist_id = "PLxxx"

    pipeline = MagicMock()
    pipeline.state = "idle"
    pipeline.current_video_id = None

    player = MagicMock()
    player.state = "idle"

    chromecast = MagicMock()
    chromecast.connected = True
    chromecast.current_time = 0.0
    chromecast.duration = 0.0

    app = create_app(library, player=player, sync_engine=sync_engine, pipeline=pipeline)
    app.state.chromecast = chromecast

    client = TestClient(app)
    resp = client.get("/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["youtube"]["state"] == "ok"
    assert body["youtube"]["last_sync_at"] == "2026-04-21T12:00:00+00:00"
    assert body["pipeline"]["state"] == "idle"
    assert body["player"]["state"] == "idle"
    assert body["player"]["chromecast"] == "connected"


# ─── /control/* ──────────────────────────────────────────────

def test_post_control_next_calls_player():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", status="ready"))

    player = MagicMock()
    async def _next():
        library.cursor_video_id = "A"
        return ActionResult(True)
    player.next = _next

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/next")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["cursor_video_id"] == "A"
    assert body["did_action"] is True
    assert body["reason"] is None


def test_post_control_prev_calls_player():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A"))
    library.items.append(QueueItem(url="u/B", video_id="B"))
    library.cursor_video_id = "B"

    player = MagicMock()
    async def _prev():
        library.cursor_video_id = "A"
        return ActionResult(True)
    player.prev = _prev

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/prev")

    assert resp.status_code == 200
    assert resp.json()["cursor_video_id"] == "A"


def test_post_control_next_reports_no_op():
    """Issue #6: a no-op (nothing playable) must be distinguishable on the wire."""
    library = LibraryStore()
    player = MagicMock()
    async def _next():
        return ActionResult(False, "no_playable_item")
    player.next = _next

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/next")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True  # request succeeded
    assert body["did_action"] is False
    assert body["reason"] == "no_playable_item"


def test_post_control_toggle_reports_no_op():
    library = LibraryStore()
    player = MagicMock()
    player.state = "idle"
    async def _toggle():
        return ActionResult(False, "no_playable_item")
    player.toggle = _toggle

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/toggle")

    body = resp.json()
    assert body["did_action"] is False
    assert body["reason"] == "no_playable_item"


def test_post_control_seek_reports_no_session():
    library = LibraryStore()
    player = MagicMock()
    async def _seek(n):
        return ActionResult(False, "no_chromecast_session")
    player.seek_relative = _seek

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/seek/forward/30")

    body = resp.json()
    assert body["did_action"] is False
    assert body["reason"] == "no_chromecast_session"


def test_post_control_stop_calls_player():
    library = LibraryStore()
    player = MagicMock()
    called = []
    async def _stop():
        called.append(1)
        return ActionResult(True)
    player.stop = _stop

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/stop")

    assert resp.status_code == 200
    assert called == [1]


def test_post_control_toggle_returns_state():
    library = LibraryStore()
    player = MagicMock()
    player.state = "playing"
    async def _toggle():
        player.state = "paused"
        return ActionResult(True)
    player.toggle = _toggle

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/toggle")

    assert resp.status_code == 200
    assert resp.json()["state"] == "paused"


def test_post_control_play_video_id():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", status="ready"))
    library.items.append(QueueItem(url="u/B", video_id="B", status="ready"))

    player = MagicMock()
    async def _play(vid):
        library.cursor_video_id = vid
        return ActionResult(True)
    player.play = _play

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/play/B")

    assert resp.status_code == 200
    assert resp.json()["cursor_video_id"] == "B"


def test_post_control_play_unknown_video_id_returns_404():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A"))

    player = MagicMock()
    async def _play(vid):
        raise KeyError(vid)
    player.play = _play

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/play/UNKNOWN")

    assert resp.status_code == 404


def test_post_control_loop_toggle_inverts():
    library = LibraryStore()
    library.loop_mode = False

    client = TestClient(_make_app(library))

    resp1 = client.post("/control/loop/toggle")
    resp2 = client.post("/control/loop/toggle")

    assert resp1.json()["loop_mode"] is True
    assert resp2.json()["loop_mode"] is False


def test_post_control_sync_kicks_engine():
    library = LibraryStore()
    sync_engine = MagicMock()

    client = TestClient(_make_app(library, sync_engine=sync_engine))
    resp = client.post("/control/sync")

    assert resp.status_code == 202
    sync_engine.kick.assert_called_once()


def test_post_control_calibrate_invokes_player():
    library = LibraryStore()
    player = MagicMock()
    called = []
    async def _calibrate():
        called.append(1)
    player.calibrate = _calibrate

    client = TestClient(_make_app(library, player=player))
    resp = client.post("/control/calibrate")

    assert resp.status_code == 200
    assert called == [1]


def test_post_control_when_player_unavailable_returns_503():
    library = LibraryStore()
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/next")
    assert resp.status_code == 503

    resp = client.post("/control/toggle")
    assert resp.status_code == 503


def test_post_control_sync_when_engine_unavailable_returns_503():
    library = LibraryStore()
    client = TestClient(_make_app(library, sync_engine=None))

    resp = client.post("/control/sync")
    assert resp.status_code == 503


# ─── /media ──────────────────────────────────────────────

def test_get_media_serves_file(tmp_path):
    f = tmp_path / "test.mp4"
    f.write_bytes(b"FAKE_MP4_BYTES")

    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/test.mp4")

    assert resp.status_code == 200
    assert resp.content == b"FAKE_MP4_BYTES"
    assert resp.headers["content-type"].startswith("video/mp4")


def test_get_media_404_for_missing(tmp_path):
    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/missing.mp4")
    assert resp.status_code == 404


def test_get_media_rejects_path_traversal(tmp_path):
    library = LibraryStore()
    app = create_app(library, media_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.get("/media/..%2Fetc%2Fpasswd")
    assert resp.status_code == 404


# ─── /control/seek/* ──────────────────────────────────────────────

def test_seek_back_calls_player_with_negative_seconds():
    library = LibraryStore()
    player = MagicMock()
    player.seek_relative = AsyncMock(return_value=ActionResult(True))
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/seek/back/15")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["did_action"] is True
    player.seek_relative.assert_awaited_once_with(-15)


def test_seek_forward_calls_player_with_positive_seconds():
    library = LibraryStore()
    player = MagicMock()
    player.seek_relative = AsyncMock(return_value=ActionResult(True))
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/seek/forward/30")

    assert resp.status_code == 200
    player.seek_relative.assert_awaited_once_with(30)


def test_seek_back_503_when_no_player():
    library = LibraryStore()
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/seek/back/15")

    assert resp.status_code == 503


# ─── /control/delete/current ──────────────────────────────────────

def test_delete_current_success():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", title="A"))
    library.cursor_video_id = "A"
    player = MagicMock()
    player.delete_current = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted_video_id": "A"}
    player.delete_current.assert_awaited_once()


def test_delete_current_404_when_no_cursor():
    library = LibraryStore()  # cursor unset
    player = MagicMock()
    player.delete_current = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 404
    player.delete_current.assert_not_awaited()


def test_delete_current_503_when_no_player():
    library = LibraryStore()
    library.cursor_video_id = "A"
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 503
