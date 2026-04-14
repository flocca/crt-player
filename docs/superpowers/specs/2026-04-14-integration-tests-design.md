# Integration Tests Design — crt-player

**Date:** 2026-04-14  
**Status:** Approved

## Problem

Existing tests (unit + TUI) mock all I/O. Real issues caused by actual download, encoding, Chromecast connectivity, or queue transitions are invisible to the test suite.

## Goal

Add opt-in integration tests that exercise the full stack: TUI input → download → encode → cast → playback completion → automatic queue transition.

## Scope

Three test cases covering single-video playback and two-video queue transition. Future cases (seek, pause/resume, volume, error recovery) are out of scope for this iteration.

---

## Configuration

Tests require environment variables. If absent, tests are **auto-skipped** (not failed). Variables are stored locally in `.env.integration` (gitignored).

| Variable | Required | Description |
|---|---|---|
| `TEST_CHROMECAST_NAME` | Yes | Exact Chromecast device name (e.g. `"Salotto"`) |
| `TEST_VIDEO_URL_1` | Yes | YouTube URL — short video ≤2 min recommended |
| `TEST_VIDEO_URL_2` | For transition test | YouTube URL — second video for queue test |
| `TEST_PLAYBACK_WAIT_S` | No | Override playback wait timeout in seconds |

---

## Architecture

### Marker

All integration tests carry `@pytest.mark.integration`. Run with:

```bash
pytest -m integration -v
```

Registered in `pytest.ini` (or `pyproject.toml`) to avoid unknown-marker warnings.

### Fixtures

Added to `tests/conftest.py` in a clearly delimited `# --- Integration fixtures ---` block.

| Fixture | Scope | Description |
|---|---|---|
| `integration_config` | session | Reads env vars; calls `pytest.skip()` if required vars missing |
| `real_tmp_dir` | session | Dedicated tmpdir for encoded files (separate from unit test tmp) |
| `real_chromecast` | session | Real `ChromecastManager` connected to `TEST_CHROMECAST_NAME` |
| `real_queue` | function | Fresh `QueueManager()` per test |
| `real_pipeline` | function | Real `PipelineWorker` wired to `real_chromecast` and `real_tmp_dir` |
| `integration_app` | function | `CRTCastApp(real_queue, real_pipeline, real_chromecast)` |

`real_chromecast` is session-scoped to avoid repeated slow Chromecast discovery. `real_queue`, `real_pipeline`, and `integration_app` are function-scoped: `PipelineWorker` holds an internal reference to its queue, so it must be recreated alongside a fresh queue for each test to ensure isolation.

### Helper: `wait_for_condition`

```python
async def wait_for_condition(pilot, fn, timeout_s=30, poll_interval=1.0, description="condition"):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await pilot.pause(poll_interval)
        if fn():
            return
    raise TimeoutError(f"Timeout after {timeout_s}s waiting for: {description}")
```

`pilot.pause(n)` yields to the Textual event loop for `n` seconds, keeping widgets, pipeline callbacks, and internal timers alive while waiting for external state changes.

---

## Test Cases

All tests: `@pytest.mark.integration`, `@pytest.mark.timeout(900)`.

### `test_integration_single_video_plays`

**Steps:**
1. Open app with `run_test()`
2. Insert `TEST_VIDEO_URL_1` into URL input, press Enter
3. `wait_for_condition`: `queue.items[0].status == "downloading"` (timeout 60s)
4. `wait_for_condition`: `queue.items[0].status == "ready"` — encode done (timeout 300s)
5. `wait_for_condition`: `chromecast.player_state == "PLAYING"` (timeout 60s)

**Assertions:**
- TUI: `NowPlayingWidget.title` matches video title; playback row visible
- Encoding: `{video_id}_pal_{scale_mode}.mp4` exists in TEMP_DIR; `ffprobe` confirms resolution 1024×576, fps 25, codec h264

### `test_integration_playback_completes`

Standalone test (repeats steps 1–5 autonomously, then continues through end of playback).

**Steps:**
1–5. Same as `test_integration_single_video_plays`
6. `wait_for_condition`: `queue.items[0].status == "done"` (timeout = `TEST_PLAYBACK_WAIT_S` or 300s default)

**Assertions:**
- TUI: `NowPlayingWidget` no longer shows the video; queue item in `"done"` state

### `test_integration_queue_transition`

Requires both `TEST_VIDEO_URL_1` and `TEST_VIDEO_URL_2`.

**Steps:**
1. Insert both URLs in sequence (Enter after each)
2. `wait_for_condition`: first video reaches `"playing"` status (timeout 420s)
3. `wait_for_condition`: first video reaches `"done"`, second reaches `"playing"` (timeout = video1 duration + 60s)

**Assertions:**
- TUI: `NowPlayingWidget.title` changes to second video title without manual intervention
- Queue state: `items[0].status == "done"`, `items[1].status == "playing"`

---

## Files Changed

| File | Change |
|---|---|
| `tests/conftest.py` | Add integration fixtures block |
| `tests/test_integration.py` | New file — 3 integration test cases |
| `pytest.ini` (or `pyproject.toml`) | Register `integration` marker |
| `.env.integration` | New gitignored file — local env vars template |
| `.gitignore` | Add `.env.integration` |

---

## Out of Scope (this iteration)

- Seek / pause / resume integration tests
- Volume control integration tests
- Error recovery (network drop, Chromecast disconnect)
- CI pipeline integration (these run locally only)
