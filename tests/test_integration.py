"""Integration tests — require real Chromecast + internet + YouTube playlist.

Run with:
    source .env.integration
    pytest -m integration -v -s

Tests skip automatically if TEST_CHROMECAST_NAME or TEST_YT_PLAYLIST_ID are not set.
"""

import time

import httpx
import pytest


@pytest.mark.integration
def test_full_flow_sync_download_cast(integration_daemon, integration_config):
    """End-to-end: daemon polla YT, scarica, encoda, casta il primo video."""
    base = integration_daemon["url"]

    # Force sync
    r = httpx.post(f"{base}/control/sync")
    assert r.status_code == 202

    # Wait for sync to populate library
    deadline = time.monotonic() + 60
    items = []
    while time.monotonic() < deadline:
        items = httpx.get(f"{base}/library/items").json()["items"]
        if items:
            break
        time.sleep(2)
    assert items, "library not populated within 60s"

    # Wait for first item to be ready (download + encode)
    target_vid = items[0]["video_id"]
    deadline = time.monotonic() + integration_config["encode_wait_s"]
    cur = None
    while time.monotonic() < deadline:
        cur = next(
            (i for i in httpx.get(f"{base}/library/items").json()["items"] if i["video_id"] == target_vid),
            None,
        )
        if cur and cur["status"] == "ready":
            break
        time.sleep(3)
    assert cur and cur["status"] == "ready", (
        f"item not ready within {integration_config['encode_wait_s']}s"
    )

    # Trigger play
    r = httpx.post(f"{base}/control/play/{target_vid}")
    assert r.status_code == 200

    # Wait for playback to start
    time.sleep(5)
    status = httpx.get(f"{base}/status").json()
    assert status["player"]["state"] in ("casting", "playing")

    # Cleanup
    httpx.post(f"{base}/control/stop")


@pytest.mark.integration
def test_status_endpoint_shape(integration_daemon):
    """Verify /status returns the expected shape."""
    base = integration_daemon["url"]
    s = httpx.get(f"{base}/status").json()
    assert "youtube" in s
    assert "pipeline" in s
    assert "player" in s
    assert s["player"]["chromecast"] in ("connected", "disconnected")


@pytest.mark.integration
def test_library_endpoint_shape(integration_daemon):
    """Verify /library/items returns the expected shape."""
    base = integration_daemon["url"]
    body = httpx.get(f"{base}/library/items").json()
    assert "cursor_video_id" in body
    assert "loop_mode" in body
    assert "items" in body
