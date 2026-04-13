# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run
./run.sh                                          # starts the TUI app (sources .env automatically)

# Tests
source .venv/bin/activate
python -m pytest tests/ -v                        # full suite (45 tests)
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

**Configuration:** Environment variables loaded from `.env` via `run.sh`. Config constants in `config.py`. Key env vars: `CRT_SCALE_MODE` (`crop`|`pad`, default `crop`) — crop fills the frame by cutting edges, pad adds letterbox bars.

**Persistence:** Queue state (items, history, playback position) saved to `~/.local/share/crt-player/state.json` (configurable via `CRT_STATE_FILE`). Auto-saves every 60s + on exit. On reload, mid-processing items reset to `"queued"`; playing items become `"ready"` if encoded file exists (skips download+encode).

**Encoding pipeline:** `_detect_crop()` runs a cropdetect pre-pass (120 frames) to remove baked-in black bars from the source. `_build_video_filter()` then applies scale+crop or scale+pad based on `CRT_SCALE_MODE`, and stretches the result to 16:9 (1024x576) so the Chromecast doesn't add pillarboxing — the user's HW squeezes 16:9→4:3 restoring correct proportions.

**Encoding cache:** Cached files are named `{video_id}_pal_{scale_mode}.mp4` in TEMP_DIR. Changing `CRT_SCALE_MODE` triggers re-encode. Files live for `FILE_TTL_HOURS` (default 24h). `fetch_title()` returns `(title, video_id)`.

## Debugging

Logs write to `crt_cast.log` (overwritten each run). stderr is redirected there too.
Scan for Chromecasts: `python -c "import pychromecast, time; ccs, b = pychromecast.get_chromecasts(); time.sleep(10); b.stop_discovery(); [print(cc.name, cc.model_name) for cc in ccs]"`

## Testing

TUI tests use Textual's `app.run_test()` / Pilot API (`tests/test_ui.py`). Shared fixtures in `tests/conftest.py`.
- `QueueManager` is used as-is (pure data). `PipelineWorker` and `ChromecastManager` are `MagicMock` with `AsyncMock` for async methods — this neutralizes `on_mount`'s infinite-loop tasks.
- `on_mount` only calls `_refresh_all` when `next_pending()` finds a queued item. To test UI state for "playing"/"ready" items, call `app._refresh_all()` manually after mount.
- Always `await pilot.pause()` after interactions (`press`, `click`, value changes) — handlers run asynchronously.
- Textual `ListView` is falsy when empty. Don't `assert widget` — use `query_one()` (raises `NoMatches` if absent).

## Gotchas

- `call_from_thread` crashes if called from the main Textual thread. Always use `_safe_call` wrapper in `ui.py`.
- Textual `Binding` with letter keys (`s`, `p`, `q`) won't show in Footer and conflict with Input widget text entry. Use `ctrl+` combos with `priority=True` for global bindings.
- All pychromecast commands (stop, pause, play, volume) can raise `RequestTimeout`/`RequestFailed`. Wrap in `_safe_cmd` in `chromecast_mgr.py`.
- pychromecast commands (pause, seek, stop) block for up to 10s on timeout. Always call them via `asyncio.to_thread()` from async action handlers. Textual supports `async def action_*()` natively.
- `pause_or_resume()` uses cached `self.player_state` — don't re-call `poll_status()` before issuing a command, it adds a redundant blocking round-trip.
- pychromecast status callbacks fire from a background thread — state can change between checks. Use `asyncio.Event` not polling for playback end detection.
- pychromecast `MediaStatusListener` only fires on state changes, not position updates. Use `set_interval` + `poll_status()` for playback progress.
- `media_controller.status.current_time` is a stale snapshot. Call `mc.update_status()` before reading it (already in `poll_status()`). Always run `poll_status()` via `asyncio.to_thread()`.
- On app exit, use `cast.quit_app()` not `media_controller.stop()` — otherwise the TV keeps showing the Chromecast backdrop.
- On shutdown, detach chromecast callbacks (`set_status_callback(None)`) before calling `quit_app()` — pychromecast fires status updates that hit dead Textual widgets.
- Pipeline must `await chromecast.wait_for_connection()` before casting. Restored `"ready"` items start processing before discovery completes.
- Textual `Static.render()` text is not clickable. Any interactive element (button) must be a real `Button` widget inside `compose()`. Events bubble up to the `App` via `on_button_pressed`.
- Chromecast always outputs a 16:9 signal. Sending a 4:3 file results in pillarboxing. Encode to 16:9 (1024x576) with stretched 4:3 content so the user's HW squeeze restores correct proportions. This is handled by `_build_video_filter()`.
- YouTube videos often have black bars baked into the pixel data (pillarbox/letterbox). `_detect_crop()` handles this automatically. Without it, crop/scale operates on the bars as if they were content.

## Language

The user interface is in Italian (labels, section headers). Code, comments, and logs are in English.
