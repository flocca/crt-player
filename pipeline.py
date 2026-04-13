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
