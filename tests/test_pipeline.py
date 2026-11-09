import asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

import crt.config as config_module
from crt.pipeline import fetch_title, download_video, encode_video, _build_video_filter, _detect_crop
from crt.config import cached_encoded_filename
from crt.library_store import QueueItem


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
    mock_info = {"title": "Test Video Title", "id": "abc", "duration": 552}
    with patch("crt.pipeline.yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_info
        title, video_id, duration = await fetch_title("https://youtube.com/watch?v=abc")
    assert title == "Test Video Title"
    assert video_id == "abc"
    assert duration == 552.0


@pytest.mark.asyncio
async def test_fetch_title_without_duration_gives_zero():
    """Live streams and some extractors carry no duration (or None)."""
    with patch("crt.pipeline.yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = {"title": "T", "id": "abc", "duration": None}
        _, _, duration = await fetch_title("https://youtube.com/watch?v=abc")
    assert duration == 0.0


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

    with patch("crt.pipeline.yt_dlp.YoutubeDL", side_effect=capture_init):
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

    with patch("crt.pipeline._detect_crop", return_value=None), \
         patch("crt.pipeline.asyncio.create_subprocess_exec", return_value=mock_process):
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
    with patch("crt.pipeline.asyncio.create_subprocess_exec") as mock_exec:
        result = await _detect_crop("/tmp/anything.mp4")
    assert result is None
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_detect_crop_runs_ffmpeg_when_auto_crop_enabled():
    config_module.AUTO_CROP = True
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    with patch("crt.pipeline.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await _detect_crop("/tmp/anything.mp4")
    mock_exec.assert_called_once()


def test_pipeline_worker_loop_mode_defaults_to_false():
    from crt.pipeline import PipelineWorker
    from unittest.mock import MagicMock
    config_module.LOOP_MODE_DEFAULT = False  # pin so test is not CRT_LOOP-env-dependent
    worker = PipelineWorker(MagicMock())
    assert worker.loop_mode is False


def test_pipeline_worker_loop_mode_reads_from_config(monkeypatch):
    monkeypatch.setattr(config_module, "LOOP_MODE_DEFAULT", True)
    from crt.pipeline import PipelineWorker
    from unittest.mock import MagicMock
    worker = PipelineWorker(MagicMock())
    assert worker.loop_mode is True


def _worker_with_slow_encode(monkeypatch, tmp_path, video_id="A"):
    """A PipelineWorker whose encode runs until terminated, like a long ffmpeg."""
    from crt import pipeline as pl
    from crt.library_store import LibraryStore
    monkeypatch.setattr(config_module, "TEMP_DIR", str(tmp_path))
    item = QueueItem(url=f"https://www.youtube.com/watch?v={video_id}", video_id=video_id)
    library = LibraryStore()
    library.items.append(item)
    worker = pl.PipelineWorker(library)
    encoding = asyncio.Event()

    async def fake_title(url):
        return video_id, video_id, 300.0

    async def fake_download(url, temp_dir, on_progress):
        path = os.path.join(temp_dir, f"{video_id}.mp4")
        with open(path, "w") as f:
            f.write("source")
        return path, 10.0

    async def fake_encode(input_path, output_path, duration, on_progress, worker=None):
        with open(output_path, "w") as f:
            f.write("partial")
        proc = await asyncio.create_subprocess_exec("sleep", "30")
        worker._current_proc = proc
        encoding.set()
        await proc.wait()
        worker._current_proc = None
        return output_path

    monkeypatch.setattr(pl, "fetch_title", fake_title)
    monkeypatch.setattr(pl, "download_video", fake_download)
    monkeypatch.setattr(pl, "encode_video", fake_encode)
    return worker, item, encoding


@pytest.mark.asyncio
async def test_cancel_prepare_for_stops_the_encode_of_that_video(monkeypatch, tmp_path):
    """Issue #17: a video removed from the playlist while encoding kept ffmpeg
    busy to the end, holding up the whole queue."""
    worker, item, encoding = _worker_with_slow_encode(monkeypatch, tmp_path)
    task = asyncio.create_task(worker._prepare_one(item))
    await asyncio.wait_for(encoding.wait(), 2)

    worker.cancel_prepare_for("A")

    await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_cancel_prepare_for_leaves_other_videos_alone(monkeypatch, tmp_path):
    worker, item, encoding = _worker_with_slow_encode(monkeypatch, tmp_path)
    task = asyncio.create_task(worker._prepare_one(item))
    await asyncio.wait_for(encoding.wait(), 2)

    worker.cancel_prepare_for("B")
    await asyncio.sleep(0.2)

    assert not task.done()
    worker.cancel_prepare()
    await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_cancelled_encode_leaves_no_partial_file(monkeypatch, tmp_path):
    """Issue #17: a truncated encoded file left behind would be taken as ready
    ("Using cached encoded file") if the video came back into the queue."""
    worker, item, encoding = _worker_with_slow_encode(monkeypatch, tmp_path)
    task = asyncio.create_task(worker._prepare_one(item))
    await asyncio.wait_for(encoding.wait(), 2)

    worker.cancel_prepare()
    await asyncio.wait_for(task, 2)

    assert not (tmp_path / cached_encoded_filename("A")).exists()


def _fake_ffmpeg(returncode, calls):
    """Stand-in for create_subprocess_exec: writes the output file (args[-1])."""
    async def fake_exec(*args, **kwargs):
        calls.append(args)
        with open(args[-1], "w") as f:
            f.write("preview")
        proc = MagicMock()
        proc.returncode = returncode
        proc.wait = AsyncMock(return_value=returncode)
        return proc
    return fake_exec


@pytest.mark.asyncio
async def test_make_preview_remuxes_video_only(monkeypatch, tmp_path):
    from crt import pipeline as pl
    calls = []
    monkeypatch.setattr(pl.asyncio, "create_subprocess_exec", _fake_ffmpeg(0, calls))
    encoded = tmp_path / "X.mp4"
    encoded.write_text("enc")

    result = await pl.make_preview(str(encoded))

    args = calls[0]
    assert "-map" in args and args[args.index("-map") + 1] == "0:v"
    assert args[args.index("-c") + 1] == "copy" and "-an" in args
    assert result == str(tmp_path / "X_noaudio.mp4")
    assert args[-1] != result
    assert sorted(p.name for p in tmp_path.iterdir()) == ["X.mp4", "X_noaudio.mp4"]


@pytest.mark.asyncio
async def test_make_preview_failure_leaves_no_file(monkeypatch, tmp_path):
    from crt import pipeline as pl
    monkeypatch.setattr(pl.asyncio, "create_subprocess_exec", _fake_ffmpeg(1, []))
    encoded = tmp_path / "X.mp4"
    encoded.write_text("enc")

    assert await pl.make_preview(str(encoded)) is None
    assert [p.name for p in tmp_path.iterdir()] == ["X.mp4"]


@pytest.mark.asyncio
async def test_prepare_one_makes_preview_after_ready(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)

    async def quick_encode(input_path, output_path, duration, on_progress, worker=None):
        with open(output_path, "w") as f:
            f.write("encoded")
        return output_path

    preview = AsyncMock(return_value=None)
    monkeypatch.setattr(pl, "encode_video", quick_encode)
    monkeypatch.setattr(pl, "make_preview", preview)

    await worker._prepare_one(item)

    assert item.status == "ready"
    preview.assert_awaited_once_with(str(tmp_path / cached_encoded_filename("A")))


@pytest.mark.asyncio
async def test_prepare_one_preview_failure_keeps_ready(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)

    async def quick_encode(input_path, output_path, duration, on_progress, worker=None):
        with open(output_path, "w") as f:
            f.write("encoded")
        return output_path

    monkeypatch.setattr(pl, "encode_video", quick_encode)
    monkeypatch.setattr(pl, "make_preview", AsyncMock(return_value=None))

    await worker._prepare_one(item)

    assert item.status == "ready" and item.error is None


@pytest.mark.asyncio
async def test_prepare_one_cache_hit_makes_missing_preview(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)
    encoded = tmp_path / cached_encoded_filename("A")
    encoded.write_text("encoded")
    preview = AsyncMock(return_value=None)
    monkeypatch.setattr(pl, "make_preview", preview)

    await worker._prepare_one(item)

    assert item.status == "ready"
    preview.assert_awaited_once_with(str(encoded))


@pytest.mark.asyncio
async def test_backfill_previews_only_for_ready_items_without_copy(monkeypatch, tmp_path):
    from crt import pipeline as pl
    from crt.library_store import LibraryStore
    monkeypatch.setattr(config_module, "TEMP_DIR", str(tmp_path))
    (tmp_path / "A.mp4").write_text("x")
    (tmp_path / "B.mp4").write_text("x")
    (tmp_path / "B_noaudio.mp4").write_text("x")
    library = LibraryStore()
    library.items += [
        QueueItem(url="u", video_id="A", status="ready", filename="A.mp4"),
        QueueItem(url="u", video_id="B", status="ready", filename="B.mp4"),
        QueueItem(url="u", video_id="C", status="queued"),
    ]
    worker = pl.PipelineWorker(library)
    preview = AsyncMock(return_value=None)
    monkeypatch.setattr(pl, "make_preview", preview)

    await worker.backfill_previews()

    preview.assert_awaited_once_with(str(tmp_path / "A.mp4"))


@pytest.mark.asyncio
async def test_concurrent_make_preview_calls_use_distinct_temp_files(monkeypatch, tmp_path):
    """backfill_previews and _prepare_one can remux the same video at once:
    a shared temp name would let one ffmpeg overwrite the other's output."""
    from crt import pipeline as pl
    calls = []
    monkeypatch.setattr(pl.asyncio, "create_subprocess_exec", _fake_ffmpeg(0, calls))
    encoded = tmp_path / "X.mp4"
    encoded.write_text("enc")

    await asyncio.gather(pl.make_preview(str(encoded)), pl.make_preview(str(encoded)))

    tmps = [args[-1] for args in calls]
    assert len(set(tmps)) == 2
    assert all(t.endswith(".part.mp4") for t in tmps)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["X.mp4", "X_noaudio.mp4"]


@pytest.mark.asyncio
async def test_backfill_previews_skips_the_item_being_prepared(monkeypatch, tmp_path):
    from crt import pipeline as pl
    from crt.library_store import LibraryStore
    monkeypatch.setattr(config_module, "TEMP_DIR", str(tmp_path))
    (tmp_path / "A.mp4").write_text("x")
    (tmp_path / "B.mp4").write_text("x")
    library = LibraryStore()
    library.items += [
        QueueItem(url="u", video_id="A", status="ready", filename="A.mp4"),
        QueueItem(url="u", video_id="B", status="ready", filename="B.mp4"),
    ]
    worker = pl.PipelineWorker(library)
    worker._preparing = library.items[0]  # the worker makes A's copy itself
    preview = AsyncMock(return_value=None)
    monkeypatch.setattr(pl, "make_preview", preview)

    await worker.backfill_previews()

    preview.assert_awaited_once_with(str(tmp_path / "B.mp4"))


@pytest.mark.asyncio
async def test_prepare_one_cache_hit_takes_the_duration_from_fetch_title(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)
    (tmp_path / cached_encoded_filename("A")).write_text("encoded")
    monkeypatch.setattr(pl, "make_preview", AsyncMock(return_value=None))

    await worker._prepare_one(item)

    assert item.duration == 300.0


async def _quick_encode(input_path, output_path, duration, on_progress, worker=None):
    with open(output_path, "w") as f:
        f.write("encoded")
    return output_path


@pytest.mark.asyncio
async def test_prepare_one_download_overwrites_the_duration(monkeypatch, tmp_path):
    """The downloaded file's duration (10 s in the fixture) wins over the
    metadata read before it."""
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)
    monkeypatch.setattr(pl, "encode_video", _quick_encode)
    monkeypatch.setattr(pl, "make_preview", AsyncMock(return_value=None))

    await worker._prepare_one(item)

    assert item.duration == 10.0


@pytest.mark.asyncio
async def test_prepare_one_download_without_duration_keeps_the_known_one(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)

    async def no_duration_download(url, temp_dir, on_progress):
        path = os.path.join(temp_dir, "A.mp4")
        with open(path, "w") as f:
            f.write("source")
        return path, 0.0

    monkeypatch.setattr(pl, "download_video", no_duration_download)
    monkeypatch.setattr(pl, "encode_video", _quick_encode)
    monkeypatch.setattr(pl, "make_preview", AsyncMock(return_value=None))

    await worker._prepare_one(item)

    assert item.duration == 300.0


@pytest.mark.asyncio
async def test_prepare_one_resumed_download_takes_the_ffprobe_duration(monkeypatch, tmp_path):
    from crt import pipeline as pl
    worker, item, _ = _worker_with_slow_encode(monkeypatch, tmp_path)
    raw = tmp_path / "A.mp4"
    raw.write_text("source")
    item.downloaded_path = str(raw)
    monkeypatch.setattr(pl, "_get_duration", AsyncMock(return_value=42.0))
    monkeypatch.setattr(pl, "encode_video", _quick_encode)
    monkeypatch.setattr(pl, "make_preview", AsyncMock(return_value=None))

    await worker._prepare_one(item)

    assert item.duration == 42.0


@pytest.mark.asyncio
async def test_backfill_durations_probes_encoded_items_without_duration(monkeypatch, tmp_path):
    from crt import pipeline as pl
    from crt.library_store import LibraryStore
    monkeypatch.setattr(config_module, "TEMP_DIR", str(tmp_path))
    for name in ("A.mp4", "B.mp4"):
        (tmp_path / name).write_text("x")
    library = LibraryStore()
    library.items += [
        QueueItem(url="u", video_id="A", status="ready", filename="A.mp4"),
        QueueItem(url="u", video_id="B", status="done", filename="B.mp4", duration=99.0),
        QueueItem(url="u", video_id="C", status="queued"),
        QueueItem(url="u", video_id="D", status="ready", filename="gone.mp4"),
    ]
    worker = pl.PipelineWorker(library)
    probe = AsyncMock(return_value=552.0)
    monkeypatch.setattr(pl, "_get_duration", probe)
    updates = []
    worker.set_update_callback(lambda: updates.append(1))

    await worker.backfill_durations()

    probe.assert_awaited_once_with(str(tmp_path / "A.mp4"))
    assert [i.duration for i in library.items] == [552.0, 99.0, 0.0, 0.0]
    assert updates  # the open screen redraws with the new durations


@pytest.mark.asyncio
async def test_backfill_durations_leaves_zero_when_ffprobe_fails(monkeypatch, tmp_path):
    from crt import pipeline as pl
    from crt.library_store import LibraryStore
    monkeypatch.setattr(config_module, "TEMP_DIR", str(tmp_path))
    (tmp_path / "A.mp4").write_text("x")
    library = LibraryStore()
    library.items.append(QueueItem(url="u", video_id="A", status="ready", filename="A.mp4"))
    worker = pl.PipelineWorker(library)
    monkeypatch.setattr(pl, "_get_duration", AsyncMock(return_value=0.0))

    await worker.backfill_durations()

    assert library.items[0].duration == 0.0
