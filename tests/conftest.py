from unittest.mock import AsyncMock, MagicMock

import pytest

from queue_manager import QueueManager
from ui import CRTCastApp


@pytest.fixture
def queue():
    """Real QueueManager — pure data, no I/O."""
    return QueueManager()


@pytest.fixture
def mock_pipeline():
    """Fully mocked PipelineWorker. Async entry points return immediately."""
    p = MagicMock()
    p.run_prepare = AsyncMock()
    p.run_cast = AsyncMock()
    p.wake = MagicMock()
    p.cancel_cast = MagicMock()
    p.cancel_prepare = MagicMock()
    p.set_update_callback = MagicMock()
    p.resume_position = 0.0
    p.loop_mode = False
    p._cast_enabled = False
    return p


@pytest.fixture
def mock_chromecast():
    """Fully mocked ChromecastManager. Defaults to disconnected."""
    c = MagicMock()
    c.discover_loop = AsyncMock()
    c.connected = False
    c.device_name = ""
    c.player_state = "UNKNOWN"
    c.current_time = 0.0
    c.duration = 0.0
    c.poll_status = MagicMock()
    c.pause_or_resume = MagicMock()
    c.stop = MagicMock()
    c.seek = MagicMock()
    c.adjust_volume = MagicMock()
    c.set_status_callback = MagicMock()
    c.set_connection_callback = MagicMock()
    c.wait_for_connection = AsyncMock()
    c.quit_app = MagicMock()
    return c


@pytest.fixture
def app(queue, mock_pipeline, mock_chromecast):
    """CRTCastApp with all I/O dependencies mocked."""
    return CRTCastApp(queue, mock_pipeline, mock_chromecast)


# ---------------------------------------------------------------------------
# Integration fixtures — require real hardware (Chromecast + internet access)
# Tests skip automatically when env vars are absent.
# Run with: source .env.integration && pytest -m integration
# ---------------------------------------------------------------------------

import os
import threading
import time

import uvicorn


@pytest.fixture(scope="session")
def integration_config():
    """Read TEST_* env vars; skip entire session if required ones are missing."""
    name = os.environ.get("TEST_CHROMECAST_NAME", "").strip()
    url1 = os.environ.get("TEST_VIDEO_URL_1", "").strip()
    if not name or not url1:
        pytest.skip(
            "Integration tests require TEST_CHROMECAST_NAME and TEST_VIDEO_URL_1 env vars. "
            "Run: source .env.integration"
        )
    return {
        "chromecast_name": name,
        "video_url_1": url1,
        "video_url_2": os.environ.get("TEST_VIDEO_URL_2", "").strip() or None,
        "playback_wait_s": int(os.environ.get("TEST_PLAYBACK_WAIT_S", "300")),
        "encode_wait_s": int(os.environ.get("TEST_ENCODE_WAIT_S", "600")),
    }


@pytest.fixture(scope="session")
def real_tmp_dir(integration_config, tmp_path_factory):
    """Dedicated temp dir for encoded files; starts the media server once for the session."""
    import config as cfg
    from media_server import create_media_app

    d = tmp_path_factory.mktemp("integration_media")
    _orig_temp_dir = cfg.TEMP_DIR
    _orig_state_file = cfg.STATE_FILE
    cfg.TEMP_DIR = str(d)
    cfg.STATE_FILE = str(d / "test_state.json")  # avoid polluting the real state file

    app = create_media_app(str(d))
    server_cfg = uvicorn.Config(
        app, host="0.0.0.0", port=cfg.SERVER_PORT, log_level="warning"
    )
    server = uvicorn.Server(server_cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn media server failed to start within 10s")
    yield str(d)
    cfg.TEMP_DIR = _orig_temp_dir
    cfg.STATE_FILE = _orig_state_file


@pytest.fixture(scope="session")
def real_chromecast(integration_config, real_tmp_dir):
    """Real ChromecastManager, discovered once per test session."""
    import config as cfg
    from chromecast_mgr import ChromecastManager

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
def real_queue():
    """Fresh QueueManager per test — no saved state loaded."""
    from queue_manager import QueueManager
    return QueueManager()


@pytest.fixture
def real_pipeline(real_queue, real_chromecast_per_test):
    """Fresh PipelineWorker per test.

    Must be function-scoped because PipelineWorker holds an internal reference
    to its queue; creating it fresh alongside real_queue ensures the pipeline
    sees the same queue the test does.
    """
    from pipeline import PipelineWorker
    return PipelineWorker(real_queue, real_chromecast_per_test)


@pytest.fixture
def integration_app(real_queue, real_pipeline, real_chromecast_per_test):
    """Full CRTCastApp wired with real dependencies, fresh per test."""
    from ui import CRTCastApp
    return CRTCastApp(real_queue, real_pipeline, real_chromecast_per_test)
