import asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

import config as config_module
from pipeline import fetch_title, download_video, encode_video, _build_video_filter, _detect_crop
from config import cached_encoded_filename
from queue_manager import QueueItem


@pytest.fixture(autouse=True)
def _restore_config():
    orig_scale = config_module.SCALE_MODE
    orig_top = config_module.MARGIN_TOP
    orig_bottom = config_module.MARGIN_BOTTOM
    orig_left = config_module.MARGIN_LEFT
    orig_right = config_module.MARGIN_RIGHT
    orig_auto_crop = config_module.AUTO_CROP
    orig_loop = config_module.LOOP_MODE_DEFAULT
    yield
    config_module.SCALE_MODE = orig_scale
    config_module.MARGIN_TOP = orig_top
    config_module.MARGIN_BOTTOM = orig_bottom
    config_module.MARGIN_LEFT = orig_left
    config_module.MARGIN_RIGHT = orig_right
    config_module.AUTO_CROP = orig_auto_crop
    config_module.LOOP_MODE_DEFAULT = orig_loop


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


def _reset_margins(top=0, bottom=0, left=0, right=0):
    config_module.MARGIN_TOP = top
    config_module.MARGIN_BOTTOM = bottom
    config_module.MARGIN_LEFT = left
    config_module.MARGIN_RIGHT = right


def test_build_filter_no_margins_crop_mode_is_backcompat():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter(None)
    expected = (
        "scale=768:576:force_original_aspect_ratio=increase,"
        "crop=768:576,scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_no_margins_pad_mode_is_backcompat():
    _reset_margins()
    config_module.SCALE_MODE = "pad"
    result = _build_video_filter(None)
    expected = (
        "scale=768:576:force_original_aspect_ratio=decrease,"
        "pad=768:576:(768-iw)/2:(576-ih)/2,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_crop_mode_with_margins():
    _reset_margins(top=20, bottom=0, left=10, right=0)
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter(None)
    expected = (
        "scale=758:556:force_original_aspect_ratio=increase,"
        "crop=758:556,"
        "pad=768:576:10:20:color=black,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_pad_mode_with_margins():
    _reset_margins(top=10, bottom=10, left=20, right=20)
    config_module.SCALE_MODE = "pad"
    result = _build_video_filter(None)
    expected = (
        "scale=728:556:force_original_aspect_ratio=decrease,"
        "pad=728:556:(728-iw)/2:(556-ih)/2,"
        "pad=768:576:20:10:color=black,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_prepends_crop_detect_when_given():
    _reset_margins(top=20, left=10)
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter("crop=640:480:0:0")
    assert result.startswith("crop=640:480:0:0,scale=758:556")


def test_build_filter_no_margins_prepends_crop_detect():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter("crop=640:480:0:0")
    assert result.startswith("crop=640:480:0:0,scale=768:576")


def test_cached_filename_no_margins_is_legacy_shape():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    assert cached_encoded_filename("abc123") == "abc123_pal_crop.mp4"


def test_cached_filename_with_margins_has_suffix():
    _reset_margins(top=10, bottom=15, left=5, right=8)
    config_module.SCALE_MODE = "crop"
    assert cached_encoded_filename("abc123") == "abc123_pal_crop_m10-15-5-8.mp4"


def test_cached_filename_pad_mode_no_margins():
    _reset_margins()
    config_module.SCALE_MODE = "pad"
    assert cached_encoded_filename("xyz") == "xyz_pal_pad.mp4"


@pytest.mark.asyncio
async def test_detect_crop_returns_none_when_auto_crop_disabled():
    config_module.AUTO_CROP = False
    # Should short-circuit without shelling out to ffmpeg at all.
    with patch("pipeline.asyncio.create_subprocess_exec") as mock_exec:
        result = await _detect_crop("/tmp/anything.mp4")
    assert result is None
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_detect_crop_runs_ffmpeg_when_auto_crop_enabled():
    config_module.AUTO_CROP = True
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    with patch("pipeline.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await _detect_crop("/tmp/anything.mp4")
    mock_exec.assert_called_once()


def test_pipeline_worker_loop_mode_defaults_to_false():
    from pipeline import PipelineWorker
    from unittest.mock import MagicMock
    worker = PipelineWorker(MagicMock(), MagicMock())
    assert worker.loop_mode is False


def test_pipeline_worker_loop_mode_reads_from_config(monkeypatch):
    monkeypatch.setattr(config_module, "LOOP_MODE_DEFAULT", True)
    from pipeline import PipelineWorker
    from unittest.mock import MagicMock
    worker = PipelineWorker(MagicMock(), MagicMock())
    assert worker.loop_mode is True
