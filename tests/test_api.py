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


def test_status_reports_player_position_duration_and_menu():
    library = LibraryStore()
    library.cursor_video_id = "A"
    player = MagicMock()
    player.state = "paused"
    player.live_position = MagicMock(return_value=61.5)
    player.screen_state = "paused"
    player.selected_video_id = "B"
    receiver = MagicMock()
    receiver.duration = 600.0
    chromecast = MagicMock()
    chromecast.connected = True
    app = create_app(library, player=player, receiver=receiver)
    app.state.chromecast = chromecast

    p = TestClient(app).get("/status").json()["player"]

    assert p["state"] == "paused" and p["current_time_s"] == 61.5
    assert p["duration_s"] == 600.0 and p["menu_open"] is True
    assert p["current_video_id"] == "A" and p["chromecast"] == "connected"


def test_status_exposes_screen_and_selected_video():
    player = MagicMock()
    player.state = "idle"
    player.live_position = MagicMock(return_value=0.0)
    player.screen_state = "stopped"
    player.selected_video_id = "B"
    p = TestClient(create_app(LibraryStore(), player=player)).get("/status").json()["player"]
    assert p["screen"] == "stopped" and p["selected_video_id"] == "B"
    assert p["menu_open"] is True

    player.screen_state = "none"
    player.selected_video_id = None
    p = TestClient(create_app(LibraryStore(), player=player)).get("/status").json()["player"]
    assert p["screen"] == "none" and p["selected_video_id"] is None
    assert p["menu_open"] is False


def test_status_without_player_or_receiver():
    p = TestClient(create_app(LibraryStore())).get("/status").json()["player"]

    assert p["state"] == "idle" and p["current_time_s"] is None
    assert p["duration_s"] is None and p["menu_open"] is False


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


def test_loop_toggle_goes_through_player():
    library = LibraryStore()
    player = MagicMock()

    def _toggle():
        library.loop_mode = True
    player.toggle_loop = MagicMock(side_effect=_toggle)

    resp = TestClient(_make_app(library, player=player)).post("/control/loop/toggle")

    player.toggle_loop.assert_called_once()
    assert resp.json() == {"ok": True, "loop_mode": True}


def test_sync_goes_through_player():
    player = MagicMock()
    sync_engine = MagicMock()

    resp = TestClient(_make_app(LibraryStore(), player=player, sync_engine=sync_engine)).post("/control/sync")

    assert resp.status_code == 202
    player.request_sync.assert_called_once()
    sync_engine.kick.assert_not_called()


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

def test_delete_current_returns_ack_from_delete_request():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", title="A"))
    library.cursor_video_id = "A"
    player = MagicMock()
    player.state = "idle"
    player.delete_request = AsyncMock(return_value=ActionResult(True, "launching"))
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True, "did_action": True, "reason": "launching",
        "cursor_video_id": "A", "state": "idle",
    }
    player.delete_request.assert_awaited_once()


def test_delete_current_without_cursor_is_a_no_items_ack():
    player = MagicMock()
    player.state = "idle"
    player.delete_request = AsyncMock(return_value=ActionResult(False, "no_items"))
    client = TestClient(_make_app(LibraryStore(), player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 200
    assert resp.json()["did_action"] is False and resp.json()["reason"] == "no_items"


def test_delete_current_503_when_no_player():
    library = LibraryStore()
    library.cursor_video_id = "A"
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 503


# ─── /receiver ──────────────────────────────────────────────

def test_receiver_page_is_served_as_html():
    client = TestClient(create_app(LibraryStore()))
    r = client.get("/receiver")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert "<title>" in r.text


def test_receiver_font_route_serves_otf():
    client = TestClient(create_app(LibraryStore()))
    r = client.get("/receiver/bedstead.otf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/otf"
    assert r.headers["cache-control"] == "max-age=86400"
    assert r.content[:4] == b"OTTO"


def test_receiver_report_is_forwarded_to_channel():
    receiver = MagicMock()
    client = TestClient(create_app(LibraryStore(), receiver=receiver))
    body = {"event": "status", "position": 1.0, "duration": 2.0, "paused": False, "src": "u"}
    r = client.post("/receiver/report", json=body)
    assert r.json() == {"ok": True}
    receiver.handle_report.assert_called_once_with(body)


def test_receiver_endpoints_503_without_channel():
    client = TestClient(create_app(LibraryStore()))
    assert client.post("/receiver/report", json={}).status_code == 503
    assert client.get("/receiver/events").status_code == 503


def test_media_supports_range_requests(tmp_path):
    (tmp_path / "v.mp4").write_bytes(bytes(1000))
    client = TestClient(create_app(LibraryStore(), media_dir=str(tmp_path)))
    r = client.get("/media/v.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 100-199/1000"
    assert len(r.content) == 100


# ─── real PlayerCore on the event loop ───────────────────────────

@pytest.mark.asyncio
async def test_loop_toggle_and_sync_flash_on_screen_with_a_real_player(tmp_path, monkeypatch):
    """The handlers must run on the event loop: the player schedules timers
    and feeds asyncio queues, which a threadpool call cannot do."""
    import httpx
    from crt import config
    from crt.player_core import PlayerCore
    from crt.receiver import ReceiverChannel

    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    library = LibraryStore()
    library.items.append(QueueItem(url="u/a", video_id="a", title="a", status="ready", filename="a.mp4"))
    library.cursor_video_id = "a"
    (tmp_path / "a.mp4").write_bytes(b"x")
    cc = MagicMock()
    cc.connected = True
    cc.receiver_app_active = True
    cc.wait_for_connection = AsyncMock()
    receiver = ReceiverChannel({"top": 0, "bottom": 0, "left": 0, "right": 0})
    receiver.subscribe()
    kicked = []
    player = PlayerCore(library, cc, receiver, on_sync_request=lambda: kicked.append(1))
    await player.play("a")
    await player.toggle()  # pause: the screen opens
    assert player.screen_state == "paused"

    app = create_app(library=library, player=player, sync_engine=MagicMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/control/loop/toggle")
        assert r1.status_code == 200 and r1.json()["loop_mode"] is True
        assert player.screen.flash == "Loop attivo"
        r2 = await c.post("/control/sync")
        assert r2.status_code == 202
        assert player.screen.flash == "Sync avviato"
    assert kicked == [1]
