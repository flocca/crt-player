# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run
./run.sh                                          # starts the TUI app (sources .env automatically)

# Tests
source .venv/bin/activate
python -m pytest tests/ -v                        # full unit suite (50 tests)
python -m pytest tests/test_queue_manager.py -v   # single file
python -m pytest tests/test_pipeline.py::test_fetch_title -v  # single test

# Integration tests (require real Chromecast + internet)
source .env.integration                           # set TEST_CHROMECAST_NAME, TEST_VIDEO_URL_1, etc.
python -m pytest -m integration -v -s             # runs tests/test_integration.py

# Dependencies
pip install -r requirements.txt                   # inside .venv
```

External dependency: `ffmpeg` must be installed (`brew install ffmpeg`).

## Architecture

TUI app (Textual) that downloads YouTube videos, converts them to 4:3 PAL (768x576, 25fps), and casts to a Chromecast via pychromecast.

**Data flow:** User adds URL in TUI -> QueueManager stores item -> PipelineWorker picks it up -> yt-dlp downloads -> ffmpeg encodes -> pychromecast casts -> MediaStatusListener detects playback end -> next item.

**Threading model:** Textual runs the asyncio event loop. The pipeline worker runs as an asyncio task in that loop. Blocking operations (yt-dlp download, pychromecast calls) use `asyncio.to_thread()`. A minimal FastAPI/uvicorn server runs in a separate daemon thread solely to serve MP4 files to the Chromecast.

**Key integration point:** Pipeline callbacks can fire from both the main asyncio thread and worker threads. The TUI's `_safe_call` method handles this by trying `call_from_thread` first and falling back to a direct call.

**Configuration:** Environment variables loaded from `.env` via `run.sh`. Config constants in `config.py`. Key env vars:
- `CRT_SCALE_MODE` (`crop`|`pad`, default `crop`) — crop fills the frame by cutting edges, pad adds letterbox bars.
- `CRT_MARGIN_TOP`, `CRT_MARGIN_BOTTOM`, `CRT_MARGIN_LEFT`, `CRT_MARGIN_RIGHT` (pixels in logical 768×576 frame, default 0) — black borders to compensate for CRT overscan. Sum per axis is clamped to 50% of the frame. Press `Ctrl+T` in the TUI to cast a calibration grid. Changing any margin triggers re-encode (cache filename carries `_m{t}-{b}-{l}-{r}` suffix when non-zero).
- `CRT_AUTO_CROP` (`1`|`0`, default `1`) — when enabled, `_detect_crop()` analyzes the first 120 frames for baked-in black bars and applies a `crop=...` before encoding. Set to `0` to skip it entirely. Useful when cropdetect misfires on videos with dark content near the edges (opening titles, night scenes), which can silently delete real pixels before the margin step.

**Persistence:** Queue state (items, history, playback position) saved to `~/.local/share/crt-player/state.json` (configurable via `CRT_STATE_FILE`). Auto-saves every 60s + on exit. On reload, mid-processing items reset to `"queued"`; playing items become `"ready"` if encoded file exists (skips download+encode).

**Encoding pipeline:** `_detect_crop()` runs a cropdetect pre-pass (120 frames) to remove baked-in black bars from the source. `_build_video_filter()` then applies scale+crop or scale+pad based on `CRT_SCALE_MODE`, and stretches the result to 16:9 (1024x576) so the Chromecast doesn't add pillarboxing — the user's HW squeezes 16:9→4:3 restoring correct proportions.

**Encoding cache:** Cached files are named `{video_id}_pal_{scale_mode}.mp4` in TEMP_DIR (back-compat shape; when any `CRT_MARGIN_*` is non-zero the name gains a `_m{top}-{bottom}-{left}-{right}` suffix). Changing `CRT_SCALE_MODE` or any margin triggers re-encode. The filename helper lives in `config.py` as `cached_encoded_filename()` so `pipeline.py` and `queue_manager.py` agree. Files live for `FILE_TTL_HOURS` (default 24h). `fetch_title()` returns `(title, video_id)`.

## Integration Tests

Tests in `tests/test_integration.py` exercise the full stack with a real Chromecast and real YouTube URLs. They are opt-in (`pytest -m integration`) and skip automatically if env vars are absent.
- Configure via `source .env.integration` (gitignored). Key vars: `TEST_CHROMECAST_NAME`, `TEST_VIDEO_URL_1`, `TEST_VIDEO_URL_2`, `TEST_ENCODE_WAIT_S` (default 600s), `TEST_PLAYBACK_WAIT_S` (default 300s).
- `fetch_title()` always runs before the encode cache check — queue item status stays `"queued"` until the network call returns. YouTube rate-limits back-to-back `extract_info` calls; allow up to `encode_wait_s` for "pipeline started".
- Session-scoped `real_chromecast` fixture must recreate `asyncio.Event` objects per test (different event loops). Teardown must call `chromecast.stop()` — leaving the device playing causes `poll_status()` threads in the next test to saturate the asyncio thread pool, starving `asyncio.to_thread(fetch_title)` from getting a worker slot.
- After state transitions (cast switch, playback start), add a 3-5s settle pause + re-assert status. Catches late pychromecast status callbacks that race past the happy-path wait and regress item state.

## Debugging

Logs write to `crt_cast.log` (overwritten each run). stderr is redirected there too.
Scan for Chromecasts: `python -c "import pychromecast, time; ccs, b = pychromecast.get_chromecasts(); time.sleep(10); b.stop_discovery(); [print(cc.name, cc.model_name) for cc in ccs]"`

## Testing

TUI tests use Textual's `app.run_test()` / Pilot API (`tests/test_ui.py`). Shared fixtures in `tests/conftest.py`.
- `QueueManager` is used as-is (pure data). `PipelineWorker` and `ChromecastManager` are `MagicMock` with `AsyncMock` for async methods — this neutralizes `on_mount`'s infinite-loop tasks.
- `on_mount` calls `_refresh_all` when `queue.items` is non-empty and focuses the queue list; otherwise focuses the URL input. `wake()` only fires if `next_pending()` exists. To test UI state for "playing"/"ready" items without pre-populating the queue, call `app._refresh_all()` manually after mount.
- Always `await pilot.pause()` after interactions (`press`, `click`, value changes) — handlers run asynchronously.
- Textual `ListView` is falsy when empty. Don't `assert widget` — use `query_one()` (raises `NoMatches` if absent).
- Config state (`MARGIN_*`, `SCALE_MODE`, `AUTO_CROP`) is mutated directly on the `config` module in tests. Each test file that does this MUST define an `autouse` `_restore_config` fixture that captures+restores these values around every test, otherwise state leaks across tests in the same pytest session (cross-file contamination). See `tests/test_pipeline.py` and `tests/test_calibration.py` for the pattern.

## Gotchas

- `_detect_crop()` can misidentify near-black scenes (dark opening titles, night shots) as letterbox and silently remove real pixels from the top/bottom of the source. Results in calibrated margins still appearing "too aggressive" on specific videos. Set `CRT_AUTO_CROP=0` to bypass.
- `action_calibrate` does not pause the `PipelineWorker` cast loop. If a prepared `ready` item exists and `_cast_enabled` is True, `run_cast()` can cast it right after the calibration pattern, overriding what's on screen. The double `_playing()` check catches items already in `casting`/`playing` but not items still `ready`. Known limitation; mitigation would be to temporarily toggle `pipeline._cast_enabled` around the calibration action.

- Calibration pattern in `calibration.py` uses only `drawbox`/`drawgrid`, no `drawtext` — the Homebrew ffmpeg build here lacks libfreetype. Numeric margin values are surfaced to the user via a TUI toast in `action_calibrate` rather than overlaid on-screen.
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
- `active_item()` returns the first item matching `ACTIVE_STATUSES` (which includes "ready"). To find the playing item specifically, use `next((i for i in queue.items if i.status == "playing"), None)` — avoids picking a "ready" item that sits earlier in the queue.
- `cast_url` / `block_until_active()` may return while the player is still in "LOADING" state. A subsequent `seek_to` call can be silently ignored. Pass `current_time=position` to `play_media` instead — starts at the right position from the initial load request.
- pychromecast `MediaStatus.idle_reason` distinguishes natural end (`"FINISHED"`) from transition-IDLE (`"CANCELLED"`/`"INTERRUPTED"` fired when loading new media). Don't treat PLAYING→IDLE as playback end without checking the reason — a late IDLE from the previous item can arrive after `reset_playback_ended()` and falsely end the new item.
- Textual `Static` with overridden `render()` doesn't update the widget's internal `_content` size cache. Use `watch_*` reactive methods that call `self.update(...)` instead — otherwise title changes may render from stale content and `height: auto` won't adapt.

## Language

The user interface is in Italian (labels, section headers). Code, comments, and logs are in English.
