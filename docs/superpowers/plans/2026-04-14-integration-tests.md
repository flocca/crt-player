# Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in integration tests that exercise the full stack (TUI input → download → encode → cast → queue transition) against a real Chromecast and real YouTube videos.

**Architecture:** Three test functions in `tests/test_integration.py`, all marked `@pytest.mark.integration`. Session-scoped fixtures handle slow one-time setup (Chromecast discovery, media server); function-scoped fixtures provide test isolation. A `wait_for_condition` async helper polls state changes by yielding to the Textual event loop via `pilot.pause()`.

**Tech Stack:** pytest, pytest-asyncio, pytest-timeout, Textual run_test/Pilot, pychromecast, ffprobe (subprocess)

---

## File Map

| File | Change |
|---|---|
| `pytest.ini` | Create — register `integration` marker |
| `requirements.txt` | Add `pytest-timeout` |
| `.gitignore` | Add `.env.integration` |
| `.env.integration` | Create — gitignored env vars template (not committed) |
| `tests/conftest.py` | Append integration fixtures block |
| `tests/test_integration.py` | Create — 3 test cases + helpers |

---

### Task 1: Pytest infrastructure

**Files:**
- Create: `pytest.ini`
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `.env.integration`

- [ ] **Step 1: Create pytest.ini**

```ini
[pytest]
markers =
    integration: end-to-end tests requiring a real Chromecast and internet access
```

- [ ] **Step 2: Add pytest-timeout to requirements.txt**

Current `requirements.txt`:
```
textual
fastapi
uvicorn
yt-dlp
pychromecast
pytest
httpx
pytest-asyncio
```

New `requirements.txt` (append `pytest-timeout`):
```
textual
fastapi
uvicorn
yt-dlp
pychromecast
pytest
httpx
pytest-asyncio
pytest-timeout
```

- [ ] **Step 3: Install pytest-timeout**

```bash
source .venv/bin/activate && pip install pytest-timeout
```

Expected: `Successfully installed pytest-timeout-...`

- [ ] **Step 4: Add .env.integration to .gitignore**

Current `.gitignore` ends at line 9 (`state.json`). Append:
```
.env.integration
```

- [ ] **Step 5: Create .env.integration template**

Create file `.env.integration` at repo root (this file is gitignored — each developer fills in their values):

```bash
# Integration test environment — source this before running pytest -m integration
#
# Usage:
#   source .env.integration
#   pytest -m integration -v -s

export TEST_CHROMECAST_NAME="Nome del tuo Chromecast"
export TEST_VIDEO_URL_1="https://www.youtube.com/watch?v=XXXXXXXXX"  # short, ≤2 min recommended
export TEST_VIDEO_URL_2="https://www.youtube.com/watch?v=XXXXXXXXX"  # needed for queue transition test
export TEST_PLAYBACK_WAIT_S=300  # seconds to wait for playback to complete
```

- [ ] **Step 6: Verify existing tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all green. The new `pytest.ini` must not break any existing test.

- [ ] **Step 7: Commit**

```bash
git add pytest.ini requirements.txt .gitignore .env.integration
git commit -m "test: add integration marker and pytest-timeout dependency"
```

---

### Task 2: Integration fixtures in conftest.py

**Files:**
- Modify: `tests/conftest.py`

Background: `ChromecastManager._discover_sync()` reads `config.CHROMECAST_NAME`. `PipelineWorker._prepare_one()` reads `config.TEMP_DIR` and `config.SCALE_MODE`. Both are module-level attributes on the `config` singleton — assigning to them in a fixture affects all subsequent uses in the same process. The media server must run as a daemon thread serving from `config.TEMP_DIR` so `cast_url` can reach the encoded files.

The `real_chromecast` fixture calls `_discover_sync()` directly (sync) to avoid asyncio event loop scope issues with session-scoped async fixtures. `asyncio.Event.set()` called outside an asyncio loop is safe in Python 3.10+ when no coroutine is yet waiting on the event.

- [ ] **Step 1: Append integration fixtures to tests/conftest.py**

Open `tests/conftest.py`. After the closing line of the existing `app` fixture (currently line 54: `return CRTCastApp(queue, mock_pipeline, mock_chromecast)`), append the following block:

```python


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
    }


@pytest.fixture(scope="session")
def real_tmp_dir(integration_config, tmp_path_factory):
    """Dedicated temp dir for encoded files; starts the media server once for the session."""
    import config as cfg
    from media_server import create_media_app

    d = tmp_path_factory.mktemp("integration_media")
    cfg.TEMP_DIR = str(d)
    cfg.STATE_FILE = str(d / "test_state.json")  # avoid polluting the real state file

    app = create_media_app(str(d))
    server_cfg = uvicorn.Config(
        app, host="0.0.0.0", port=cfg.SERVER_PORT, log_level="warning"
    )
    server = uvicorn.Server(server_cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.5)  # give uvicorn time to bind the port
    yield str(d)


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
def real_queue():
    """Fresh QueueManager per test — no saved state loaded."""
    from queue_manager import QueueManager
    return QueueManager()


@pytest.fixture
def real_pipeline(real_queue, real_chromecast):
    """Fresh PipelineWorker per test.

    Must be function-scoped because PipelineWorker holds an internal reference
    to its queue; creating it fresh alongside real_queue ensures the pipeline
    sees the same queue the test does.
    """
    from pipeline import PipelineWorker
    return PipelineWorker(real_queue, real_chromecast)


@pytest.fixture
def integration_app(real_queue, real_pipeline, real_chromecast):
    """Full CRTCastApp wired with real dependencies, fresh per test."""
    from ui import CRTCastApp
    return CRTCastApp(real_queue, real_pipeline, real_chromecast)
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all green. The appended block adds new imports inside fixture bodies — no top-level side effects.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add integration fixtures to conftest"
```

---

### Task 3: test_integration.py skeleton + helpers

**Files:**
- Create: `tests/test_integration.py`

The `wait_for_condition` helper calls `pilot.pause(poll_interval)` in a loop. `pilot.pause(n)` yields to the Textual asyncio event loop for `n` seconds, which lets the pipeline worker tasks (`run_prepare`, `run_cast`) and the status callbacks run. This is how we wait for real I/O (download, encode, cast) without blocking the loop.

`get_video_info` runs `ffprobe` as a subprocess and parses JSON output. It's used by `test_integration_single_video_plays` to assert the encoded file has the correct dimensions (1024×576), frame rate (25fps), and codec (h264).

- [ ] **Step 1: Create tests/test_integration.py**

```python
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
    integration_config, integration_app, real_queue, real_chromecast
):
    pass  # implemented in Task 4


@pytest.mark.integration
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_integration_playback_completes(
    integration_config, integration_app, real_queue, real_chromecast
):
    pass  # implemented in Task 5


@pytest.mark.integration
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_integration_queue_transition(
    integration_config, integration_app, real_queue, real_chromecast
):
    pass  # implemented in Task 6
```

- [ ] **Step 2: Verify the skeleton collects cleanly**

```bash
source .venv/bin/activate && python -m pytest tests/test_integration.py --collect-only
```

Expected output contains `3 tests` and no import errors. Tests will show as skipped (no env vars set in this shell).

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test skeleton with wait_for_condition and get_video_info helpers"
```

---

### Task 4: test_integration_single_video_plays

**Files:**
- Modify: `tests/test_integration.py`

This test verifies that inserting a URL through the TUI starts the full pipeline and the Chromecast enters PLAYING state, and that the encoded file meets the expected spec (1024×576, 25fps, h264).

- [ ] **Step 1: Replace the `pass` stub in test_integration_single_video_plays**

Replace the line `pass  # implemented in Task 4` with:

```python
    import config
    url = integration_config["video_url_1"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Type URL into the TUI input field and submit
        await pilot.click("#url-input")
        await pilot.pause()
        integration_app.query_one("#url-input", Input).value = url
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(real_queue.items) == 1, "URL was not added to the queue"

        # Pipeline transitions: queued → downloading
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "downloading",
            timeout_s=60,
            description="status=downloading",
        )

        # downloading → encoding → ready
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "ready",
            timeout_s=300,
            description="status=ready (encode complete)",
        )

        # Chromecast receives the cast and starts playing
        await wait_for_condition(
            pilot,
            lambda: real_chromecast.player_state == "PLAYING",
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
```

- [ ] **Step 2: Run the test with real env vars**

```bash
source .env.integration
source .venv/bin/activate && python -m pytest tests/test_integration.py::test_integration_single_video_plays -v -s
```

Expected: `PASSED` (takes a few minutes depending on video length and network speed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: implement test_integration_single_video_plays"
```

---

### Task 5: test_integration_playback_completes

**Files:**
- Modify: `tests/test_integration.py`

This is a standalone test (independent of Task 4 — it restarts from a fresh app/queue). It extends the single-video flow through complete playback: the queue item must reach `status == "done"` and the TUI must hide the playback row.

- [ ] **Step 1: Replace the `pass` stub in test_integration_playback_completes**

Replace the line `pass  # implemented in Task 5` with:

```python
    url = integration_config["video_url_1"]
    playback_wait_s = integration_config["playback_wait_s"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Steps 1–5: same as test_integration_single_video_plays
        await pilot.click("#url-input")
        await pilot.pause()
        integration_app.query_one("#url-input", Input).value = url
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert len(real_queue.items) == 1

        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "downloading",
            timeout_s=60,
            description="status=downloading",
        )
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[0].status == "ready",
            timeout_s=300,
            description="status=ready",
        )
        await wait_for_condition(
            pilot,
            lambda: real_chromecast.player_state == "PLAYING",
            timeout_s=60,
            description="player_state=PLAYING",
        )

        # Step 6: wait for full playback to complete
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
```

- [ ] **Step 2: Run the test**

```bash
source .env.integration
source .venv/bin/activate && python -m pytest tests/test_integration.py::test_integration_playback_completes -v -s
```

Expected: `PASSED` (takes download + encode + full video duration).

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: implement test_integration_playback_completes"
```

---

### Task 6: test_integration_queue_transition

**Files:**
- Modify: `tests/test_integration.py`

This is the critical test: two URLs are inserted, the first plays to completion, and the second must start automatically without any user interaction. The TUI's `NowPlayingWidget` must update to the second video's title.

This test requires `TEST_VIDEO_URL_2`; it skips itself (not the whole session) if that env var is absent.

- [ ] **Step 1: Replace the `pass` stub in test_integration_queue_transition**

Replace the line `pass  # implemented in Task 6` with:

```python
    url1 = integration_config["video_url_1"]
    url2 = integration_config["video_url_2"]
    if not url2:
        pytest.skip("TEST_VIDEO_URL_2 not set — queue transition test requires two videos")
    playback_wait_s = integration_config["playback_wait_s"]

    async with integration_app.run_test(size=(120, 40)) as pilot:
        # Insert both URLs in sequence via the TUI input
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
        # (download + encode for both videos may run in parallel via the prepare loop)
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

        # Second video must start automatically (no user action)
        await wait_for_condition(
            pilot,
            lambda: real_queue.items[1].status == "playing",
            timeout_s=60,
            description="video2 status=playing (automatic transition)",
        )

        # TUI: NowPlayingWidget updated to second video
        title_2 = integration_app.query_one("#now-playing", NowPlayingWidget).title
        assert title_2 != title_1, (
            f"NowPlayingWidget should show second video title, still showing: '{title_2}'"
        )
        assert real_queue.items[0].status == "done"
        assert real_queue.items[1].status == "playing"
```

- [ ] **Step 2: Run the test**

```bash
source .env.integration
source .venv/bin/activate && python -m pytest tests/test_integration.py::test_integration_queue_transition -v -s
```

Expected: `PASSED` (takes download/encode time for both videos + full duration of video1).

- [ ] **Step 3: Run all integration tests together**

```bash
source .env.integration
source .venv/bin/activate && python -m pytest -m integration -v -s
```

Expected: all 3 tests `PASSED`. Note that `test_integration_playback_completes` benefits from the encode cache: if `test_integration_single_video_plays` already ran in the same session, the encoded file exists and the pipeline skips re-encoding.

- [ ] **Step 4: Verify existing unit tests still pass**

```bash
source .venv/bin/activate && python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: implement test_integration_queue_transition"
```
