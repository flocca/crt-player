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
    loop = asyncio.get_running_loop()
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
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed with exit code {proc.returncode} for path: {path}"
        )
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
@pytest.mark.timeout(1800)
@pytest.mark.asyncio
async def test_integration_single_video_plays(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    import config
    url = integration_config["video_url_1"]
    encode_wait_s = integration_config["encode_wait_s"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Type URL into the TUI input field and submit
        await pilot.click("#url-input")
        await pilot.pause()
        integration_app.query_one("#url-input", Input).value = url
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(real_queue.items) == 1, "URL was not added to the queue"

        # Pipeline started — either downloading or (if cached) already past it
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in (
                "downloading", "encoding", "ready", "casting", "playing"
            ),
            timeout_s=60,
            description="pipeline started",
        )

        # downloading → encoding → ready (also accept casting/playing in case we miss the ready window)
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in ("ready", "casting", "playing"),
            timeout_s=encode_wait_s,
            description="status=ready (encode complete)",
        )

        # Chromecast receives the cast and starts playing
        await wait_for_condition(
            pilot,
            lambda: real_chromecast_per_test.player_state == "PLAYING",
            timeout_s=60,
            description="chromecast player_state=PLAYING",
        )

        # TUI assertions
        np = integration_app.query_one("#now-playing", NowPlayingWidget)
        assert np.title, "NowPlayingWidget.title should be non-empty while playing"
        playback_row = integration_app.query_one("#playback-row")
        assert playback_row.display is True, "#playback-row should be visible during playback"

        # Encoding quality assertions (ffprobe)
        item = real_queue.items[0]
        assert item.filename, "item.filename should be set after encode"
        encoded_path = f"{config.TEMP_DIR}/{item.filename}"
        info = await get_video_info(encoded_path)
        assert info.get("width") == 1024, f"Expected width=1024, got {info.get('width')}"
        assert info.get("height") == 576, f"Expected height=576, got {info.get('height')}"
        assert abs(info.get("fps", 0) - 25.0) < 0.5, (
            f"Expected fps≈25, got {info.get('fps')}"
        )
        assert info.get("codec") == "h264", f"Expected codec=h264, got {info.get('codec')}"


@pytest.mark.integration
@pytest.mark.timeout(1800)
@pytest.mark.asyncio
async def test_integration_playback_completes(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    url = integration_config["video_url_1"]
    playback_wait_s = integration_config["playback_wait_s"]
    encode_wait_s = integration_config["encode_wait_s"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Submit URL via TUI
        await pilot.click("#url-input")
        await pilot.pause()
        integration_app.query_one("#url-input", Input).value = url
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(real_queue.items) == 1

        # Pipeline started (handles cache hit: status may skip directly to ready)
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in (
                "downloading", "encoding", "ready", "casting", "playing"
            ),
            timeout_s=60,
            description="pipeline started",
        )

        # Encode complete
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in ("ready", "casting", "playing"),
            timeout_s=encode_wait_s,
            description="status=ready (encode complete)",
        )

        # Chromecast playing
        await wait_for_condition(
            pilot,
            lambda: real_chromecast_per_test.player_state == "PLAYING",
            timeout_s=60,
            description="player_state=PLAYING",
        )

        # Wait for full playback to complete
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "done",
            timeout_s=playback_wait_s,
            description="status=done (playback finished)",
        )

        # TUI assertions post-playback
        assert real_queue.items[0].status == "done"
        playback_row = integration_app.query_one("#playback-row")
        assert playback_row.display is False, (
            "#playback-row should be hidden after playback completes"
        )


@pytest.mark.integration
@pytest.mark.timeout(1800)
@pytest.mark.asyncio
async def test_integration_queue_transition(
    integration_config, integration_app, real_queue, real_chromecast_per_test
):
    url1 = integration_config["video_url_1"]
    url2 = integration_config["video_url_2"]
    if not url2:
        pytest.skip("TEST_VIDEO_URL_2 not set — queue transition test requires two videos")
    playback_wait_s = integration_config["playback_wait_s"]
    encode_wait_s = integration_config["encode_wait_s"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Insert both URLs in sequence
        await pilot.click("#url-input")
        await pilot.pause()
        integration_app.query_one("#url-input", Input).value = url1
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        integration_app.query_one("#url-input", Input).value = url2
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(real_queue.items) == 2, "Both URLs should be in the queue"

        # Wait for first video to reach playing status
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "playing",
            timeout_s=420,
            description="video1 status=playing",
        )

        title_1 = integration_app.query_one("#now-playing", NowPlayingWidget).title
        assert title_1, "NowPlayingWidget should show first video title"

        # Wait for first video to finish
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "done",
            timeout_s=playback_wait_s,
            description="video1 status=done",
        )

        # Second video must start automatically — may still be encoding when video1 ends
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[1].status == "playing",
            timeout_s=encode_wait_s,
            description="video2 status=playing (automatic transition)",
        )

        # TUI: NowPlayingWidget updated to second video
        await pilot.pause()  # let _refresh_all propagate after status change
        title_2 = integration_app.query_one("#now-playing", NowPlayingWidget).title
        assert title_2 != title_1, (
            f"NowPlayingWidget should show second video title, still showing: '{title_2}'"
        )
        assert real_queue.items[0].status == "done"
        assert real_queue.items[1].status == "playing"
