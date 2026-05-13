# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run
./run.sh                                          # starts the daemon (sources .env automatically; runs `python -m crt.daemon`)
crt-tui                                           # console script: Textual TUI client; talks HTTP to the daemon
CRT_DAEMON_URL=http://lodge.<tailnet>.ts.net:8765 crt-tui   # target a remote daemon over Tailscale

# Tests
source .venv/bin/activate
python -m pytest tests/ -v                        # full unit suite
python -m pytest tests/test_library_store.py -v   # single file
python -m pytest tests/test_pipeline.py::test_fetch_title -v  # single test

# Integration tests (require real Chromecast + internet)
source .env.integration                           # set TEST_CHROMECAST_NAME, TEST_VIDEO_URL_1, etc.
python -m pytest -m integration -v -s             # runs tests/test_integration.py

# Dependencies
pip install -r requirements.txt                   # inside .venv

# Flipper FAP (subproject in flipper_app/, opt-in — Flipper Zero remote)
cd flipper_app && ufbt              # build → dist/crt_remote.fap
cd flipper_app && ufbt launch       # flash + run on Flipper via USB
```

External dependency: `ffmpeg` must be installed (`brew install ffmpeg`).

## Architecture

Client/server. A headless **daemon** (`crt/daemon.py`, started by `./run.sh` or `crt-daemon`) does all the work — YouTube playlist sync, download, ffmpeg encoding to 4:3 PAL (768x576, 25fps), Chromecast casting. A **Textual TUI client** (`tui_client/`, run via `crt-tui`) is a thin HTTP consumer of the daemon's API (`crt/api.py`). The Flipper FAP + bridge are a parallel control surface — see [flipper_app/CLAUDE.md](flipper_app/CLAUDE.md).

**Data flow (in-daemon):** `SyncEngine` (`crt/sync_engine.py`) pulls the YouTube playlist into `LibraryStore` -> `PipelineWorker` (`crt/pipeline.py`) picks queued items -> yt-dlp downloads -> ffmpeg encodes -> `ChromecastManager.cast_url` (`crt/chromecast_mgr.py`) casts -> `MediaStatusListener` detects playback end -> `advance_cursor` finds the next item.

**Daemon threading model:** single asyncio event loop in `crt/daemon.py` hosts `PipelineWorker` + `PlayerCore` (`crt/player_core.py`) + FastAPI app (via uvicorn). Blocking work (yt-dlp, ffmpeg, every pychromecast call) goes through `asyncio.to_thread()`. The same uvicorn instance also serves encoded MP4 files to the Chromecast on `:8765`.

**Client/daemon contract:** TUI client (`tui_client/ui.py`) calls the daemon via `DaemonClient` (`tui_client/data_provider.py`, httpx). All `action_*` handlers wrap the blocking httpx call in `asyncio.to_thread` so Textual's loop stays responsive. The HTTP surface (`GET /status`, `GET /library/items`, `POST /control/{next,prev,toggle,stop,loop/toggle,sync,calibrate}`) is shared with the Flipper bridge — see "Production deployment".

**Configuration:** Environment variables loaded from `.env` via `run.sh`. Config constants in `config.py`. Key env vars:
- `CRT_SCALE_MODE` (`crop`|`pad`, default `crop`) — crop fills the frame by cutting edges, pad adds letterbox bars.
- `CRT_MARGIN_TOP`, `CRT_MARGIN_BOTTOM`, `CRT_MARGIN_LEFT`, `CRT_MARGIN_RIGHT` (pixels in logical 768×576 frame, default 0) — black borders to compensate for CRT overscan. Sum per axis is clamped to 50% of the frame. Press `Ctrl+T` in the TUI to cast a calibration grid. Changing any margin triggers re-encode (cache filename carries `_m{t}-{b}-{l}-{r}` suffix when non-zero).
- `CRT_AUTO_CROP` (`1`|`0`, default `1`) — when enabled, `_detect_crop()` analyzes the first 120 frames for baked-in black bars and applies a `crop=...` before encoding. Set to `0` to skip it entirely. Useful when cropdetect misfires on videos with dark content near the edges (opening titles, night scenes), which can silently delete real pixels before the margin step.
- `CRT_LOOP` (`1`|`0`, default `0`) — when enabled, the playlist loops back to the first item after the last item finishes. Togglable at runtime via `Ctrl+R` in the TUI (session-local, not persisted to state.json).

**Persistence:** Queue state (items, history, playback position) saved to `~/.local/share/crt-player/state.json` (configurable via `CRT_STATE_FILE`). Auto-saves every 60s + on exit. On reload, mid-processing items reset to `"queued"`; playing items become `"ready"` if encoded file exists (skips download+encode).

**Playlist model:** `done` is informational only — not a terminal state. The cast loop uses `advance_cursor()` on `LibraryStore` to find the next item by queue position, looping back if `loop_mode=True`. `prepare_for_play()` transitions `done`/`error` items to `ready` (cache hit) or `queued` (cache miss) before casting.

**Encoding pipeline:** `_detect_crop()` runs a cropdetect pre-pass (120 frames) to remove baked-in black bars from the source. `_build_video_filter()` then applies scale+crop or scale+pad based on `CRT_SCALE_MODE`, and stretches the result to 16:9 (1024x576) so the Chromecast doesn't add pillarboxing — the user's HW squeezes 16:9→4:3 restoring correct proportions.

**Encoding cache:** Cached files are named `{video_id}_pal_{scale_mode}.mp4` in TEMP_DIR (back-compat shape; when any `CRT_MARGIN_*` is non-zero the name gains a `_m{top}-{bottom}-{left}-{right}` suffix). Changing `CRT_SCALE_MODE` or any margin triggers re-encode. The filename helper lives in `crt/config.py` as `cached_encoded_filename()` so `crt/pipeline.py` and `crt/library_store.py` agree. Files live for `FILE_TTL_HOURS` (default 24h). `fetch_title()` returns `(title, video_id)`.

**Queue helpers:** `advance_cursor(loop)` returns the next item by position after the current cursor (playing or last done). `prepare_for_play(item)` transitions done/error to ready/queued based on cache. `can_move(item_id, direction)` returns bool for UI disabled state. `first_queued_after_cursor()` is used by the prefetch loop to skip items before the cursor.

## Production deployment (Lodge)

Daemon runs in Docker on a Raspberry Pi 5 ("Lodge") via the sibling `lodge-tools` repo at `services/crt-player/`. Ops/gotchas detail: [`../lodge-tools/services/crt-player/CLAUDE.md`](../lodge-tools/services/crt-player/CLAUDE.md). Integration spec: `../lodge-tools/docs/superpowers/specs/2026-05-11-crt-services-integration-design.md`.

**Container layout:** `network_mode: host` (mDNS Chromecast discovery + direct MP4 stream require multicast/no-NAT), `cpus: 2.0` + `cpu_shares: 256` (deprioritized vs OpenClaw/HA/Matrix — ffmpeg ~2x slower but system stays responsive). Bind mounts: `/opt/lodge/crt-player/{cache,state,secrets}` → `/data/{cache,state,secrets}`. **`secrets` mount is `:ro`** — token refresh is in-memory only (verified 2026-05-11; if upstream changes write path, symptom is 401s + "Read-only file system" in logs).

**Baked container env** (set in `docker/Dockerfile`, do NOT set in `.env`): `CRT_TEMP_DIR=/data/cache`, `CRT_STATE_FILE=/data/state/library.json`, `CRT_YT_CLIENT_SECRETS=/data/secrets/client_secrets.json`, `CRT_YT_TOKEN_FILE=/data/secrets/oauth_token.json`.

**Tailnet access from Mac:** `CRT_DAEMON_URL=http://lodge.<tailnet>.ts.net:8765 crt-tui`. Daemon listens on `0.0.0.0:8765` but has no UFW LAN rule since 2026-05-11 — external access only via Tailscale.

**HTTP control surface** consumed by TUI + Flipper bridge: `GET /status`, `GET /library/items`, `POST /control/{next,prev,toggle,stop,loop/toggle,sync,calibrate}`.

**OAuth bootstrap** (one-time, manual on Mac): `python -m crt.bootstrap` opens browser → writes `~/.local/share/crt-player/{client_secrets,oauth_token}.json`. `lodge crt-player install` validates these exist on the Mac then `scp`s them to `/opt/lodge/crt-player/secrets/` with `chmod 600`.

**Backup boundary** (restic on Pi, daily 04:00): only `secrets/` + `.env` + `docker-compose.yml`. `cache/` (encoded MP4) and `state/` (library DB) are regenerable from the YouTube playlist.

**Flipper command byte → HTTP endpoint** (in `../lodge-tools/services/crt-flipper-bridge/bridge.py` COMMAND_TABLE): `0x01`→next, `0x02`→prev, `0x03`→toggle, `0x04`→stop, `0x05`→loop/toggle, `0x06`→sync, `0x07`→calibrate. Bridge runs in Docker on the same Pi (host network + `/run/dbus` mount + `NET_ADMIN`). When you add/rename a `/control/*` endpoint in this repo, mirror the change in the bridge's COMMAND_TABLE. Bridge ops: [`../lodge-tools/services/crt-flipper-bridge/CLAUDE.md`](../lodge-tools/services/crt-flipper-bridge/CLAUDE.md).

## Integration Tests

Tests in `tests/test_integration.py` exercise the full stack with a real Chromecast and real YouTube URLs. They are opt-in (`pytest -m integration`) and skip automatically if env vars are absent.
- Configure via `source .env.integration` (gitignored). Key vars: `TEST_CHROMECAST_NAME`, `TEST_VIDEO_URL_1`, `TEST_VIDEO_URL_2`, `TEST_ENCODE_WAIT_S` (default 600s), `TEST_PLAYBACK_WAIT_S` (default 300s).
- `fetch_title()` always runs before the encode cache check — queue item status stays `"queued"` until the network call returns. YouTube rate-limits back-to-back `extract_info` calls; allow up to `encode_wait_s` for "pipeline started".
- Session-scoped `real_chromecast` fixture must recreate `asyncio.Event` objects per test (different event loops). Teardown must call `chromecast.stop()` — leaving the device playing causes `poll_status()` threads in the next test to saturate the asyncio thread pool, starving `asyncio.to_thread(fetch_title)` from getting a worker slot.
- After state transitions (cast switch, playback start), add a 3-5s settle pause + re-assert status. Catches late pychromecast status callbacks that race past the happy-path wait and regress item state.

## Debugging

Logs write to **both** stdout (so `docker logs lodge-crt-player` / Beszel capture them in prod) and `crt_cast.log` (overwritten each run, for local `./run.sh` debug). Set up in [crt/daemon.py:21-30](crt/daemon.py#L21-L30) via dual `StreamHandler(sys.stdout)` + `FileHandler`. Container `HEALTHCHECK` in [docker/Dockerfile](docker/Dockerfile) hits `/status` every 30s — surfaces as `healthy`/`unhealthy` in `docker ps` and Beszel.
Scan for Chromecasts: `python -c "import pychromecast, time; ccs, b = pychromecast.get_chromecasts(); time.sleep(10); b.stop_discovery(); [print(cc.name, cc.model_name) for cc in ccs]"`

## Testing

TUI client tests (`tests/test_ui.py`) use Textual's `app.run_test()` / Pilot API against `tui_client.ui.CRTCastApp` with a `MagicMock` `DaemonClient` (`mock_daemon_client` fixture in that file). The daemon is never started; the client only sees fixture return values for `fetch_library` / `fetch_status` / control calls.
- Daemon-side modules have their own test files: `test_library_store.py`, `test_pipeline.py`, `test_player_core.py`, `test_sync_engine.py`, `test_state_persistence.py`, `test_state_v2_migration.py`, `test_api.py`, `test_youtube_client.py`, `test_bootstrap.py`, `test_calibration.py`, `test_config.py`. `LibraryStore` is used as-is; `PipelineWorker` / `ChromecastManager` are mocked where needed.
- `test_tui_client_data_provider.py` covers the `DaemonClient` HTTP surface separately from the UI.
- Always `await pilot.pause()` after interactions (`press`, `click`, value changes) — handlers run asynchronously.
- Textual `ListView` is falsy when empty. Don't `assert widget` — use `query_one()` (raises `NoMatches` if absent).
- Config state (`MARGIN_*`, `SCALE_MODE`, `AUTO_CROP`) is mutated directly on the `config` module in tests. Each test file that does this MUST define an `autouse` `_restore_config` fixture that captures+restores these values around every test, otherwise state leaks across tests in the same pytest session (cross-file contamination). See `tests/test_pipeline.py` and `tests/test_calibration.py` for the pattern.

## Gotchas

- `_detect_crop()` can misidentify near-black scenes (dark opening titles, night shots) as letterbox and silently remove real pixels from the top/bottom of the source. Results in calibrated margins still appearing "too aggressive" on specific videos. Set `CRT_AUTO_CROP=0` to bypass.
- `action_calibrate` does not pause the `PipelineWorker` cast loop. If a prepared `ready` item exists and `_cast_enabled` is True, `run_cast()` can cast it right after the calibration pattern, overriding what's on screen. The double `_playing()` check catches items already in `casting`/`playing` but not items still `ready`. Known limitation; mitigation would be to temporarily toggle `pipeline._cast_enabled` around the calibration action.

- Calibration pattern in `calibration.py` uses only `drawbox`/`drawgrid`, no `drawtext` — the Homebrew ffmpeg build here lacks libfreetype. Numeric margin values are surfaced to the user via a TUI toast in `action_calibrate` rather than overlaid on-screen.
- Textual `Binding` with letter keys (`s`, `p`, `q`) won't show in Footer and conflict with Input widget text entry. Use `ctrl+` combos with `priority=True` for global bindings — see `tui_client/ui.py` `BINDINGS` list.
- All pychromecast commands (stop, pause, play, volume) can raise `RequestTimeout`/`RequestFailed`. Wrap in `_safe_cmd` in `chromecast_mgr.py`.
- pychromecast commands (pause, seek, stop) block for up to 10s on timeout. The daemon always calls them via `asyncio.to_thread()` from async handlers (`crt/player_core.py`, `crt/pipeline.py`). The TUI client never calls pychromecast — it goes through `/control/*` HTTP.
- `pause_or_resume()` uses cached `self.player_state` — don't re-call `poll_status()` before issuing a command, it adds a redundant blocking round-trip.
- pychromecast status callbacks fire from a background thread — state can change between checks. Use `asyncio.Event` not polling for playback end detection.
- pychromecast `MediaStatusListener` only fires on state changes, not position updates. Use `set_interval` + `poll_status()` for playback progress.
- `media_controller.status.current_time` is a stale snapshot. Call `mc.update_status()` before reading it (already in `poll_status()`). Always run `poll_status()` via `asyncio.to_thread()`.
- On app exit, use `cast.quit_app()` not `media_controller.stop()` — otherwise the TV keeps showing the Chromecast backdrop.
- On shutdown, detach chromecast callbacks (`set_status_callback(None)`) before calling `quit_app()` — pychromecast fires status updates that hit dead Textual widgets.
- Pipeline must `await chromecast.wait_for_connection()` before casting. Restored `"ready"` items start processing before discovery completes.
- Textual `Static.render()` text is not clickable (applies to `tui_client/ui.py`). Any interactive element must be a real `Button` widget inside `compose()`; events bubble up via `on_button_pressed`.
- Chromecast always outputs a 16:9 signal. Sending a 4:3 file results in pillarboxing. Encode to 16:9 (1024x576) with stretched 4:3 content so the user's HW squeeze restores correct proportions. This is handled by `_build_video_filter()`.
- YouTube videos often have black bars baked into the pixel data (pillarbox/letterbox). `_detect_crop()` handles this automatically. Without it, crop/scale operates on the bars as if they were content.
- `active_item()` returns the first item matching `ACTIVE_STATUSES` (which includes "ready"). To find the playing item specifically, use `next((i for i in queue.items if i.status == "playing"), None)` — avoids picking a "ready" item that sits earlier in the queue.
- `cast_url` / `block_until_active()` may return while the player is still in "LOADING" state. A subsequent `seek_to` call can be silently ignored. Pass `current_time=position` to `play_media` instead — starts at the right position from the initial load request.
- pychromecast `MediaStatus.idle_reason` distinguishes natural end (`"FINISHED"`) from transition-IDLE (`"CANCELLED"`/`"INTERRUPTED"` fired when loading new media). Don't treat PLAYING→IDLE as playback end without checking the reason — a late IDLE from the previous item can arrive after `reset_playback_ended()` and falsely end the new item.
- Textual `Static` with overridden `render()` doesn't update the widget's internal `_content` size cache (applies to `tui_client/ui.py`). Use `watch_*` reactive methods that call `self.update(...)` instead — otherwise title changes may render from stale content and `height: auto` won't adapt.
- Running scripts standalone (outside `./run.sh`) needs `set -a; source .env; set +a` before `python ...` — the `.env` file has no `export` prefix, so a plain `source` only sets shell-local vars and Python won't see them.
- The `flipper_app/` Flipper FAP is its own subproject: `ufbt` SDK, no Python tests. Spec: `docs/superpowers/specs/2026-05-10-flipper-remote-design.md`. Uses a forked Serial profile (Momentum FW source in `flipper_app/libs/serial_profile.{c,h}`) with custom `mac_xor` to bypass the firmware's BtSrv RPC handler — without that, button TX is silently dropped. Pairs with the bridge in `lodge-tools/services/crt-flipper-bridge/`. Detailed subproject guidance: [flipper_app/CLAUDE.md](flipper_app/CLAUDE.md).
- Don't add `sources=` to a Flipper `application.fam` if you have subdirs: ufbt auto-discovery is recursive, and explicit globs (`["*.c", "libs/*.c"]`) cause duplicate-definition link errors.

## Language

The user interface is in Italian (labels, section headers). Code, comments, and logs are in English.
