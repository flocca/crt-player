from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time

import uvicorn

from crt import config
from crt.api import create_app
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore
from crt.pipeline import PipelineWorker
from crt.player_core import PlayerCore
from crt.sync_engine import SyncEngine
from crt.youtube_client import YouTubeAuthError, YouTubeClient

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "crt_cast.log")
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="w"),
    ],
)
log = logging.getLogger(__name__)


def cleanup_temp_files() -> None:
    if config.FILE_TTL_HOURS <= 0:
        return
    if not os.path.isdir(config.TEMP_DIR):
        return
    cutoff = time.time() - config.FILE_TTL_HOURS * 3600
    for fname in os.listdir(config.TEMP_DIR):
        fpath = os.path.join(config.TEMP_DIR, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            log.info("Removing old temp file: %s", fname)
            try:
                os.remove(fpath)
            except OSError:
                pass


async def main_async() -> None:
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    cleanup_temp_files()

    library = LibraryStore()
    library.load_state(config.STATE_FILE)
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(library, chromecast)

    yt_client = None
    if config.YT_PLAYLIST_ID:
        try:
            yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)
        except (YouTubeAuthError, FileNotFoundError) as e:
            log.warning("YouTube client unavailable, remote delete + sync disabled: %s", e)

    player = PlayerCore(library, chromecast, youtube_client=yt_client)

    # Capture the running loop here so callbacks invoked from worker threads
    # (e.g. SyncEngine.run_sync_once dispatched via asyncio.to_thread) can
    # schedule coroutines back onto it. asyncio.get_event_loop() does not
    # work from non-event-loop threads in Python 3.12+.
    main_loop = asyncio.get_running_loop()

    sync_engine = None
    if yt_client is not None:
        def _on_yt_remove(video_id: str):
            asyncio.run_coroutine_threadsafe(player.stop_and_remove(video_id), main_loop)

        def _on_yt_add():
            pipeline.wake_prepare()

        sync_engine = SyncEngine(
            library, yt_client, config.YT_PLAYLIST_ID,
            on_remove=_on_yt_remove,
            on_add=_on_yt_add,
        )
        log.info("SyncEngine ready (playlist=%s)", config.YT_PLAYLIST_ID)

    app = create_app(
        library=library,
        player=player,
        sync_engine=sync_engine,
        pipeline=pipeline,
        media_dir=config.TEMP_DIR,
    )
    app.state.chromecast = chromecast

    server_cfg = uvicorn.Config(
        app, host="0.0.0.0", port=config.SERVER_PORT, log_level="warning",
    )
    server = uvicorn.Server(server_cfg)

    tasks = [
        asyncio.create_task(server.serve(), name="uvicorn"),
        asyncio.create_task(chromecast.discover_loop(), name="cc_discovery"),
        asyncio.create_task(pipeline.run_prepare(), name="pipeline_prepare"),
        asyncio.create_task(player.watch_natural_end(), name="player_natural_end"),
    ]
    if sync_engine is not None:
        tasks.append(asyncio.create_task(
            sync_engine.run_loop(interval_s=config.SYNC_INTERVAL_S),
            name="sync_loop",
        ))

    # If load_state restored items in `queued` (or the first sync already populated
    # the library before run_prepare entered its wait), kick the pipeline once so
    # it picks them up immediately.
    if any(i.status == "queued" for i in library.items):
        pipeline.wake_prepare()

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("Daemon ready on port %d", config.SERVER_PORT)
    await stop_event.wait()
    log.info("Shutdown signal received")

    server.should_exit = True
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    library.save_state(config.STATE_FILE, playback_position=chromecast.current_time)
    chromecast.set_status_callback(None)
    chromecast.set_connection_callback(None)
    pipeline.cancel_current()
    if chromecast.cast:
        try:
            chromecast.cast.quit_app()
        except Exception:
            pass
    chromecast.shutdown()
    log.info("Daemon stopped")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
