"""Integration tests — require real Chromecast + internet.

Run with:
    source .env.integration
    pytest -m integration -v -s

Tests skip automatically if TEST_CHROMECAST_NAME or TEST_VIDEO_URL_1 are not set.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import Input

from ui import CRTCastApp, NowPlayingWidget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def wait_for_condition(
    pilot,
    fn,
    *,
    timeout_s: float = 30,
    poll_interval: float = 1.0,
    description: str = "condition",
) -> None:
    """Yield to the Textual event loop until fn() is True or timeout expires.

    pilot.pause(n) cedes control to Textual's asyncio loop for n seconds,
    keeping pipeline tasks and callbacks alive while we wait for external state.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        await pilot.pause(poll_interval)
        if fn():
            return
    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for: {description}"
    )


async def get_video_info(path: str) -> dict:
    """Run ffprobe and return dict with width, height, fps (float), codec."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate",
        "-of", "json",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    streams = data.get("streams", [{}])
    if not streams:
        return {}
    s = streams[0]
    fps_str = s.get("r_frame_rate", "0/1")
    num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
    fps = float(num) / float(den) if float(den) != 0 else 0.0
    return {
        "codec": s.get("codec_name", ""),
        "width": s.get("width", 0),
        "height": s.get("height", 0),
        "fps": fps,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_integration_single_video_plays(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    pass  # implemented in Task 4


@pytest.mark.integration
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_integration_playback_completes(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    pass  # implemented in Task 5


@pytest.mark.integration
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_integration_queue_transition(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    pass  # implemented in Task 6
