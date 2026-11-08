from __future__ import annotations

import logging
import os

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from crt.receiver import ReceiverChannel, sse_events

log = logging.getLogger(__name__)

RECEIVER_PAGE = os.path.join(os.path.dirname(__file__), "receiver_page", "index.html")


def create_app(
    library,
    player=None,
    sync_engine=None,
    pipeline=None,
    media_dir: str | None = None,
    receiver: ReceiverChannel | None = None,
) -> FastAPI:
    """Build the unified FastAPI app exposing /library, /control, /status and /media."""
    if media_dir is None:
        from crt import config
        media_dir = config.TEMP_DIR
    app = FastAPI(title="crt-player daemon")

    def _ack(result, **extra) -> dict:
        """Translate a PlayerCore ActionResult into a structured response so a
        caller (BLE bridge / TUI) can tell "action performed" from "no-op"
        (issue #6). Backwards-compatible: adds fields, never removes them."""
        did_action = getattr(result, "did_action", True)
        reason = getattr(result, "reason", None)
        body = {"ok": True, "did_action": did_action, "reason": reason}
        body.update(extra)
        return body

    # ─── Read endpoints ─────────────────────────────────────────────

    @app.get("/library/items")
    def get_library_items():
        return {
            "cursor_video_id": library.cursor_video_id,
            "loop_mode": library.loop_mode,
            "items": [
                {
                    "video_id": item.video_id,
                    "id": item.id,
                    "title": item.title,
                    "status": item.status,
                    "progress": item.progress,
                    "error": item.error,
                    "is_cursor": item.video_id == library.cursor_video_id,
                }
                for item in library.items
            ],
        }

    @app.get("/status")
    def get_status():
        yt = sync_engine
        pl = pipeline
        pc = player
        cc = getattr(app.state, "chromecast", None)

        return {
            "youtube": {
                "state": getattr(yt, "state", "ok") if yt else "disabled",
                "last_sync_at": getattr(yt, "last_sync_at", None) if yt else None,
                "last_error": getattr(yt, "last_error", None) if yt else None,
                "playlist_id": getattr(yt, "playlist_id", None) if yt else None,
                "playlist_size": len(library.items),
            },
            "pipeline": {
                "state": getattr(pl, "state", "idle") if pl else "idle",
                "current_video_id": getattr(pl, "current_video_id", None) if pl else None,
                "queue_depth": sum(1 for i in library.items if i.status == "queued"),
            },
            "player": {
                "state": getattr(pc, "state", "idle") if pc else "idle",
                "current_video_id": library.cursor_video_id,
                # Position and duration come from the receiver page.
                "current_time_s": pc.live_position() if pc else None,
                "duration_s": receiver.duration if receiver is not None else None,
                "menu_open": getattr(pc, "menu", None) is not None if pc else False,
                "chromecast": "connected" if (cc and getattr(cc, "connected", False)) else "disconnected",
            },
        }

    # ─── Control endpoints ─────────────────────────────────────────

    @app.post("/control/next")
    async def control_next():
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.next()
        return _ack(
            result,
            cursor_video_id=library.cursor_video_id,
            state=player.state,
        )

    @app.post("/control/prev")
    async def control_prev():
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.prev()
        return _ack(
            result,
            cursor_video_id=library.cursor_video_id,
            state=player.state,
        )

    @app.post("/control/toggle")
    async def control_toggle():
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.toggle()
        return _ack(
            result,
            state=player.state,
            cursor_video_id=library.cursor_video_id,
        )

    @app.post("/control/stop")
    async def control_stop():
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.stop()
        return _ack(result, state=player.state)

    @app.post("/control/play/{video_id}")
    async def control_play(video_id: str):
        if player is None:
            raise HTTPException(503, "player unavailable")
        try:
            result = await player.play(video_id)
        except KeyError:
            raise HTTPException(404, f"video_id {video_id} not in library")
        return _ack(
            result,
            cursor_video_id=library.cursor_video_id,
            state=player.state,
        )

    @app.post("/control/loop/toggle")
    def control_loop_toggle():
        library.loop_mode = not library.loop_mode
        return {"ok": True, "loop_mode": library.loop_mode}

    @app.post("/control/sync", status_code=202)
    def control_sync():
        if sync_engine is None:
            raise HTTPException(503, "sync engine unavailable")
        sync_engine.kick()
        return {"ok": True}

    @app.post("/control/calibrate")
    async def control_calibrate():
        if player is None:
            raise HTTPException(503, "player unavailable")
        await player.calibrate()
        return {"ok": True}

    @app.post("/control/seek/back/{seconds}")
    async def control_seek_back(seconds: int):
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.seek_relative(-seconds)
        return _ack(result)

    @app.post("/control/seek/forward/{seconds}")
    async def control_seek_forward(seconds: int):
        if player is None:
            raise HTTPException(503, "player unavailable")
        result = await player.seek_relative(seconds)
        return _ack(result)

    @app.post("/control/delete/current")
    async def control_delete_current():
        if player is None:
            raise HTTPException(503, "player unavailable")
        video_id = library.cursor_video_id
        if not video_id:
            raise HTTPException(404, "no current video")
        await player.delete_current()
        return {"ok": True, "deleted_video_id": video_id}

    # ─── Receiver page (opened on the Chromecast through DashCast) ──

    @app.get("/receiver")
    def receiver_page():
        with open(RECEIVER_PAGE, encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/receiver/events")
    def receiver_events():
        if receiver is None:
            raise HTTPException(503, "receiver unavailable")
        return StreamingResponse(
            sse_events(receiver),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # async on purpose: handle_report touches asyncio primitives and the
    # player's state, so it must run on the event loop, not in the threadpool.
    @app.post("/receiver/report")
    async def receiver_report(report: dict = Body(...)):
        if receiver is None:
            raise HTTPException(503, "receiver unavailable")
        receiver.handle_report(report)
        return {"ok": True}

    # ─── Media file serving ────────────────────────────────────────

    @app.get("/media/{filename}")
    async def serve_media(filename: str):
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(404, "not found")
        filepath = os.path.join(media_dir, filename)
        if not os.path.isfile(filepath):
            raise HTTPException(404, "not found")
        return FileResponse(filepath, media_type="video/mp4")

    return app
