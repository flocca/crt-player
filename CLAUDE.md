# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run
./run.sh                                          # starts the TUI app (sources .env automatically)

# Tests
source .venv/bin/activate
python -m pytest tests/ -v                        # full suite (23 tests)
python -m pytest tests/test_queue_manager.py -v   # single file
python -m pytest tests/test_pipeline.py::test_fetch_title -v  # single test

# Dependencies
pip install -r requirements.txt                   # inside .venv
```

External dependency: `ffmpeg` must be installed (`brew install ffmpeg`).

## Architecture

TUI app (Textual) that downloads YouTube videos, converts them to 4:3 PAL (768x576, 25fps), and casts to a Chromecast via pychromecast.

**Data flow:** User adds URL in TUI -> QueueManager stores item -> PipelineWorker picks it up -> yt-dlp downloads -> ffmpeg encodes -> pychromecast casts -> MediaStatusListener detects playback end -> next item.

**Threading model:** Textual runs the asyncio event loop. The pipeline worker runs as an asyncio task in that loop. Blocking operations (yt-dlp download, pychromecast calls) use `asyncio.to_thread()`. A minimal FastAPI/uvicorn server runs in a separate daemon thread solely to serve MP4 files to the Chromecast.

**Key integration point:** Pipeline callbacks can fire from both the main asyncio thread and worker threads. The TUI's `_safe_call` method handles this by trying `call_from_thread` first and falling back to a direct call.

**Configuration:** Environment variables loaded from `.env` via `run.sh`. Config constants in `config.py`.

## Debugging

Logs write to `crt_cast.log` (overwritten each run). stderr is redirected there too.
Scan for Chromecasts: `python -c "import pychromecast, time; ccs, b = pychromecast.get_chromecasts(); time.sleep(10); b.stop_discovery(); [print(cc.name, cc.model_name) for cc in ccs]"`

## Gotchas

- `call_from_thread` crashes if called from the main Textual thread. Always use `_safe_call` wrapper in `ui.py`.
- pychromecast `media_controller.stop()` raises `RequestFailed` if nothing is playing. Guard with player_state check.
- pychromecast status callbacks fire from a background thread — state can change between checks. Use `asyncio.Event` not polling for playback end detection.

## Language

The user interface is in Italian (labels, section headers). Code, comments, and logs are in English.
