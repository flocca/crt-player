import asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from pipeline import fetch_title, download_video, encode_video
from queue_manager import QueueItem


@pytest.mark.asyncio
async def test_fetch_title():
    mock_info = {"title": "Test Video Title", "id": "abc"}
    with patch("pipeline.yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        title, video_id = await fetch_title("https://youtube.com/watch?v=abc")
    assert title == "Test Video Title"
    assert video_id == "abc"


@pytest.mark.asyncio
async def test_download_video():
    progress_values = []

    def on_progress(pct):
        progress_values.append(pct)

    captured_opts: dict = {}
    MockYDL_instance = MagicMock()

    def capture_init(opts):
        # Capture opts (including progress_hooks) passed to YoutubeDL constructor
        captured_opts.update(opts)
        # Set up extract_info to fire the hooks and return mock info
        def fake_extract_info(url, download=True):
            for hook in captured_opts.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
                hook({"status": "finished"})
            return {"id": "abc", "ext": "mp4", "title": "Test", "duration": 120.0}
        MockYDL_instance.extract_info.side_effect = fake_extract_info
        MockYDL_instance.prepare_filename.return_value = "/tmp/test/abc.mp4"
        context_mock = MagicMock()
        context_mock.__enter__.return_value = MockYDL_instance
        context_mock.__exit__.return_value = False
        return context_mock

    with patch("pipeline.yt_dlp.YoutubeDL", side_effect=capture_init):
        filepath, duration = await download_video(
            "https://youtube.com/watch?v=abc", "/tmp/test", on_progress
        )

    assert len(progress_values) >= 1
    assert duration == 120.0


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

    with patch("pipeline._detect_crop", return_value=None), \
         patch("pipeline.asyncio.create_subprocess_exec", return_value=mock_process):
        result = await encode_video(input_file, output_file, 10.0, on_progress)

    assert result == output_file
