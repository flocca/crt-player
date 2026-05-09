from __future__ import annotations

import logging
import os
import sys
import threading
import time

import uvicorn

from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.media_server import create_media_app
from crt.pipeline import PipelineWorker
from crt.library_store import LibraryStore
from crt.ui import CRTCastApp

LOG_FILE = os.path.join(os.path.dirname(__file__), "crt_cast.log")
_log_fh = open(LOG_FILE, "w")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    stream=_log_fh,
)
# Redirect stderr to log file so unhandled exceptions are captured
sys.stderr = _log_fh
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
            os.remove(fpath)


def start_media_server() -> None:
    app = create_media_app(config.TEMP_DIR)
    server_config = uvicorn.Config(
        app, host="0.0.0.0", port=config.SERVER_PORT, log_level="warning"
    )
    server = uvicorn.Server(server_config)
    server.run()


def main() -> None:
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    cleanup_temp_files()

    # Start media server in background thread
    media_thread = threading.Thread(target=start_media_server, daemon=True)
    media_thread.start()

    # Create core components and restore saved state
    queue = LibraryStore()
    saved_position = queue.load_state(config.STATE_FILE)
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(queue, chromecast)
    pipeline.resume_position = saved_position

    # Create and run the TUI app
    # Background tasks (chromecast discovery, pipeline worker) are started
    # from CRTCastApp.on_mount() which runs inside Textual's asyncio loop
    app = CRTCastApp(queue, pipeline, chromecast)
    app.run()

    # Save state before shutdown
    queue.save_state(config.STATE_FILE, playback_position=chromecast.current_time)

    # Detach callbacks so pychromecast status updates don't hit dead widgets
    chromecast.set_status_callback(None)
    chromecast.set_connection_callback(None)

    # Graceful shutdown
    pipeline.cancel_current()
    if chromecast.cast:
        try:
            chromecast.cast.quit_app()
        except Exception:
            pass
    chromecast.shutdown()


if __name__ == "__main__":
    main()
