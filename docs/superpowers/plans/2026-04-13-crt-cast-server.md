# CRT Cast Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TUI-based server that downloads YouTube videos, converts them to 4:3 PAL format, and casts them to a Chromecast connected to a CRT TV.

**Architecture:** Textual TUI drives the user interaction, communicating in-process with a queue manager, async pipeline worker, and pychromecast wrapper. A minimal FastAPI server runs in a background thread solely to serve encoded MP4 files to the Chromecast.

**Tech Stack:** Python 3.14, Textual, FastAPI/uvicorn, yt-dlp (Python API), ffmpeg (subprocess), pychromecast

---

## File Structure

| File | Responsibility |
|------|----------------|
| `config.py` | Global constants (Chromecast name, paths, port) |
| `queue_manager.py` | Queue item dataclass, CRUD operations, ordering |
| `media_server.py` | FastAPI app with single `/media/{filename}` endpoint |
| `chromecast_mgr.py` | pychromecast wrapper: discovery, cast, controls, status listener |
| `pipeline.py` | Async worker: download → encode → cast, progress callbacks |
| `ui.py` | Textual app: layout, widgets, keybindings |
| `main.py` | Entry point: wires all modules, starts TUI + media server |
| `requirements.txt` | Python dependencies |
| `tests/test_queue_manager.py` | Unit tests for queue manager |
| `tests/test_media_server.py` | Tests for media server endpoint |
| `tests/test_pipeline.py` | Tests for download/encode functions (mocked externals) |

---

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
textual
fastapi
uvicorn
yt-dlp
pychromecast
pytest
httpx
```

- [ ] **Step 2: Create config.py**

```python
import os

CHROMECAST_NAME = os.environ.get("CRT_CHROMECAST_NAME", "Living Room TV")
MAX_VIDEO_HEIGHT = int(os.environ.get("CRT_MAX_VIDEO_HEIGHT", "576"))
TEMP_DIR = os.environ.get("CRT_TEMP_DIR", "/tmp/crt_cast")
FILE_TTL_HOURS = int(os.environ.get("CRT_FILE_TTL_HOURS", "24"))
SERVER_PORT = int(os.environ.get("CRT_SERVER_PORT", "8765"))
```

- [ ] **Step 3: Create virtual environment and install dependencies**

Run:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 4: Create tests directory**

```bash
mkdir tests
touch tests/__init__.py
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.py tests/__init__.py
git commit -m "feat: project setup with config and dependencies"
```

---

### Task 2: Queue Manager

**Files:**
- Create: `queue_manager.py`
- Create: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests for QueueItem and QueueManager basics**

Create `tests/test_queue_manager.py`:

```python
import pytest
from queue_manager import QueueItem, QueueManager


def test_queue_item_creation():
    item = QueueItem(url="https://youtube.com/watch?v=abc123")
    assert item.url == "https://youtube.com/watch?v=abc123"
    assert item.status == "queued"
    assert item.progress == 0.0
    assert item.title == ""
    assert item.error is None
    assert item.filename is None
    assert len(item.id) == 36  # uuid4


def test_add_item_queue_mode():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert len(qm.items) == 1
    assert qm.items[0].url == "https://youtube.com/watch?v=1"


def test_add_item_queue_mode_appends_to_end():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    qm.add("https://youtube.com/watch?v=3", mode="queue")
    assert qm.items[0].url == "https://youtube.com/watch?v=1"
    assert qm.items[2].url == "https://youtube.com/watch?v=3"


def test_add_item_next_mode_inserts_after_active():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    qm.add("https://youtube.com/watch?v=next", mode="next")
    assert qm.items[1].url == "https://youtube.com/watch?v=next"
    assert qm.items[2].url == "https://youtube.com/watch?v=2"


def test_add_item_next_mode_no_active_inserts_at_start():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=next", mode="next")
    assert qm.items[0].url == "https://youtube.com/watch?v=next"


def test_add_item_now_mode_inserts_at_start():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    item_now = qm.add("https://youtube.com/watch?v=now", mode="now")
    assert qm.items[0].url == "https://youtube.com/watch?v=now"


def test_remove_queued_item():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.remove(item.id) is True
    assert len(qm.items) == 0


def test_remove_non_queued_item_fails():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item.status = "downloading"
    assert qm.remove(item.id) is False
    assert len(qm.items) == 1


def test_remove_nonexistent_item_fails():
    qm = QueueManager()
    assert qm.remove("nonexistent-id") is False


def test_move_up():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1", mode="queue")
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item2.id, "up") is True
    assert qm.items[0].url == "https://youtube.com/watch?v=2"


def test_move_down():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_move_up_at_top_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.move(item1.id, "up") is False


def test_move_down_at_bottom_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.move(item1.id, "down") is False


def test_move_non_queued_item_fails():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "downloading"
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.move(item1.id, "down") is False


def test_next_pending_returns_first_queued():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2", mode="queue")
    assert qm.next_pending() is item2


def test_next_pending_returns_none_when_empty():
    qm = QueueManager()
    assert qm.next_pending() is None


def test_active_item():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1", mode="queue")
    assert qm.active_item() is None
    item1.status = "downloading"
    assert qm.active_item() is item1
    item1.status = "playing"
    assert qm.active_item() is item1
    item1.status = "done"
    assert qm.active_item() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_queue_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'queue_manager'`

- [ ] **Step 3: Implement QueueItem and QueueManager**

Create `queue_manager.py`:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


ACTIVE_STATUSES = {"downloading", "encoding", "casting", "playing"}


@dataclass
class QueueItem:
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None


class QueueManager:
    def __init__(self) -> None:
        self.items: list[QueueItem] = []

    def add(self, url: str, mode: str = "queue") -> QueueItem:
        item = QueueItem(url=url)
        if mode == "queue":
            self.items.append(item)
        elif mode == "next":
            insert_idx = self._after_active_index()
            self.items.insert(insert_idx, item)
        elif mode == "now":
            self.items.insert(0, item)
        return item

    def remove(self, item_id: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                if item.status != "queued":
                    return False
                self.items.pop(i)
                return True
        return False

    def move(self, item_id: str, direction: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                if item.status != "queued":
                    return False
                if direction == "up" and i > 0 and self.items[i - 1].status == "queued":
                    self.items[i], self.items[i - 1] = self.items[i - 1], self.items[i]
                    return True
                if direction == "down" and i < len(self.items) - 1 and self.items[i + 1].status == "queued":
                    self.items[i], self.items[i + 1] = self.items[i + 1], self.items[i]
                    return True
                return False
        return False

    def next_pending(self) -> QueueItem | None:
        for item in self.items:
            if item.status == "queued":
                return item
        return None

    def active_item(self) -> QueueItem | None:
        for item in self.items:
            if item.status in ACTIVE_STATUSES:
                return item
        return None

    def _after_active_index(self) -> int:
        for i, item in enumerate(self.items):
            if item.status in ACTIVE_STATUSES:
                return i + 1
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_queue_manager.py -v`
Expected: All 17 tests PASS

- [ ] **Step 5: Commit**

```bash
git add queue_manager.py tests/test_queue_manager.py
git commit -m "feat: queue manager with item CRUD, ordering, and mode insertion"
```

---

### Task 3: Media Server

**Files:**
- Create: `media_server.py`
- Create: `tests/test_media_server.py`

- [ ] **Step 1: Write failing tests for media server**

Create `tests/test_media_server.py`:

```python
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from media_server import create_media_app


@pytest.fixture
def media_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def app(media_dir):
    return create_media_app(media_dir)


@pytest.mark.asyncio
async def test_serve_existing_file(app, media_dir):
    filepath = os.path.join(media_dir, "test_video.mp4")
    with open(filepath, "wb") as f:
        f.write(b"\x00" * 1024)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/test_video.mp4")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert len(resp.content) == 1024


@pytest.mark.asyncio
async def test_serve_nonexistent_file(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/nope.mp4")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_path_traversal_blocked(app, media_dir):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/../../../etc/passwd")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_media_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'media_server'`

- [ ] **Step 3: Implement media server**

Create `media_server.py`:

```python
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response


def create_media_app(media_dir: str) -> FastAPI:
    app = FastAPI()

    @app.get("/media/{filename}")
    async def serve_media(filename: str) -> Response:
        if "/" in filename or "\\" in filename or ".." in filename:
            return Response(status_code=404)
        filepath = os.path.join(media_dir, filename)
        if not os.path.isfile(filepath):
            return Response(status_code=404)
        return FileResponse(filepath, media_type="video/mp4")

    return app
```

- [ ] **Step 4: Add pytest-asyncio to requirements.txt**

Add `pytest-asyncio` to `requirements.txt` after the `httpx` line:

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

Run: `pip install pytest-asyncio`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_media_server.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add media_server.py tests/test_media_server.py requirements.txt
git commit -m "feat: media server serving MP4 files from temp dir"
```

---

### Task 4: Chromecast Manager

**Files:**
- Create: `chromecast_mgr.py`

- [ ] **Step 1: Implement ChromecastManager**

Create `chromecast_mgr.py`:

```python
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

import pychromecast
from pychromecast.controllers.media import MediaStatusListener

import config

log = logging.getLogger(__name__)


class StatusListener(MediaStatusListener):
    def __init__(self, callback: Callable) -> None:
        self._callback = callback

    def new_media_status(self, status) -> None:
        self._callback(status)

    def load_media_failed(self, item, error_code) -> None:
        log.error("Load media failed: item=%s error=%s", item, error_code)


class ChromecastManager:
    def __init__(self) -> None:
        self.cast: pychromecast.Chromecast | None = None
        self.browser: pychromecast.CastBrowser | None = None
        self.connected: bool = False
        self.device_name: str = ""
        self.player_state: str = "UNKNOWN"
        self.current_time: float = 0.0
        self.duration: float = 0.0
        self.volume: float = 1.0
        self._on_status_change: Callable | None = None
        self._on_connection_change: Callable | None = None
        self._previous_state: str = "UNKNOWN"

    def set_status_callback(self, callback: Callable) -> None:
        self._on_status_change = callback

    def set_connection_callback(self, callback: Callable) -> None:
        self._on_connection_change = callback

    async def discover(self) -> bool:
        return await asyncio.to_thread(self._discover_sync)

    def _discover_sync(self) -> bool:
        chromecasts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[config.CHROMECAST_NAME]
        )
        self.browser = browser
        if not chromecasts:
            self.connected = False
            self._notify_connection()
            return False
        self.cast = chromecasts[0]
        self.cast.wait()
        self.device_name = self.cast.name
        self.connected = True
        listener = StatusListener(self._on_media_status)
        self.cast.media_controller.register_status_listener(listener)
        self._notify_connection()
        return True

    async def discover_loop(self) -> None:
        while not self.connected:
            log.info("Searching for Chromecast '%s'...", config.CHROMECAST_NAME)
            found = await self.discover()
            if not found:
                await asyncio.sleep(10)

    def _on_media_status(self, status) -> None:
        self._previous_state = self.player_state
        self.player_state = status.player_state or "UNKNOWN"
        self.current_time = status.current_time or 0.0
        self.duration = status.duration or 0.0
        if status.volume_level is not None:
            self.volume = status.volume_level
        if self._on_status_change:
            self._on_status_change()

    def _notify_connection(self) -> None:
        if self._on_connection_change:
            self._on_connection_change()

    @property
    def playback_ended(self) -> bool:
        return (
            self.player_state == "IDLE"
            and self._previous_state in ("PLAYING", "BUFFERING")
        )

    def cast_url(self, url: str) -> None:
        if not self.cast:
            raise RuntimeError("Chromecast not connected")
        mc = self.cast.media_controller
        mc.play_media(url, "video/mp4")
        mc.block_until_active()

    def stop(self) -> None:
        if self.cast:
            self.cast.media_controller.stop()

    def pause(self) -> None:
        if self.cast:
            self.cast.media_controller.pause()

    def resume(self) -> None:
        if self.cast:
            self.cast.media_controller.play()

    def pause_or_resume(self) -> None:
        if self.player_state == "PAUSED":
            self.resume()
        elif self.player_state == "PLAYING":
            self.pause()

    def adjust_volume(self, delta: int) -> None:
        if not self.cast:
            return
        new_vol = max(0.0, min(1.0, self.volume + delta / 100.0))
        self.cast.set_volume(new_vol)
        self.volume = new_vol

    def shutdown(self) -> None:
        if self.browser:
            self.browser.stop_discovery()
```

- [ ] **Step 2: Verify it imports without errors**

Run: `python -c "import chromecast_mgr; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add chromecast_mgr.py
git commit -m "feat: chromecast manager with discovery, cast, controls, status listener"
```

---

### Task 5: Pipeline Worker

**Files:**
- Create: `pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests for download and encode helpers**

Create `tests/test_pipeline.py`:

```python
import asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from pipeline import fetch_title, download_video, encode_video
from queue_manager import QueueItem


@pytest.mark.asyncio
async def test_fetch_title():
    mock_info = {"title": "Test Video Title"}
    with patch("pipeline.yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        title = await fetch_title("https://youtube.com/watch?v=abc")
    assert title == "Test Video Title"


@pytest.mark.asyncio
async def test_download_video():
    progress_values = []

    def on_progress(pct):
        progress_values.append(pct)

    def fake_download(urls):
        # Simulate yt-dlp calling the progress hook
        hook = MockYDL_instance.params["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
        hook({"status": "finished"})

    with patch("pipeline.yt_dlp.YoutubeDL") as MockYDL:
        MockYDL_instance = MagicMock()
        MockYDL.return_value.__enter__.return_value = MockYDL_instance
        MockYDL_instance.params = {}

        # Capture the progress hook from the opts
        original_init = MockYDL.call_args
        def capture_init(*args, **kwargs):
            opts = args[0] if args else kwargs.get("params", {})
            MockYDL_instance.params = opts
            return MockYDL.return_value
        MockYDL.side_effect = capture_init

        MockYDL_instance.download.side_effect = fake_download

        filepath = await download_video(
            "https://youtube.com/watch?v=abc", "/tmp/test", on_progress
        )

    assert len(progress_values) >= 1


@pytest.mark.asyncio
async def test_encode_video(tmp_path):
    # Create a minimal input file
    input_file = str(tmp_path / "input.mp4")
    output_file = str(tmp_path / "output.mp4")
    with open(input_file, "wb") as f:
        f.write(b"\x00" * 100)

    progress_values = []

    def on_progress(pct):
        progress_values.append(pct)

    # Mock subprocess to simulate ffmpeg progress output
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.pid = 12345

    progress_output = b"out_time_us=5000000\nprogress=continue\nout_time_us=10000000\nprogress=end\n"

    async def mock_readline():
        if not hasattr(mock_readline, "_lines"):
            mock_readline._lines = iter(progress_output.split(b"\n"))
        try:
            line = next(mock_readline._lines)
            return line + b"\n"
        except StopIteration:
            return b""

    mock_process.stdout.readline = mock_readline
    mock_process.wait = AsyncMock(return_value=0)

    with patch("pipeline.asyncio.create_subprocess_exec", return_value=mock_process):
        result = await encode_video(input_file, output_file, 10.0, on_progress)

    assert result == output_file
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Implement pipeline module**

Create `pipeline.py`:

```python
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Callable

import yt_dlp

import config
from chromecast_mgr import ChromecastManager
from queue_manager import QueueItem, QueueManager

log = logging.getLogger(__name__)


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def fetch_title(url: str) -> str:
    def _extract():
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "Unknown")

    return await asyncio.to_thread(_extract)


async def download_video(
    url: str, temp_dir: str, on_progress: Callable[[float], None]
) -> str:
    result_path: str = ""

    def _download():
        nonlocal result_path

        def progress_hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total > 0:
                    pct = d.get("downloaded_bytes", 0) / total * 100
                    on_progress(pct)
            elif d["status"] == "finished":
                on_progress(100.0)

        opts = {
            "format": f"bestvideo[height<={config.MAX_VIDEO_HEIGHT}]+bestaudio/best[height<={config.MAX_VIDEO_HEIGHT}]",
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            result_path = ydl.prepare_filename(info)
            # yt-dlp may merge to mp4
            base, _ = os.path.splitext(result_path)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                result_path = mp4_path

    await asyncio.to_thread(_download)
    return result_path


async def encode_video(
    input_path: str,
    output_path: str,
    duration_secs: float,
    on_progress: Callable[[float], None],
) -> str:
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=768:576:force_original_aspect_ratio=decrease,pad=768:576:(768-iw)/2:(576-ih)/2,setsar=1:1",
        "-r", "25",
        "-progress", "pipe:1",
        "-loglevel", "quiet",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if decoded.startswith("out_time_us="):
            try:
                us = int(decoded.split("=")[1])
                if duration_secs > 0:
                    pct = min(100.0, (us / 1_000_000) / duration_secs * 100)
                    on_progress(pct)
            except ValueError:
                pass
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
    on_progress(100.0)
    return output_path


class PipelineWorker:
    def __init__(
        self, queue: QueueManager, chromecast: ChromecastManager
    ) -> None:
        self.queue = queue
        self.chromecast = chromecast
        self._cancel_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._current_proc: asyncio.subprocess.Process | None = None
        self._on_update: Callable | None = None

    def set_update_callback(self, callback: Callable) -> None:
        self._on_update = callback

    def notify(self) -> None:
        if self._on_update:
            self._on_update()

    def wake(self) -> None:
        self._wake_event.set()

    def cancel_current(self) -> None:
        self._cancel_event.set()

    async def run(self) -> None:
        while True:
            item = self.queue.next_pending()
            if item is None:
                self._wake_event.clear()
                await self._wake_event.wait()
                continue
            await self._process(item)

    async def _process(self, item: QueueItem) -> None:
        self._cancel_event.clear()
        try:
            # Fetch title
            item.title = await fetch_title(item.url)
            self.notify()

            # Download
            item.status = "downloading"
            item.progress = 0.0
            self.notify()

            def dl_progress(pct: float) -> None:
                item.progress = pct
                self.notify()

            downloaded_path = await download_video(
                item.url, config.TEMP_DIR, dl_progress
            )

            if self._cancel_event.is_set():
                item.status = "done"
                self.notify()
                return

            # Get duration for encoding progress
            duration = await self._get_duration(item.url)

            # Encode
            item.status = "encoding"
            item.progress = 0.0
            self.notify()

            base = os.path.splitext(os.path.basename(downloaded_path))[0]
            encoded_path = os.path.join(config.TEMP_DIR, f"{base}_pal.mp4")

            def enc_progress(pct: float) -> None:
                item.progress = pct
                self.notify()

            await encode_video(downloaded_path, encoded_path, duration, enc_progress)

            if self._cancel_event.is_set():
                item.status = "done"
                self.notify()
                return

            item.filename = os.path.basename(encoded_path)

            # Cast
            item.status = "casting"
            item.progress = 0.0
            self.notify()

            local_ip = get_local_ip()
            media_url = f"http://{local_ip}:{config.SERVER_PORT}/media/{item.filename}"
            await asyncio.to_thread(self.chromecast.cast_url, media_url)

            item.status = "playing"
            self.notify()

            # Wait for playback to end or cancellation
            await self._wait_for_playback_end()

            if not self._cancel_event.is_set():
                item.status = "done"
                self.notify()

        except Exception as e:
            log.exception("Pipeline error for %s", item.url)
            item.status = "error"
            item.error = str(e)
            self.notify()

    async def _get_duration(self, url: str) -> float:
        def _extract():
            opts = {"quiet": True, "no_warnings": True, "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("duration", 0.0)

        return await asyncio.to_thread(_extract)

    async def _wait_for_playback_end(self) -> None:
        while not self._cancel_event.is_set():
            if self.chromecast.playback_ended:
                return
            await asyncio.sleep(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: `test_fetch_title` PASS. The download and encode tests may need adjustments depending on mock setup — fix until green.

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline worker with download, encode, cast stages"
```

---

### Task 6: TUI

**Files:**
- Create: `ui.py`

- [ ] **Step 1: Implement the Textual app**

Create `ui.py`:

```python
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Select,
    Static,
)

from chromecast_mgr import ChromecastManager
from pipeline import PipelineWorker
from queue_manager import QueueItem, QueueManager


class NowPlayingWidget(Static):
    title = reactive("")
    status = reactive("")
    progress = reactive(0.0)
    playback_position = reactive(0.0)
    playback_duration = reactive(0.0)
    player_state = reactive("")

    def render(self) -> str:
        if not self.title:
            return "  No video playing"

        lines = [f'  "{self.title}"']

        if self.status in ("downloading", "encoding"):
            bar_width = 30
            filled = int(self.progress / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            label = self.status.capitalize()
            lines.append(f"  {bar} {label} {self.progress:.0f}%")

        if self.status == "playing" and self.playback_duration > 0:
            pos = self._format_time(self.playback_position)
            dur = self._format_time(self.playback_duration)
            bar_width = 30
            frac = self.playback_position / self.playback_duration
            filled = int(frac * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            state_icon = "▶" if self.player_state == "PLAYING" else "⏸"
            lines.append(f"  {state_icon} {pos} / {dur}  {bar}")

        if self.status == "casting":
            lines.append("  Connecting to Chromecast...")

        return "\n".join(lines)

    @staticmethod
    def _format_time(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


class QueueListItem(ListItem):
    def __init__(self, item: QueueItem, index: int) -> None:
        super().__init__()
        self.queue_item = item
        self.index = index

    def compose(self) -> ComposeResult:
        yield Label(f"  {self.index + 1}. {self.queue_item.title or self.queue_item.url}")


class CRTCastApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #header-bar {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        content-align: center middle;
    }
    #chromecast-status {
        dock: right;
        width: auto;
        padding: 0 1;
    }
    #url-input {
        margin: 1 2;
    }
    #mode-select {
        width: 16;
        margin: 0 2;
    }
    #input-row {
        height: 3;
        margin: 0 1;
    }
    #now-playing {
        height: auto;
        min-height: 5;
        margin: 1 2;
        border: solid $accent;
        padding: 0 1;
    }
    #now-playing-header {
        text-style: bold;
        margin: 0 1;
    }
    #queue-section {
        margin: 1 2;
        border: solid $accent;
        height: 1fr;
    }
    #queue-header {
        text-style: bold;
        margin: 0 1;
    }
    #queue-list {
        height: 1fr;
    }
    #controls-row {
        height: 1;
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("s", "stop", "Stop", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("plus,equal", "volume_up", "Vol+", show=True),
        Binding("minus", "volume_down", "Vol-", show=True),
        Binding("d", "remove_item", "Remove", show=True),
        Binding("k", "move_up", "Move Up", show=True),
        Binding("j", "move_down", "Move Down", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    chromecast_connected = reactive(False)
    chromecast_device = reactive("")

    def __init__(
        self,
        queue: QueueManager,
        pipeline: PipelineWorker,
        chromecast: ChromecastManager,
    ) -> None:
        super().__init__()
        self.queue = queue
        self.pipeline = pipeline
        self.chromecast = chromecast

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="input-row"):
            yield Input(placeholder="YouTube URL...", id="url-input")
            yield Select(
                [("Accoda", "queue"), ("Prossimo", "next"), ("Subito", "now")],
                value="queue",
                id="mode-select",
                allow_blank=False,
            )
        yield Static(" IN RIPRODUZIONE", id="now-playing-header")
        yield NowPlayingWidget(id="now-playing")
        yield Static(" CODA", id="queue-header")
        yield ListView(id="queue-list")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "CRT Cast"
        self.sub_title = "Disconnected"
        self.chromecast.set_status_callback(self._on_chromecast_status)
        self.chromecast.set_connection_callback(self._on_chromecast_connection)
        self.pipeline.set_update_callback(self._on_pipeline_update)
        asyncio.create_task(self.chromecast.discover_loop())
        asyncio.create_task(self.pipeline.run())

    def _on_chromecast_connection(self) -> None:
        self.call_from_thread(self._update_connection)

    def _update_connection(self) -> None:
        if self.chromecast.connected:
            self.sub_title = f"Chromecast: {self.chromecast.device_name} ●"
        else:
            self.sub_title = "Chromecast: Disconnected"

    def _on_chromecast_status(self) -> None:
        self.call_from_thread(self._update_playback)

    def _on_pipeline_update(self) -> None:
        self.call_from_thread(self._refresh_all)

    def _update_playback(self) -> None:
        widget = self.query_one("#now-playing", NowPlayingWidget)
        widget.playback_position = self.chromecast.current_time
        widget.playback_duration = self.chromecast.duration
        widget.player_state = self.chromecast.player_state

    def _refresh_all(self) -> None:
        active = self.queue.active_item()
        widget = self.query_one("#now-playing", NowPlayingWidget)
        if active:
            widget.title = active.title
            widget.status = active.status
            widget.progress = active.progress
        else:
            widget.title = ""
            widget.status = ""
            widget.progress = 0.0

        self._refresh_queue_list()

    def _refresh_queue_list(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        list_view.clear()
        for i, item in enumerate(self.queue.items):
            if item.status == "queued":
                list_view.append(QueueListItem(item, i))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        mode_select = self.query_one("#mode-select", Select)
        mode = str(mode_select.value)

        if mode == "now":
            self.pipeline.cancel_current()

        self.queue.add(url, mode=mode)
        self.pipeline.wake()
        event.input.value = ""
        self._refresh_all()

    def action_stop(self) -> None:
        self.pipeline.cancel_current()
        self.chromecast.stop()

    def action_pause(self) -> None:
        self.chromecast.pause_or_resume()

    def action_volume_up(self) -> None:
        self.chromecast.adjust_volume(10)

    def action_volume_down(self) -> None:
        self.chromecast.adjust_volume(-10)

    def action_remove_item(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.remove(queue_item.id)
            self._refresh_queue_list()

    def action_move_up(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.move(queue_item.id, "up")
            self._refresh_queue_list()

    def action_move_down(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        if list_view.highlighted_child is not None:
            queue_item = list_view.highlighted_child.queue_item
            self.queue.move(queue_item.id, "down")
            self._refresh_queue_list()
```

- [ ] **Step 2: Verify it imports without errors**

Run: `python -c "import ui; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ui.py
git commit -m "feat: TUI interface with Textual widgets and keybindings"
```

---

### Task 7: Main Entry Point

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement main.py**

Create `main.py`:

```python
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import main; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: main entry point wiring TUI, media server, and pipeline"
```

---

### Task 8: Integration Smoke Test

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify the app launches (then quit immediately with q)**

Run: `python main.py`
Expected: TUI renders with "CRT Cast" header, input field, empty queue. Chromecast shows "Disconnected" since no device is on the network. Press `q` to exit cleanly.

- [ ] **Step 3: Commit any test fixes if needed**

```bash
git add -A
git commit -m "fix: test adjustments from integration smoke test"
```

---

### Task 9: Cleanup and Final Polish

- [ ] **Step 1: Add .gitignore**

Create `.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add gitignore"
```
