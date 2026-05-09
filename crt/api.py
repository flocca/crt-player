from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException

log = logging.getLogger(__name__)


def create_app(
    library,
    player=None,
    sync_engine=None,
    pipeline=None,
    media_dir: str | None = None,
) -> FastAPI:
    """Build the unified FastAPI app exposing /library, /control, /status (and /media in task 4.6)."""
    app = FastAPI(title="crt-player daemon")

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
                "current_time_s": getattr(cc, "current_time", None) if cc else None,
                "duration_s": getattr(cc, "duration", None) if cc else None,
                "chromecast": "connected" if (cc and getattr(cc, "connected", False)) else "disconnected",
            },
        }

    # ─── Control endpoints ─────────────────────────────────────────

    @app.post("/control/next")
    async def control_next():
        if player is None:
            raise HTTPException(503, "player unavailable")
        await player.next()
        return {"ok": True, "cursor_video_id": library.cursor_video_id}

    @app.post("/control/prev")
    async def control_prev():
        if player is None:
            raise HTTPException(503, "player unavailable")
        await player.prev()
        return {"ok": True, "cursor_video_id": library.cursor_video_id}

    @app.post("/control/toggle")
    async def control_toggle():
        if player is None:
            raise HTTPException(503, "player unavailable")
        await player.toggle()
        return {"ok": True, "state": player.state}

    @app.post("/control/stop")
    async def control_stop():
        if player is None:
            raise HTTPException(503, "player unavailable")
        await player.stop()
        return {"ok": True}

    @app.post("/control/play/{video_id}")
    async def control_play(video_id: str):
        if player is None:
            raise HTTPException(503, "player unavailable")
        try:
            await player.play(video_id)
        except KeyError:
            raise HTTPException(404, f"video_id {video_id} not in library")
        return {"ok": True, "cursor_video_id": library.cursor_video_id}

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

    return app
