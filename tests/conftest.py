import os
import threading
import time

import pytest
import uvicorn

from crt.library_store import LibraryStore


@pytest.fixture
def queue():
    """Real LibraryStore — pure data, no I/O."""
    return LibraryStore()


# ---------------------------------------------------------------------------
# Integration fixtures — require real hardware (Chromecast + internet access)
# Tests skip automatically when env vars are absent.
# Run with: source .env.integration && pytest -m integration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def integration_config():
    """Read TEST_* env vars; skip entire session if required ones are missing."""
    name = os.environ.get("TEST_CHROMECAST_NAME", "").strip()
    playlist = os.environ.get("TEST_YT_PLAYLIST_ID", "").strip()
    if not name or not playlist:
        pytest.skip(
            "Integration tests require TEST_CHROMECAST_NAME and TEST_YT_PLAYLIST_ID env vars. "
            "Run: source .env.integration"
        )
    return {
        "chromecast_name": name,
        "playlist_id": playlist,
        "playback_wait_s": int(os.environ.get("TEST_PLAYBACK_WAIT_S", "300")),
        "encode_wait_s": int(os.environ.get("TEST_ENCODE_WAIT_S", "600")),
    }


@pytest.fixture(scope="session")
def real_chromecast(integration_config):
    """Real ChromecastManager, discovered once per test session."""
    import crt.config as cfg
    from crt.chromecast_mgr import ChromecastManager

    cfg.CHROMECAST_NAME = integration_config["chromecast_name"]
    cc = ChromecastManager()
    found = cc._discover_sync()
    if not found:
        pytest.skip(
            f"Chromecast '{integration_config['chromecast_name']}' not found on network"
        )
    yield cc
    cc.set_status_callback(None)
    cc.set_connection_callback(None)
    cc.shutdown()


@pytest.fixture
def real_chromecast_per_test(real_chromecast):
    """Re-create asyncio.Event objects bound to the current test's event loop.

    real_chromecast is session-scoped (expensive to discover) but asyncio.Event
    objects must be re-created per test to bind to the current event loop.

    Teardown stops any ongoing Chromecast playback so the next test starts from
    a clean state. Without this, _poll_playback in the next test repeatedly calls
    poll_status() (a blocking pychromecast call) on an actively-playing device,
    which saturates the asyncio thread pool and starves fetch_title() from getting
    a worker thread — leaving the queue item stuck in "queued" indefinitely.
    """
    import asyncio
    real_chromecast._connected_event = asyncio.Event()
    real_chromecast._playback_ended_event = asyncio.Event()
    if real_chromecast.connected:
        real_chromecast._connected_event.set()
    yield real_chromecast
    # Teardown: stop playback so the next test sees an idle Chromecast.
    try:
        real_chromecast.stop()
    except Exception:
        pass


@pytest.fixture
def integration_daemon(integration_config, real_chromecast_per_test, tmp_path_factory):
    """Avvia il daemon HTTP completo in-process per il test."""
    import asyncio
    import threading
    import time
    import uvicorn
    from crt import config as cfg
    from crt.api import create_app
    from crt.library_store import LibraryStore
    from crt.pipeline import PipelineWorker
    from crt.player_core import PlayerCore
    from crt.sync_engine import SyncEngine
    from crt.youtube_client import YouTubeClient

    d = tmp_path_factory.mktemp("integration_daemon")
    cfg.TEMP_DIR = str(d / "cache")
    os.makedirs(cfg.TEMP_DIR, exist_ok=True)
    cfg.STATE_FILE = str(d / "state.json")
    cfg.CHROMECAST_NAME = integration_config["chromecast_name"]
    cfg.YT_PLAYLIST_ID = integration_config["playlist_id"]

    library = LibraryStore()
    cc = real_chromecast_per_test
    pipeline = PipelineWorker(library, cc)
    player = PlayerCore(library, cc)
    yt = YouTubeClient.from_token_file(cfg.YT_TOKEN_FILE, cfg.YT_CLIENT_SECRETS)
    sync_engine = SyncEngine(library, yt, cfg.YT_PLAYLIST_ID)

    app = create_app(
        library=library,
        player=player,
        sync_engine=sync_engine,
        pipeline=pipeline,
        media_dir=cfg.TEMP_DIR,
    )
    app.state.chromecast = cc

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.SERVER_PORT, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    yield {
        "library": library,
        "player": player,
        "sync_engine": sync_engine,
        "chromecast": cc,
        "pipeline": pipeline,
        "url": f"http://localhost:{cfg.SERVER_PORT}",
    }

    server.should_exit = True
    cc.set_status_callback(None)
    cc.set_connection_callback(None)
