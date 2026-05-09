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
from textual.widgets import Input, Static

from ui import CRTCastApp, NowPlayingWidget, QueueListView


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
    fail_fn=None,
    fail_msg_fn=None,
) -> None:
    """Yield to the Textual event loop until fn() is True or timeout expires.

    pilot.pause(n) cedes control to Textual's asyncio loop for n seconds,
    keeping pipeline tasks and callbacks alive while we wait for external state.

    fail_fn: if provided, called each poll; raises AssertionError immediately
             (don't wait for timeout) with fail_msg_fn() as the message.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        await pilot.pause(poll_interval)
        if fn():
            return
        if fail_fn and fail_fn():
            msg = fail_msg_fn() if fail_msg_fn else "early-fail condition met"
            raise AssertionError(
                f"Giving up waiting for '{description}': {msg}"
            )
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
    import crt.config as config
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
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item.error={real_queue.items[0].error!r}",
        )

        # downloading → encoding → ready (also accept casting/playing in case we miss the ready window)
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in ("ready", "casting", "playing"),
            timeout_s=encode_wait_s,
            description="status=ready (encode complete)",
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item.error={real_queue.items[0].error!r}",
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

        # Pipeline started — fetch_title() always runs first (before the encode cache
        # check), so even a cache hit requires a YouTube network round-trip. Use
        # encode_wait_s here: if YouTube rate-limits us back-to-back we need the
        # same headroom as a full encode would take.
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in (
                "downloading", "encoding", "ready", "casting", "playing"
            ),
            timeout_s=encode_wait_s,
            description="pipeline started",
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item.error={real_queue.items[0].error!r}",
        )

        # Encode complete
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status in ("ready", "casting", "playing"),
            timeout_s=encode_wait_s,
            description="status=ready (encode complete)",
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item.error={real_queue.items[0].error!r}",
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
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"video1 item.error={real_queue.items[0].error!r}",
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
            fail_fn=lambda: real_queue.items[1].status == "error",
            fail_msg_fn=lambda: f"video2 item.error={real_queue.items[1].error!r}",
        )

        # TUI: NowPlayingWidget updated to second video
        await pilot.pause()  # let _refresh_all propagate after status change
        title_2 = integration_app.query_one("#now-playing", NowPlayingWidget).title
        assert title_2 != title_1, (
            f"NowPlayingWidget should show second video title, still showing: '{title_2}'"
        )
        assert real_queue.items[0].status == "done"
        assert real_queue.items[1].status == "playing"


@pytest.mark.integration
@pytest.mark.timeout(1800)
@pytest.mark.asyncio
async def test_integration_manual_playback_switching(
    integration_config, integration_app, real_queue, real_pipeline, real_chromecast_per_test
):
    """Test manual video selection: add two videos, wait for both to encode with
    auto-cast suppressed, then manually switch between them and verify resume positions.

    Verifies that:
    - Pressing Enter on a queue item starts playback of that item
    - NowPlaying widget shows correct title and updating progress bar
    - Switching back to a previously played item resumes from the saved position
    """
    url1 = integration_config["video_url_1"]
    url2 = integration_config["video_url_2"]
    if not url2:
        pytest.skip("TEST_VIDEO_URL_2 not set — manual switching test requires two videos")
    encode_wait_s = integration_config["encode_wait_s"]
    MIN_PLAY_S = 5.0   # seconds to accumulate before switching
    RESUME_TOL_S = 5.0  # tolerance for resume position check

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # --- Phase 1: add both videos via URL input ---
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

        # --- Phase 2: wait for both items to be "ready" while suppressing auto-cast ---
        # The cast loop only runs at await points. Setting _cast_enabled=False before
        # each yield ensures the cast loop sees False and skips. Manual selection via
        # Enter calls pipeline.wake() which re-enables cast.
        # If files are already cached from a previous run, both items become "ready"
        # in seconds (only fetch_title() network call needed).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + encode_wait_s
        while loop.time() < deadline:
            real_pipeline._cast_enabled = False
            await pilot.pause(1.0)
            if any(i.status == "error" for i in real_queue.items):
                errors = [(i.title or i.url, i.error) for i in real_queue.items if i.status == "error"]
                raise AssertionError(f"Item encode failed: {errors}")
            if all(i.status in ("ready", "done") for i in real_queue.items):
                break
        else:
            statuses = [i.status for i in real_queue.items]
            raise TimeoutError(
                f"Timed out after {encode_wait_s}s waiting for both items to be ready; "
                f"statuses={statuses}"
            )

        np = integration_app.query_one("#now-playing", NowPlayingWidget)
        np_progress = integration_app.query_one("#np-progress", Static)
        queue_list = integration_app.query_one("#queue-list", QueueListView)

        # --- Phase 3: manually select item1 (cursor is at index 0) ---
        # Pressing Enter calls pipeline.wake(), re-enabling cast.
        queue_list.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "playing",
            timeout_s=60,
            description="item1 status=playing after manual selection",
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item1 error: {real_queue.items[0].error!r}",
        )

        # --- Phase 4: accumulate position, verify NowPlaying for item1 ---
        await wait_for_condition(
            pilot,
            lambda: (
                real_queue.items[0].status == "playing"
                and real_queue.items[0].playback_position >= MIN_PLAY_S
            ),
            timeout_s=MIN_PLAY_S + 30,
            poll_interval=1.0,
            description=f"item1 playing with position >= {MIN_PLAY_S}s",
            fail_fn=lambda: real_queue.items[0].status not in ("playing", "casting"),
            fail_msg_fn=lambda: (
                f"item1 left playing state: status={real_queue.items[0].status!r}"
            ),
        )

        assert np.title == real_queue.items[0].title, (
            f"NowPlaying should show item1 title, got: {np.title!r}"
        )
        assert "▶" in str(np_progress.content) or "⏸" in str(np_progress.content), (
            f"Expected play indicator in progress bar: {np_progress.content!r}"
        )
        assert "/" in str(np_progress.content), (
            f"Expected time separator in progress bar: {np_progress.content!r}"
        )

        # Verify progress bar updates over time
        progress_before = np_progress.content
        await pilot.pause(3.0)
        assert np_progress.content != progress_before, (
            "Progress bar should change over time while item1 is playing"
        )

        item1_saved_position = real_queue.items[0].playback_position

        # --- Phase 5: manually select item2 ---
        # Cursor is at index 0 (item1); one "down" moves to index 1 (item2).
        queue_list.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await wait_for_condition(
            pilot,
            lambda: real_queue.items[1].status == "playing",
            timeout_s=60,
            description="item2 status=playing after manual selection",
            fail_fn=lambda: real_queue.items[1].status == "error",
            fail_msg_fn=lambda: f"item2 error: {real_queue.items[1].error!r}",
        )

        # --- Phase 6: accumulate position, verify NowPlaying for item2 ---
        # Let several seconds of playback accumulate. This also catches the
        # regression where a stale IDLE event from item1's transition falsely
        # ends item2's playback a few hundred ms after it starts.
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[1].playback_position >= MIN_PLAY_S,
            timeout_s=MIN_PLAY_S + 30,
            poll_interval=1.0,
            description=f"item2 playing with position >= {MIN_PLAY_S}s",
            fail_fn=lambda: real_queue.items[1].status not in ("playing", "casting"),
            fail_msg_fn=lambda: (
                f"item2 left playing state: status={real_queue.items[1].status!r}"
            ),
        )
        # Extra settle time so any late spurious status updates can surface.
        await pilot.pause(3.0)
        assert real_queue.items[1].status == "playing", (
            f"item2 should still be playing after settle, got {real_queue.items[1].status!r}"
        )

        assert np.title == real_queue.items[1].title, (
            f"NowPlaying should show item2 title, got: {np.title!r}"
        )
        assert "▶" in str(np_progress.content) or "⏸" in str(np_progress.content), (
            f"Expected play indicator in item2 progress bar: {np_progress.content!r}"
        )
        assert "/" in str(np_progress.content), (
            f"Expected time separator in item2 progress bar: {np_progress.content!r}"
        )

        progress_before = np_progress.content
        await pilot.pause(3.0)
        assert np_progress.content != progress_before, (
            "Progress bar should change over time while item2 is playing"
        )

        # --- Phase 7: switch back to item1 ---
        # Cursor is at index 1 (item2); one "up" returns to index 0 (item1).
        queue_list.focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "playing",
            timeout_s=60,
            description="item1 status=playing again after resume",
            fail_fn=lambda: real_queue.items[0].status == "error",
            fail_msg_fn=lambda: f"item1 error on resume: {real_queue.items[0].error!r}",
        )

        # --- Phase 8: verify resume position and NowPlaying ---
        # Wait several seconds so poll_status can update playback_position and
        # any late spurious status updates surface (would push status away from playing).
        await pilot.pause(5.0)
        assert real_queue.items[0].status == "playing", (
            f"item1 should still be playing after settle, got {real_queue.items[0].status!r}"
        )
        assert real_queue.items[0].playback_position >= item1_saved_position - RESUME_TOL_S, (
            f"item1 should resume near {item1_saved_position:.1f}s, "
            f"got {real_queue.items[0].playback_position:.1f}s"
        )
        assert np.title == real_queue.items[0].title, (
            f"NowPlaying should show item1 title after resume, got: {np.title!r}"
        )
        assert "▶" in str(np_progress.content) or "⏸" in str(np_progress.content), (
            f"Expected play indicator in item1 resume progress bar: {np_progress.content!r}"
        )
        assert "/" in str(np_progress.content), (
            f"Expected time separator in item1 resume progress bar: {np_progress.content!r}"
        )

        progress_before = np_progress.content
        await pilot.pause(3.0)
        assert np_progress.content != progress_before, (
            "Progress bar should continue updating after item1 resumes"
        )
