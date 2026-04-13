from __future__ import annotations

import logging
import os
import threading
import time

import uvicorn

import config
from chromecast_mgr import ChromecastManager
from media_server import create_media_app
from pipeline import PipelineWorker
from queue_manager import QueueManager
from ui import CRTCastApp

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
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

    # Create core components
    queue = QueueManager()
    chromecast = ChromecastManager()
    pipeline = PipelineWorker(queue, chromecast)

    # Create and run the TUI app
    # Background tasks (chromecast discovery, pipeline worker) are started
    # from CRTCastApp.on_mount() which runs inside Textual's asyncio loop
    app = CRTCastApp(queue, pipeline, chromecast)
    app.run()


if __name__ == "__main__":
    main()
