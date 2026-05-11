from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from typing import Callable

import yt_dlp

from crt import config
from crt.chromecast_mgr import ChromecastManager
from crt.library_store import QueueItem, LibraryStore

log = logging.getLogger(__name__)


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def fetch_title(url: str) -> tuple[str, str]:
    """Return (title, video_id) for the given URL."""
    def _extract():
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", "Unknown"), info.get("id", "")

    return await asyncio.to_thread(_extract)


async def download_video(
    url: str, temp_dir: str, on_progress: Callable[[float], None]
) -> tuple[str, float]:
    result_path: str = ""
    duration: float = 0.0

    def _download():
        nonlocal result_path, duration

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
            duration = info.get("duration", 0.0) or 0.0
            result_path = ydl.prepare_filename(info)
            # yt-dlp may merge to mp4
            base, _ = os.path.splitext(result_path)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                result_path = mp4_path

    await asyncio.to_thread(_download)
    return result_path, duration


def _build_video_filter(crop_detect: str | None = None) -> str:
    w, h = 768, 576
    out_w = w * 16 // 12  # 1024
    prefix = f"{crop_detect}," if crop_detect else ""

    top = config.MARGIN_TOP
    bottom = config.MARGIN_BOTTOM
    left = config.MARGIN_LEFT
    right = config.MARGIN_RIGHT
    has_margins = any((top, bottom, left, right))

    if not has_margins:
        # Back-compat fast path: keep filter byte-identical to the pre-margin
        # version so cached encoded files stay valid.
        if config.SCALE_MODE == "crop":
            return (
                f"{prefix}scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},scale={out_w}:{h},setsar=1:1"
            )
        return (
            f"{prefix}scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:({w}-iw)/2:({h}-ih)/2,"
            f"scale={out_w}:{h},setsar=1:1"
        )

    inner_w = w - left - right
    inner_h = h - top - bottom
    if config.SCALE_MODE == "crop":
        return (
            f"{prefix}scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase,"
            f"crop={inner_w}:{inner_h},"
            f"pad={w}:{h}:{left}:{top}:color=black,"
            f"scale={out_w}:{h},setsar=1:1"
        )
    return (
        f"{prefix}scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,"
        f"pad={inner_w}:{inner_h}:({inner_w}-iw)/2:({inner_h}-ih)/2,"
        f"pad={w}:{h}:{left}:{top}:color=black,"
        f"scale={out_w}:{h},setsar=1:1"
    )


async def _get_duration(path: str) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def _detect_crop(input_path: str) -> str | None:
    """Run a quick cropdetect pass and return the most common crop value."""
    if not config.AUTO_CROP:
        return None
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "cropdetect=24:16:0",
        "-frames:v", "120",
        "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    crops: dict[str, int] = {}
    for line in stderr.decode(errors="replace").splitlines():
        if "crop=" in line:
            crop_val = line.rpartition("crop=")[2].strip()
            crops[crop_val] = crops.get(crop_val, 0) + 1
    if not crops:
        return None
    best = max(crops, key=crops.get)
    # Only apply if it actually trims something (not full-frame crop)
    parts = best.split(":")
    if len(parts) == 4:
        cw, ch = int(parts[0]), int(parts[1])
        # Skip if crop removes less than 16px on any side
        if cw < 32 or ch < 32:
            return None
        return f"crop={best}"
    return None


async def encode_video(
    input_path: str,
    output_path: str,
    duration_secs: float,
    on_progress: Callable[[float], None],
    worker: PipelineWorker | None = None,
) -> str:
    crop_filter = await _detect_crop(input_path)
    if crop_filter:
        log.info("Detected source black bars, applying: %s", crop_filter)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", _build_video_filter(crop_filter),
        "-r", "25",
        "-progress", "pipe:1",
        "-loglevel", "quiet",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    if worker:
        worker._current_proc = proc
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
    if worker:
        worker._current_proc = None
    if proc.returncode not in (0, -15):  # 0=success, -15=SIGTERM (cancelled)
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
    if proc.returncode == 0:
        on_progress(100.0)
    return output_path


class PipelineWorker:
    def __init__(
        self, queue: LibraryStore, chromecast: ChromecastManager
    ) -> None:
        self.queue = queue
        self.chromecast = chromecast
        self._prepare_cancel = asyncio.Event()
        self._cast_cancel = asyncio.Event()
        self._prepare_wake = asyncio.Event()
        self._cast_wake = asyncio.Event()
        self._current_proc: asyncio.subprocess.Process | None = None
        self._on_update: Callable | None = None
        self.resume_position: float = 0.0
        self._cast_enabled: bool = False  # True once user explicitly starts playback
        self.loop_mode: bool = config.LOOP_MODE_DEFAULT
        self._next_item_id: str | None = None  # Specific item to cast next (no reorder)

    def set_update_callback(self, callback: Callable) -> None:
        self._on_update = callback

    def notify(self) -> None:
        if self._on_update:
            self._on_update()

    def cancel_prepare(self) -> None:
        self._prepare_cancel.set()
        if self._current_proc and self._current_proc.returncode is None:
            self._current_proc.terminate()

    def cancel_cast(self) -> None:
        self._cast_cancel.set()

    def cancel_current(self) -> None:
        self.cancel_prepare()
        self.cancel_cast()

    def wake(self) -> None:
        self._cast_enabled = True
        self._prepare_wake.set()
        self._cast_wake.set()

    def wake_prepare(self) -> None:
        """Wake only the prepare loop, leaving the cast loop idle."""
        self._prepare_wake.set()

    async def run_prepare(self) -> None:
        while True:
            self._prepare_cancel.clear()
            # Prioritise an explicitly-requested item (set via _next_item_id)
            # so it gets prepared even if it sits before the cursor.
            item = None
            if self._next_item_id:
                item = next(
                    (i for i in self.queue.items if i.id == self._next_item_id and i.status == "queued"),
                    None,
                )
            if item is None:
                item = self.queue.first_queued_after_cursor()
            # Fallback: after a loop-wrap, the next candidate may be before the
            # cursor (e.g. items[0] after all items are done). first_queued()
            # finds it while first_queued_after_cursor() would not.
            if item is None:
                item = self.queue.first_queued()
            if item is None:
                self._prepare_wake.clear()
                await self._prepare_wake.wait()
                continue
            await self._prepare_one(item)
            self._cast_wake.set()

    async def run_cast(self) -> None:
        while True:
            self._cast_cancel.clear()
            item = None
            if self._cast_enabled:
                if self._next_item_id:
                    nid = self._next_item_id
                    target = next((i for i in self.queue.items if i.id == nid), None)
                    if target is None or target.status == "error":
                        # Item was removed or failed to prepare — clear the pending
                        # selection so normal cursor-based playback can resume.
                        self._next_item_id = None
                    elif target.status == "ready":
                        item = target
                        self._next_item_id = None
                # Only advance cursor when no explicit selection is pending.
                # If _next_item_id is set but item isn't ready yet, we wait
                # for the prepare loop to finish encoding it.
                if item is None and not self._next_item_id:
                    candidate = self.queue.advance_cursor(loop=self.loop_mode)
                    if candidate is not None:
                        self.queue.prepare_for_play(candidate)
                        if candidate.status == "ready":
                            item = candidate
                        elif candidate.status == "queued":
                            # Cache miss: wake prepare loop so it encodes the item.
                            self.wake_prepare()
            if item is None:
                self._cast_wake.clear()
                await self._cast_wake.wait()
                continue
            await self._cast_and_wait(item)

    async def _prepare_one(self, item: QueueItem) -> None:
        try:
            item.title, video_id = await fetch_title(item.url)
            if video_id:
                item.video_id = video_id
            self.notify()

            # Check for cached encoded file
            cached_encoded = os.path.join(config.TEMP_DIR, config.cached_encoded_filename(video_id))
            if video_id and os.path.isfile(cached_encoded):
                log.info("Using cached encoded file: %s", cached_encoded)
                item.filename = os.path.basename(cached_encoded)
                item.status = "ready"
                self.notify()
                return

            # Skip download if file already exists from a previous interrupted session
            if item.downloaded_path and os.path.isfile(item.downloaded_path):
                log.info("Resuming encode from existing download: %s", item.downloaded_path)
                downloaded_path = item.downloaded_path
                duration = await _get_duration(downloaded_path)
            else:
                # Download
                item.status = "downloading"
                item.progress = 0.0
                self.notify()
                log.info("Downloading: %s (%s)", item.title, item.url)
                t0 = time.monotonic()

                def dl_progress(pct: float) -> None:
                    item.progress = pct
                    self.notify()

                downloaded_path, duration = await download_video(
                    item.url, config.TEMP_DIR, dl_progress
                )
                item.downloaded_path = downloaded_path

                if self._prepare_cancel.is_set():
                    item.status = "queued"
                    item.progress = 0.0
                    self.notify()
                    return

                size_mb = os.path.getsize(downloaded_path) / (1024 * 1024)
                log.info(
                    "Downloaded: %s in %.1fs (%.1f MB, video duration %.0fs)",
                    item.title, time.monotonic() - t0, size_mb, duration,
                )

            # Encode
            item.status = "encoding"
            item.progress = 0.0
            self.notify()
            log.info("Encoding: %s (video duration %.0fs)", item.title, duration)
            t0 = time.monotonic()

            base = os.path.splitext(os.path.basename(downloaded_path))[0]
            encoded_path = os.path.join(config.TEMP_DIR, config.cached_encoded_filename(base))

            def enc_progress(pct: float) -> None:
                item.progress = pct
                self.notify()

            await encode_video(downloaded_path, encoded_path, duration, enc_progress, worker=self)

            if self._prepare_cancel.is_set():
                item.status = "queued"
                item.progress = 0.0
                self.notify()
                return

            item.filename = os.path.basename(encoded_path)
            item.status = "ready"
            self.notify()
            log.info("Ready: %s (encoded in %.1fs)", item.title, time.monotonic() - t0)

        except Exception as e:
            log.exception("Pipeline prepare error for %s", item.url)
            item.status = "error"
            item.error = str(e)
            self.notify()

    async def _cast_and_wait(self, item: QueueItem) -> None:
        item.status = "casting"
        item.progress = 0.0
        self.notify()

        # Wait for Chromecast to be discovered before casting
        if not self.chromecast.connected:
            log.info("Waiting for Chromecast connection before casting...")
            conn_task = asyncio.create_task(self.chromecast.wait_for_connection())
            cancel_task = asyncio.create_task(self._cast_cancel.wait())
            done, pending = await asyncio.wait(
                {conn_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            if self._cast_cancel.is_set():
                item.status = "done"
                self.notify()
                return

        local_ip = get_local_ip()
        media_url = f"http://{local_ip}:{config.SERVER_PORT}/media/{item.filename}"
        start_pos = self.resume_position
        self.resume_position = 0.0
        try:
            await asyncio.to_thread(self.chromecast.cast_url, media_url, start_pos)
        except Exception as e:
            log.exception("cast_url failed for %s", item.url)
            item.status = "error"
            item.error = str(e)
            self.notify()
            return

        item.status = "playing"
        self.notify()

        await self._wait_for_playback_end()

        item.status = "done"
        if not self._cast_cancel.is_set():
            self.queue.push_to_history(item)
        self.notify()

    async def _wait_for_playback_end(self) -> None:
        self.chromecast.reset_playback_ended()
        log.info("pipeline: waiting for playback end (event cleared)")
        cancel_task = asyncio.create_task(self._cast_cancel.wait())
        playback_task = asyncio.create_task(self.chromecast.wait_for_playback_end())
        done, pending = await asyncio.wait(
            {cancel_task, playback_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        reason = "cancel" if cancel_task in done else "playback_end"
        log.info("pipeline: _wait_for_playback_end returned (reason=%s)", reason)
