from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from typing import Callable

import uvicorn

from crt import config
from crt.api import create_app
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import LibraryStore
from crt.pipeline import PipelineWorker
from crt.player_core import PlayerCore
from crt.receiver import ReceiverChannel
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


def cleanup_temp_files(library: LibraryStore) -> None:
    """Remove cache files older than FILE_TTL_HOURS that no library item uses.

    Files of videos still in the library are kept however old they are
    (issue #16): deleting them made every restart re-download and re-encode
    the whole playlist. A video leaving the playlist has its files deleted by
    the sync; this TTL only catches the orphans that slipped through.
    """
    if config.FILE_TTL_HOURS <= 0:
        return
    if not os.path.isdir(config.TEMP_DIR):
        return
    in_use: set[str] = set()
    for item in library.items:
        if item.filename:
            in_use.add(item.filename)
            in_use.add(config.preview_filename(item.filename))
        if item.video_id:
            canonical = config.cached_encoded_filename(item.video_id)
            in_use.add(canonical)
            in_use.add(config.preview_filename(canonical))
        if item.downloaded_path:
            in_use.add(os.path.basename(item.downloaded_path))
    cutoff = time.time() - config.FILE_TTL_HOURS * 3600
    for fname in os.listdir(config.TEMP_DIR):
        if fname in in_use:
            continue
        fpath = os.path.join(config.TEMP_DIR, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            log.info("Removing old temp file: %s", fname)
            try:
                os.remove(fpath)
            except OSError:
                pass


async def periodic_save_loop(
    library, position: Callable[[], float], state_file: str, interval_s: float
) -> None:
    """Persist state to disk every interval_s seconds (issue #4).

    The original code only saved on a graceful SIGTERM shutdown, so a hard
    kill / OOM / host reboot lost cursor, loop_mode and playback position.
    This loop bounds that loss to one interval. The shutdown path keeps its
    own final flush. A failing save is logged and retried on the next tick —
    it must never kill the loop.

    `position` is PlayerCore.live_position: the page's position, extrapolated
    between reports, and 0.0 while idle or calibrating (save_state ignores a
    zero, so the stored resume point survives).
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            pos = position() or 0.0
            library.save_state(state_file, playback_position=pos)
            log.debug("periodic save_state -> %s (pos=%.1f)", state_file, pos)
        except Exception:
            log.exception("periodic save_state failed")


async def main_async() -> None:
    os.makedirs(config.TEMP_DIR, exist_ok=True)

    library = LibraryStore()
    library.load_state(config.STATE_FILE)
    # After load_state: the TTL must know which files the library still uses.
    cleanup_temp_files(library)
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(library)

    yt_client = None
    if config.YT_PLAYLIST_ID:
        try:
            yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)
        except (YouTubeAuthError, FileNotFoundError) as e:
            log.warning("YouTube client unavailable, remote delete + sync disabled: %s", e)

    receiver = ReceiverChannel({
        "top": config.MARGIN_TOP,
        "bottom": config.MARGIN_BOTTOM,
        "left": config.MARGIN_LEFT,
        "right": config.MARGIN_RIGHT,
    })
    player = PlayerCore(
        library, chromecast, receiver, youtube_client=yt_client,
        on_cache_miss=pipeline.wake_prepare,
    )

    # Capture the running loop here so callbacks invoked from worker threads
    # (e.g. SyncEngine.run_sync_once dispatched via asyncio.to_thread) can
    # schedule coroutines back onto it. asyncio.get_event_loop() does not
    # work from non-event-loop threads in Python 3.12+.
    main_loop = asyncio.get_running_loop()

    # Pipeline updates (progress, a finished encode) come from worker threads
    # too: redraw an open pause menu on the loop.
    pipeline.set_update_callback(
        lambda: main_loop.call_soon_threadsafe(player.refresh_menu),
    )

    sync_engine = None
    if yt_client is not None:
        def _on_yt_remove(video_id: str):
            asyncio.run_coroutine_threadsafe(player.stop_and_remove(video_id), main_loop)
            # Stop a download/encode of the removed video too (issue #17).
            main_loop.call_soon_threadsafe(pipeline.cancel_prepare_for, video_id)

        def _on_yt_add():
            # run_sync_once runs in a worker thread (asyncio.to_thread), so
            # wake_prepare() -> asyncio.Event.set() must be marshalled onto the
            # event loop or the awaiting prepare loop is not reliably woken
            # (symmetric to _on_yt_remove's run_coroutine_threadsafe).
            main_loop.call_soon_threadsafe(pipeline.wake_prepare)

        sync_engine = SyncEngine(
            library, yt_client, config.YT_PLAYLIST_ID,
            on_remove=_on_yt_remove,
            on_add=_on_yt_add,
            on_synced=player.refresh_menu,
        )
        log.info("SyncEngine ready (playlist=%s)", config.YT_PLAYLIST_ID)
        # Menu command "Sync now".
        player.on_sync_request = sync_engine.kick

    app = create_app(
        library=library,
        player=player,
        sync_engine=sync_engine,
        pipeline=pipeline,
        media_dir=config.TEMP_DIR,
        receiver=receiver,
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
        # One-shot: calibrate() never waits for ffmpeg (CRT_MARGIN_* only
        # change with a restart, so one render serves the whole run).
        asyncio.create_task(player.prepare_calibration(), name="calibration_clip"),
        # One-shot: previews of items encoded before the copy existed.
        asyncio.create_task(pipeline.backfill_previews(), name="preview_backfill"),
        asyncio.create_task(
            player.watch_idle(config.RECEIVER_IDLE_CLOSE_S), name="player_idle_close",
        ),
        asyncio.create_task(
            periodic_save_loop(
                library, player.live_position, config.STATE_FILE, config.SAVE_INTERVAL_S
            ),
            name="periodic_save",
        ),
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

    library.save_state(config.STATE_FILE, playback_position=player.live_position())
    chromecast.set_connection_callback(None)
    pipeline.cancel_current()
    chromecast.quit_app()
    chromecast.shutdown()
    log.info("Daemon stopped")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
