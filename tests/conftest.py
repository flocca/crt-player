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
    import crt.config as cfg
    from crt.api import create_app

    d = tmp_path_factory.mktemp("integration_media")
    _orig_temp_dir = cfg.TEMP_DIR
    _orig_state_file = cfg.STATE_FILE
    cfg.TEMP_DIR = str(d)
    cfg.STATE_FILE = str(d / "test_state.json")  # avoid polluting the real state file

    app = create_app(LibraryStore(), media_dir=str(d))
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
def real_queue():
    """Fresh LibraryStore per test — no saved state loaded."""
    return LibraryStore()


@pytest.fixture
def real_pipeline(real_queue, real_chromecast_per_test):
    """Fresh PipelineWorker per test.

    Must be function-scoped because PipelineWorker holds an internal reference
    to its queue; creating it fresh alongside real_queue ensures the pipeline
    sees the same queue the test does.
    """
    from crt.pipeline import PipelineWorker
    return PipelineWorker(real_queue, real_chromecast_per_test)


# TODO Phase 7: integration_app fixture will be rewritten to use the daemon HTTP API
# instead of the in-process TUI. For now it is intentionally omitted so that
# test_integration.py fails at collection time (skipped by -m integration anyway).
