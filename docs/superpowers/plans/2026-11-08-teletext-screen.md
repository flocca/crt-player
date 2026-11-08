# Teletext Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pause menu and the black screen after Stop with one teletext-style screen (paused and stopped states), with the muted preview of the highlighted video behind it.

**Architecture:** A pure model `Screen` (`crt/screen.py`) replaces `PauseMenu` and is owned by `PlayerCore` in both `paused` and `idle`; the daemon sends its whole `view()` to the page as a `screen` message and reinterprets the Flipper endpoints while it is shown. The pipeline adds an audio-less copy of every encoded file for previews. The receiver page renders a 40x18 teletext grid in the Bedstead font over a second `<video>` (preview) or the paused main video, with a snow canvas for videos not ready.

**Tech Stack:** Python 3 asyncio, FastAPI/uvicorn, pytest + pytest-asyncio, ffmpeg, static HTML/ES5-ish JS for Chrome 90 (Chromecast Ultra), Textual (TUI).

**Spec:** `docs/superpowers/specs/2026-11-08-teletext-screen-design.md` (Italian). Read it whole before any task; this plan only records the decisions the spec leaves to the builder and the exact names every task shares.

## Global Constraints

- Language: UI copy on the TV in Italian, exactly as listed below; code, comments and logs in English (repo CLAUDE.md "Language").
- `PlayerCore` transport commands never suspend (repo CLAUDE.md gotcha). Only the deletion itself (`_delete_video`) may `await` real work, after the screen state is already updated. Timers use `loop.call_later`.
- Every new SSE subscription gets the whole state (`_on_receiver_connect`), screen included.
- Page runs on Chrome 90: no `:has()`, container queries, `structuredClone`, top-level `await`. No CSS animation or transition on or over the videos. Everything drawn sits in the 75%-wide box with `transform: scaleX(1.3333)`.
- API responses add fields, never remove them (`crt/api.py` `_ack`).
- No change to the Flipper FAP or the bridge (`COMMAND_TABLE` unchanged).
- Copy (verbatim): commands `Riprendi`, `Riproduci`, `Elimina`, `Stop`, `Calibra`, `Loop`, `Sync`, `Spegni`; `Eliminare dalla playlist? OK = sì`; `Non ancora pronto`; `Fine della coda`; `Video rimosso dalla playlist`; `La playlist è vuota`; `Errore nel video`; `Loop attivo`; `Loop disattivato`; `Sync avviato`; `Sync non disponibile`; `Calibrazione non pronta`; row prefixes `In coda · `, `Download · `, `Codifica · `, `Errore nel video · `; bottom line `Sync hh:mm` / `Sync mai` / `Sync errore`, `Loop attivo`, `OK = <label>`; header `P100`, `CRT QUEUE`, `n/m`, date `dom 08 nov 21:34:05` (days `dom lun mar mer gio ven sab`, months `gen feb mar apr mag giu lug ago set ott nov dic`); row 3 `PLAYLIST`, `<n> video`; paused row 4 `▌▌ mm:ss / mm:ss` + 14-column bar of `▓`/`░`.
- Numbers: 10 video rows per subpage; grid 40 columns × 18 rows; veil 70% awake, 90% night; night after 300 s without keys; arrival notice 5 s on row 4; short notices 2 s on row 18 (`NOTICE_S`, existing); preview window 20 s; snow canvas 160×90, one frame every 83 ms, grey levels 40-160; night text at 35% opacity, drift 3 px every 30 s; extra left margin 1.5%.
- Colours (page): white `#f0f0f0`, yellow `#f0f000`, cyan `#00f0f0`, green `#00e000`, magenta `#f000f0`, red `#f00000`, blue `#2020ff`, grey `#a0a0a0`, black `#000`. Commands: resume green, delete red, stop yellow, calibrate cyan, loop magenta, sync blue, power white; selected = black on its colour, others = colour on black. Rows: ready white, done grey, preparing cyan, error magenta, selected black on yellow, awaiting delete confirmation black on red. Header: `P100` yellow, `CRT QUEUE` white, `n/m` yellow, date white; mosaic line blue `▄`×40; row 3 cyan; row 18 green, `Sync errore` and `Errore nel video` magenta, the delete confirmation red.
- Bedstead (CC0) has every glyph used (`▶ ○ ✓ … ! ▌ ▓ ░ ▄ ·`, Italian accents): verified with fontTools on 2026-11-08.

## Review Focus

1. **Progress redraw storm**: the pipeline notifies at every percent; a redraw must not wake the night phase, re-arm its timer, or make the page reload the preview. → Task 4 test `test_refresh_during_night_keeps_night_and_timer`; Task 6 page rule (preview reloads only when `preview.url` changes).
2. **The confirmed video leaves the library before OK** (sync removes it): OK must delete nothing. → Task 2 test `test_confirm_dropped_when_video_leaves`, Task 3 test `test_ok_after_confirmed_video_removed_deletes_nothing`.
3. **Playlist emptied while the screen is up** (sync removes everything): no selection, only the player commands, the command index clamped, OK runs the selected player command and never touches a video. → Task 2 tests `test_empty_library_view`, `test_command_index_clamped_when_commands_shrink`; Task 4 test `test_ok_on_empty_library_runs_player_command`.
4. **Page reconnects in night or during a confirmation**: the resent view carries `night` and the confirmation. → Task 4 test `test_reconnect_resends_night_and_confirm`.
5. **A crash mid-remux leaves a partial preview**: it must never count as present. → Task 1 test `test_make_preview_failure_leaves_no_file` (temp name + `os.replace`).

---

### Task 1: Audio-less preview copies

**Files:**
- Modify: `crt/config.py` (after `cached_encoded_filename`)
- Modify: `crt/library_store.py` (`QueueItem`)
- Modify: `crt/pipeline.py` (`make_preview`, `PipelineWorker._prepare_one`, new `PipelineWorker.backfill_previews`)
- Modify: `crt/daemon.py` (`cleanup_temp_files`, task list)
- Modify: `crt/sync_engine.py` (`_delete_cache_files`)
- Modify: `crt/player_core.py` (`_delete_local`)
- Test: `tests/test_config.py`, `tests/test_pipeline.py`, `tests/test_daemon.py`, `tests/test_sync_engine.py`, `tests/test_player_core.py`

**Interfaces:**
- Produces: `config.preview_filename(encoded_filename: str) -> str` (`"X.mp4"` → `"X_noaudio.mp4"`); `QueueItem.preview_filename() -> str | None` (None without `filename`); `QueueItem.preview_present() -> bool`; `async def make_preview(encoded_path: str) -> str | None` in `crt/pipeline.py`; `async PipelineWorker.backfill_previews() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
def test_preview_filename_derives_from_encoded_name():
    assert config.preview_filename("abc_pal_crop_m35-30-25-35.mp4") == "abc_pal_crop_m35-30-25-35_noaudio.mp4"

# tests/test_pipeline.py
async def test_make_preview_remuxes_video_only(tmp_path):
    # patch crt.pipeline.asyncio.create_subprocess_exec with a fake that writes args[-1] and returns returncode 0
    # assert the command has "-map", "0:v", "-c", "copy", "-an"; the final file is tmp_path/"X_noaudio.mp4";
    # the command's output path is NOT the final name (temp file), and no temp file remains afterwards
async def test_make_preview_failure_leaves_no_file(tmp_path):
    # fake process writes a partial output and returns returncode 1
    # assert make_preview(...) is None and neither the final nor the temp file exists
async def test_prepare_one_makes_preview_after_ready(monkeypatch, tmp_path):
    # fake fetch/download/encode as in test_cancel_prepare_for_*; patch pl.make_preview with an AsyncMock
    # assert item.status == "ready" and make_preview was awaited with the encoded path
async def test_prepare_one_cache_hit_makes_missing_preview(monkeypatch, tmp_path):
    # encoded file already present, preview missing -> make_preview awaited once
async def test_backfill_previews_only_for_ready_items_without_copy(monkeypatch, tmp_path):
    # three items: ready without copy, ready with copy, queued -> make_preview awaited once, for the first

# tests/test_daemon.py
def test_cleanup_temp_files_keeps_preview_of_library_items(monkeypatch, tmp_path):
    # old "<encoded>_noaudio.mp4" of a library item survives; an old orphan "_noaudio.mp4" is removed

# tests/test_sync_engine.py
def test_remove_deletes_preview_copy(tmp_path, monkeypatch):
    # after _remove_item the "_noaudio.mp4" next to item.filename is gone

# tests/test_player_core.py
async def test_delete_local_removes_preview_copy():
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py tests/test_pipeline.py tests/test_daemon.py tests/test_sync_engine.py tests/test_player_core.py -k "preview" -v`
Expected: FAIL (`preview_filename` / `make_preview` not defined).

- [ ] **Step 3: Implement**

- `make_preview`: run `ffmpeg -v error -y -i <encoded> -map 0:v -c copy -an -movflags +faststart <final>.part.mp4` via `asyncio.create_subprocess_exec`, then `os.replace` to the final name on returncode 0; on failure log a warning, unlink the temp file, return None. Returns the final path.
- `_prepare_one`: after `item.status = "ready"` + `notify()` in both the cache-hit path and the encode path, `await make_preview(...)` if `not item.preview_present()`, then `notify()` again (the screen goes from black to preview). A preview failure never changes the item's status.
- `backfill_previews`: for each item with `cache_file_present()` and not `preview_present()`, sequentially `await make_preview`, `notify()` after each. The daemon starts it as a one-shot task (`name="preview_backfill"`) next to `prepare_calibration`.
- Cleanup: `cleanup_temp_files` adds `preview_filename()` of `item.filename` and of the canonical encoded name to `in_use`; `SyncEngine._delete_cache_files` and `PlayerCore._delete_local` unlink the preview copy next to the encoded file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crt/config.py crt/library_store.py crt/pipeline.py crt/daemon.py crt/sync_engine.py crt/player_core.py tests/
git commit -m "feat: copia senza audio di ogni video per le anteprime"
```

---

### Task 2: `Screen` model

**Files:**
- Create: `crt/screen.py`
- Test: `tests/test_screen.py`

**Interfaces:**
- Consumes: `QueueItem.cache_file_present()`, `QueueItem.preview_present()` (Task 1).
- Produces (all in `crt/screen.py`):
  - Constants: `ROWS_PER_PAGE = 10`, `PREVIEW_WINDOW_S = 20.0`, `LABELS = {"resume": "Riprendi", "play": "Riproduci", "delete": "Elimina", "stop": "Stop", "calibrate": "Calibra", "loop": "Loop", "sync": "Sync", "power": "Spegni"}`, `CONFIRM_DELETE`, `NOT_READY_NOTICE`, `END_OF_QUEUE_NOTICE`, `REMOVED_NOTICE`, `EMPTY_PLAYLIST` (copy from Global Constraints).
  - `def preview_window(position: float) -> tuple[float, float]`: `(position - 20, position)` when `position >= 20`, else `(0.0, 20.0)`.
  - `def row_for(item: QueueItem, loaded_video_id: str | None) -> dict` → `{"num", "symbol", "text", "right", "kind"}` (num filled by `view`). Symbol: `▶` loaded; `○` status in `ready/casting/playing/paused`; `✓` done; `…` queued/downloading/encoding; `!` error. Text: title, or prefixed for queued/downloading/encoding/error. Right: `"43%"` for downloading/encoding (int of `progress`), else `mm:ss`/`h:mm:ss` of `playback_position` when > 0, else `""`. Kind: `"ready"`, `"done"`, `"preparing"`, `"error"` (loaded counts as ready).
  - `class Screen` with attributes `selected_video_id: str | None`, `loaded_video_id: str | None`, `command_index: int`, `confirm_video_id: str | None`, `flash: str | None`, `flash_kind: str` (`"info" | "error"`), `arrival: str | None`, `night: bool`; methods:
    - `__init__(self, selected_video_id: str | None, loaded_video_id: str | None = None)`
    - `commands(self, items: list[QueueItem]) -> list[str]`: `[]` video commands when `items` is empty; else `["resume", "delete"] + (["stop"] if loaded_video_id else [])`; then always `["calibrate", "loop", "sync", "power"]`.
    - `fix_selection(self, items, cursor_video_id) -> None`: a selection not in `items` falls back to the cursor, else the first item, else None; a `confirm_video_id` not in `items` becomes None.
    - `move(self, items, delta: int) -> None`: fix, then step with wrap; resets `command_index` to 0 and clears `confirm_video_id`, `flash`.
    - `move_command(self, items, delta: int) -> None`: wraps over `commands(items)`; clears `confirm_video_id`, `flash`.
    - `selected_command(self, items) -> str` (`command_index` is clamped to the current `commands(items)`: Stop disappears on Stop, video commands disappear on an empty library)
    - `resume_label(self, items) -> str`: `"Riprendi"` when the selection is the loaded video or has `playback_position > 0`, else `"Riproduci"`.
    - `view(self, items, cursor_video_id, *, loop: bool, sync_at: str | None, sync_error: bool, preview_url: Callable[[QueueItem], str | None]) -> dict` (calls `fix_selection` first). Keys: `state` (`"paused"` if `loaded_video_id` else `"stopped"`), `count`, `page`, `pages` (≥1), `rows` (≤10, the subpage of the selection; each row dict + `"selected"`, `"confirm"`), `paused_position` (loaded item's `playback_position`, or None), `status` (row-4 text: `arrival`, or `EMPTY_PLAYLIST` when empty, else None), `commands` (two lists of `{"key", "label", "selected"}`: video commands, player commands; `"resume"` gets `resume_label`), `flash`, `flash_kind` (`"confirm"` while `confirm_video_id` is set, with `flash = CONFIRM_DELETE`), `help` (`"OK = " + label of the selected command`), `loop`, `sync_at`, `sync_error`, `background` and `preview` (below), `night`.
    - Background: no items → `"black"`; selection is the loaded video → `"frame"`; selection status in queued/downloading/encoding/error → `"snow"`; `preview_url(item)` not None → `"preview"` with `preview = {"url": ..., "start": s, "end": e}` from `preview_window(playback_position)`; else `"black"`. `preview` is None unless background is `"preview"`.

- [ ] **Step 1: Write the failing tests** (`tests/test_screen.py`, items built like `tests/test_pause_menu.py`, TEMP_DIR on `tmp_path`)

```python
def test_rows_symbols_text_right_and_kind()          # one item per status: ▶/○/✓/…/!; "Download · T" with right "43%"; "In coda · T"; done kind "done"; position 3725 -> "1:02:05"
def test_subpage_follows_selection()                 # 19 items, selection #12 -> page 2, pages 2, rows are items 11-19, num 11..19
def test_move_wraps_and_resets_command()             # down from last -> first; command_index back to 0
def test_move_command_wraps_over_two_rows()          # paused: resume,delete,stop,calibrate,loop,sync,power; right from power -> resume
def test_commands_without_stop_when_stopped()
def test_resume_label()                              # loaded -> Riprendi; position>0 -> Riprendi; 0 -> Riproduci
def test_confirm_view()                              # confirm_video_id set -> that row "confirm": True, flash CONFIRM_DELETE, flash_kind "confirm"
def test_confirm_dropped_when_video_leaves()         # confirm id removed from items -> view clears it
def test_selection_falls_back_to_cursor_then_first()
def test_empty_library_view()                        # rows [], status EMPTY_PLAYLIST, commands[0] == [], background "black", help "OK = Calibra"
def test_background_kinds()                          # frame / snow / preview (with window) / black (ready, no copy)
def test_preview_window()                            # 0 -> (0,20); 12 -> (0,20); 95 -> (75,95)
def test_paused_position_and_state()
def test_command_index_clamped_when_commands_shrink()  # index on "power" (6) while paused, loaded cleared -> selected_command still valid; empty items -> index within player commands
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_screen.py -v`
Expected: FAIL (`ModuleNotFoundError: crt.screen`).

- [ ] **Step 3: Implement `crt/screen.py`** per the Interfaces block. Pure logic, no I/O beyond the two `QueueItem` file checks.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_screen.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crt/screen.py tests/test_screen.py
git commit -m "feat: modello della schermata televideo"
```

---

### Task 3: Screen in pause (replaces `PauseMenu`)

**Files:**
- Modify: `crt/player_core.py`
- Modify: `crt/receiver.py` (module docstring: messages)
- Modify: `crt/daemon.py` (wiring: `refresh_screen`, `sync_info`)
- Delete: `crt/pause_menu.py`, `tests/test_pause_menu.py`
- Test: `tests/test_player_core.py`

**Interfaces:**
- Consumes: `Screen`, constants and `preview_window` (Task 2); `QueueItem.preview_filename()` (Task 1).
- Produces on `PlayerCore`:
  - `screen: Screen | None` (replaces `menu`), `_send_screen(view: dict | None = None) -> None` (sends `{"type": "screen", "view": view}`), `refresh_screen() -> None` (replaces `refresh_menu`; sends only when the view changed), `_flash(text: str, kind: str = "info") -> None` (2 s, `"error"` stays until a key), `sync_info: Callable[[], tuple[str | None, bool]] | None` (daemon sets `lambda: (sync_engine.last_sync_at, sync_engine.state == "degraded")`).
  - `toggle_loop() -> ActionResult`, `request_sync() -> ActionResult` (flash the existing notices when the screen is shown; used by the screen and, in Task 5, by the API).
  - `power_off() -> ActionResult`: snapshot + `ready` like `_close_if_idle`, `screen = None`, `_loaded_video_id = None`, state idle, `_inactive_since = None`, then `loop.create_task(asyncio.to_thread(self.chromecast.quit_app))` (no await).
  - `async _delete_video(video_id: str) -> None`: if it is the loaded video, `await self.stop()` first; remove the item (`_delete_local`), move the cursor to the successor if it was the cursor (same rule as `SyncEngine._remove_item`), remote YouTube delete as `delete_current` does; then `_send_screen()`. `delete_current()` keeps its behaviour (immediate, cursor) and shares the removal code.
- Message protocol: daemon → page `screen{view}` replaces `menu{view}`; `blank{}` now means only "unload the main video and stop calibration" (it no longer hides anything else). `_resume` and `_cast` send `screen` with `view: None`.

- [ ] **Step 1: Rewrite the pause tests first** (in `tests/test_player_core.py`: rename helper `menus` → `screens`, drop `from crt.pause_menu import Entry`; replace the tests that assert the old ring/seek-in-pause)

```python
async def test_pause_opens_screen_on_loaded_video()            # view state "paused", selected row ▶, commands[0] labels Riprendi/Elimina/Stop, background "frame"
async def test_up_down_move_highlight_with_wrap_while_paused()
async def test_left_right_move_commands_while_paused()          # seek_relative(-15)/(+30) -> command_index -1/+1, no "seek" message, saved position unchanged
async def test_ok_on_resume_resumes()                           # "play" sent, screen view None, state playing
async def test_ok_on_other_ready_video_plays_it_from_its_position()
async def test_ok_on_unready_video_flashes_not_ready()          # flash "Non ancora pronto", cursor unchanged
async def test_delete_confirm_then_ok_deletes_highlighted_other_video()   # paused video stays paused and loaded
async def test_delete_confirm_on_paused_video_stops_then_deletes()        # state idle, stopped screen
async def test_any_key_cancels_confirm_and_does_nothing_else()  # next during confirm: confirm cleared, selection unchanged
async def test_ok_after_confirmed_video_removed_deletes_nothing()
async def test_stop_command_goes_to_stopped_screen_same_row()   # "blank" sent, then screen state "stopped", selection unchanged, Riprendi
async def test_power_command_saves_and_quits_app()              # quit_app called (after a loop tick), screen None, state idle
async def test_loop_and_sync_commands_flash()
async def test_media_error_flash_stays_until_a_key()
async def test_refresh_screen_sends_only_on_change()
async def test_view_carries_preview_url_and_sync_info()         # preview url ends with "_noaudio.mp4"; sync_at from sync_info
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_player_core.py -v`
Expected: the new tests FAIL; delete `tests/test_pause_menu.py`.

- [ ] **Step 3: Implement in `crt/player_core.py`**

- `_pause(open_menu=True)` creates `Screen(loaded, loaded)`.
- `next`/`prev`/`toggle`/`seek_relative`: when `self.screen is not None and self.state == "paused"` → screen handling (this guard is widened to idle in Task 4). Order inside each: if `confirm_video_id` is set and the key is not OK → clear it, send, return `_DID_ACTION`.
- `toggle` on the screen runs `selected_command`: `resume` (loaded → `_resume`; other playable → `_cast`; not playable → `_flash(NOT_READY_NOTICE)`, `_NO_PLAYABLE`); `delete` (first OK sets `confirm_video_id = selected`, second OK → `await self._delete_video(...)` only if that id is still in the library); `stop`; `calibrate`; `loop`; `sync`; `power`.
- The view's `preview_url` callback: `self._media_url(item.preview_filename())` when `item.preview_present()`, else None.
- `calibrate()` keeps `self.screen` (does not clear it) and `_end_calibration()` returning to `paused` re-sends the kept screen (creating `Screen(loaded, loaded)` if none).
- `_on_receiver_connect` sends `screen` instead of `menu`; `_on_media_error` uses `_flash(MEDIA_ERROR_NOTICE, "error")`.
- Daemon: `pipeline.set_update_callback(... player.refresh_screen)`, `on_synced=player.refresh_screen`, `player.sync_info = ...`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all pass (stopped-state tests still asserting the old idle behaviour are rewritten in Task 4; mark none as skipped).

- [ ] **Step 5: Commit**

```bash
git add crt/ tests/
git rm crt/pause_menu.py tests/test_pause_menu.py
git commit -m "feat: schermata televideo in pausa al posto del menu ad anello"
```

---

### Task 4: Stopped screen, closed page, night phase

**Files:**
- Modify: `crt/player_core.py`
- Test: `tests/test_player_core.py`

**Interfaces:**
- Consumes: Task 3 names.
- Produces on `PlayerCore`: constants `ARRIVAL_NOTICE_S = 5.0`, `NIGHT_AFTER_S = 300.0`; `ActionResult` reasons `"already_stopped"` (`_ALREADY_STOPPED = ActionResult(False, "already_stopped")`) and `"woke"` (`_WOKE = ActionResult(True, "woke")`), documented in the `ActionResult` docstring; `_show_stopped(*, highlight: str | None, arrival: str | None = None) -> None` (send `blank`, `screen = Screen(highlight, None)` with `arrival`, schedule its 5 s clear, `_touch()`, send, and `_start_launch()` when the page is not ready); `_touch() -> None` (`_inactive_since = clock()` when idle/paused; cancel and re-arm the night timer with `call_later(NIGHT_AFTER_S, self._enter_night)`); `_wake_if_night() -> bool` (True if the screen was asleep: clears `night`, `_touch()`, sends); properties `screen_state -> str` (`"paused"` if paused with a screen, `"stopped"` if idle with a screen, else `"none"`) and `selected_video_id -> str | None`.

- [ ] **Step 1: Write the failing tests** (replace `test_stop_blanks_page_*`, `test_toggle_when_idle_*`, `test_toggle_idle_*`, `test_calibrate_from_idle_returns_to_blank`, `test_reconnect_after_stop_blanks_the_page`, `test_watch_idle_*` where they assert the old behaviour)

```python
async def test_stop_while_playing_shows_stopped_screen_on_that_video()
async def test_stop_when_stopped_is_already_stopped()                 # did_action False, reason "already_stopped", no message
async def test_end_of_playlist_highlights_first_not_done_with_notice()  # status "Fine della coda", cleared after ARRIVAL_NOTICE_S (monkeypatch to 0.01)
async def test_end_of_playlist_all_done_highlights_last()
async def test_sync_removing_playing_video_shows_removed_notice()     # stop_and_remove -> status "Video rimosso dalla playlist"
async def test_calibration_from_stopped_returns_to_same_screen()
async def test_reconnect_while_stopped_resends_screen()
async def test_up_down_while_stopped_move_highlight_no_cast()
async def test_ok_while_stopped_runs_selected_command()
async def test_closed_page_up_opens_stopped_screen_from_cursor()      # screen None, page not ready: next -> launch started, selection = item after cursor, no load
async def test_closed_page_ok_plays_ready_cursor()
async def test_closed_page_ok_on_unready_cursor_shows_not_ready()     # no skip to the next ready video; flash "Non ancora pronto"; launch started
async def test_closed_page_seek_opens_screen_with_command_moved()
async def test_night_after_timeout_then_first_key_only_wakes()        # monkeypatch NIGHT_AFTER_S; view night True; next -> reason "woke", selection unchanged, night False
async def test_explicit_command_in_night_wakes_and_acts()             # toggle_loop during night: night False and loop toggled
async def test_refresh_during_night_keeps_night_and_timer()
async def test_reconnect_resends_night_and_confirm()
async def test_idle_close_counts_from_last_key()                      # fake clock: keys keep the page open past 30 min from the stop; closes 30 min after the last key
async def test_screen_state_and_selected_video_id()
async def test_ok_on_empty_library_runs_player_command()             # stopped screen, every item removed: help "OK = Calibra", toggle calls calibrate, no load
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_player_core.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

- `stop()`: from playing/paused/calibrating → snapshot as today, then `_show_stopped(highlight=<loaded or cursor>)`, state idle. From idle → `_ALREADY_STOPPED`. Keep `test_stop_during_launch_wins` green.
- `_end_of_playlist()` → `_show_stopped(highlight=<first item not done, else last>, arrival=END_OF_QUEUE_NOTICE)`; `stop_and_remove` → `_show_stopped(highlight=cursor, arrival=REMOVED_NOTICE)`; `_end_calibration` returning to idle re-sends the kept screen (or `Screen(cursor)`); `_on_receiver_connect` while idle sends the screen, creating `Screen(cursor)` if none.
- Widen the screen guards in `next`/`prev`/`toggle`/`seek_relative` to `self.screen is not None and self.state in ("paused", "idle")`. At the top of those four handlers: `if self._wake_if_night(): return _WOKE`; then `_touch()`.
- Closed page (`state == "idle" and self.screen is None`): `next`/`prev`/`seek_relative` create `Screen(cursor)`, apply the move, send (queued for the page) and `_start_launch()`; `toggle` creates `Screen(cursor)` and plays the cursor if `cache_file_present()`, else flashes `NOT_READY_NOTICE` and launches. No forward skip anywhere from idle.
- `_enter_night()`: only if a screen exists and state is paused/idle: `screen.night = True`, send. Cancel the night timer whenever the screen goes away (`_resume`, `_cast`, `_drop_page`, `_close_if_idle`, `power_off`, calibration start).
- Explicit commands (`stop`, `calibrate`, `toggle_loop`, `request_sync`, `play(video_id)`, and in Task 5 `delete_request`) call `_wake_if_night()` and then act; they also cancel a pending confirmation.
- `_close_if_idle` and `_drop_page` set `screen = None`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crt/player_core.py tests/test_player_core.py
git commit -m "feat: schermata da fermo, pagina chiusa e fase notte"
```

---

### Task 5: Endpoints and `/status`

**Files:**
- Modify: `crt/player_core.py` (`delete_request`)
- Modify: `crt/api.py`
- Test: `tests/test_player_core.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 3-4 names.
- Produces: `async PlayerCore.delete_request() -> ActionResult`:
  - calibrating → `await self._end_calibration()`, then as with the screen shown;
  - playing (no screen) → `await self.delete_current()`, `_DID_ACTION` (immediate, as today);
  - screen shown → wake if night; confirmation pending → cancel it (`_DID_ACTION`); else set `confirm_video_id = selected` (no selection → `_NO_ITEMS`);
  - closed page (idle, no screen) → `Screen(cursor)` with `confirm_video_id = cursor`, send, `_start_launch()`; empty library → `_NO_ITEMS`.
- API: `/control/delete/current` → `_ack(await player.delete_request(), cursor_video_id=..., state=...)` (no more 404 on a missing cursor: `no_items` ack); `/control/loop/toggle` → `player.toggle_loop()` when a player exists (fallback to the library flag without player), response keeps `loop_mode`; `/control/sync` → 503 without engine as today, else `player.request_sync()` when a player exists, else `sync_engine.kick()`; `/status.player` adds `"screen": player.screen_state`, `"selected_video_id": player.selected_video_id`, and `menu_open` becomes `player.screen_state != "none"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_player_core.py
async def test_delete_request_while_playing_is_immediate()
async def test_delete_request_on_screen_asks_confirmation_on_highlighted_row()
async def test_second_delete_request_cancels()                       # bridge retry never confirms
async def test_delete_request_with_closed_page_opens_screen_confirming_cursor()
async def test_delete_request_on_empty_library_is_no_items()
# tests/test_api.py
def test_delete_current_returns_ack_from_delete_request()           # replaces test_delete_current_success / _404_when_no_cursor
def test_loop_toggle_goes_through_player()
def test_sync_goes_through_player()
def test_status_exposes_screen_and_selected_video()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_player_core.py tests/test_api.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add crt/player_core.py crt/api.py tests/
git commit -m "feat: endpoint di elimina, loop e sync passano dalla schermata; /status con screen"
```

---

### Task 6: Receiver page: teletext, preview, snow, night

**Files:**
- Modify: `crt/receiver_page/index.html`
- Create: `crt/receiver_page/bedstead.otf` (copy of `/private/tmp/claude-501/-Users-flocca-src-crt-player/b8a6f1ae-49dd-45b3-8429-5edf2f139e01/scratchpad/dl-font/bedstead.otf`, or download `https://bjh21.me.uk/bedstead/bedstead.otf`), `crt/receiver_page/BEDSTEAD-LICENSE.md` (author, URL, CC0 statement)
- Modify: `crt/api.py` (route `GET /receiver/bedstead.otf`, `Cache-Control: max-age=86400`)
- Test: `tests/test_receiver_page.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: the `screen{view}` message (Task 2 view keys), `blank{}` new meaning (Task 3).
- Reference for what Giacomo approved on the TV (not product code): `/private/tmp/claude-501/-Users-flocca-src-crt-player/b8a6f1ae-49dd-45b3-8429-5edf2f139e01/scratchpad/tvlab/page.html`, scenes `ttxmixp`/`ttxp`: fit algorithm (`fitTtx`), padding, snow, preview window loop, `previewOff` unload.

- [ ] **Step 1: Update the static tests**

```python
def test_page_handles_every_message_type():   # ("config","load","play","pause","seek","blank","screen","calibration"); "menu" absent
def test_page_unloads_preview_instead_of_hiding()   # regex: removeAttribute\("src"\) on the preview element
def test_page_has_no_css_animation_or_transition()  # "@keyframes" and "transition:" absent
def test_page_loads_bedstead_from_receiver_route()   # "/receiver/bedstead.otf" in src
# tests/test_api.py
def test_receiver_font_route_serves_otf()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_receiver_page.py tests/test_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the page**

- Remove the old menu markup, CSS and `renderMenu`; keep reports, `load`/`play`/`pause`/`seek`, the same-file shortcut, calibration.
- Elements, bottom to top: `#main`, `#preview` (`muted`, `playsinline`, no `loop`), canvas `#snow` (160×90, `image-rendering: pixelated`), `#veil` (black, opacity .7 / .9 in night), `#screen` (the grid inside the `.stretch` box).
- Padding of the screen box: only `config.margins` (top×1.25 px, right×1.6667 px, bottom×1.25 px, left×1.6667 px + 1.5%). Fit: font size = min(width-based for 40 columns, box height / 18), line height = box height / 18, `letter-spacing` = (box width − 40-column text width) / 40; recompute after the font loads (`document.fonts.load`).
- Render rows exactly as the spec table "Righe": header with a 1 s clock (local `Date`), mosaic, `PLAYLIST`/`n video`, row 4 (`paused_position` → `▌▌ mm:ss / mm:ss` + bar from `main.duration`; else `status`), 10 rows (truncate `text` with `…` to fit `40 − 6 − 7` columns, right column 7 wide), blank, the two command rows, row 18 (`flash` replaces the sync/loop part while set; `help` on the right).
- Background: `frame` → preview unloaded, snow off; `preview` → load `preview.url` only when it differs from the current `src`, seek to `start`, play muted, loop back to `start` at `end` (`timeupdate`); `snow` → preview unloaded, snow running; `black` → both off. Unload = `pause()`, `removeAttribute("src")`, `load()`.
- `screen` with `view: null` hides the grid, unloads the preview, stops the snow (playing).
- Night (`view.night`): veil .9, preview unloaded, snow stopped and hidden, only the header visible at opacity .35; drift of 3 px every 30 s on the grid (awake too).
- `blank`: unload `#main` and stop calibration only.
- Loading `#main` paused (`autoplay: false`): set `muted`, `play()`, `pause()` on `playing` after ~250 ms, unmute; skip `status` reports while doing it and send one report after the pause.

- [ ] **Step 4: Run tests and a local look**

Run: `python -m pytest tests/ -q` → all pass. Then serve the page locally (daemon on the Mac: `./run.sh`) and open `http://localhost:8765/receiver` in Chrome at 1280×720 to check the grid renders; the real check is Task 8 on the TV.

- [ ] **Step 5: Commit**

```bash
git add crt/receiver_page/ crt/api.py tests/
git commit -m "feat: pagina del receiver a televideo con anteprima, neve e fase notte"
```

---

### Task 7: TUI marker and Enter to play

**Files:**
- Modify: `tui_client/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `/status.player.screen`, `/status.player.selected_video_id` (Task 5); `DaemonClient.play(video_id)` (exists).

- [ ] **Step 1: Write the failing tests**

```python
async def test_list_marks_tv_highlight()             # status selected_video_id "B" -> B's label starts with "  *"; cursor A starts with ">> "
async def test_enter_on_row_plays_that_video()       # select row of B, press enter -> client.play("B")
async def test_list_keeps_index_across_refresh()     # index survives a library_state update
async def test_status_bar_shows_tv_screen()          # screen "stopped", selected 08 -> "TV: fermo · evidenziato 08"; "paused" -> "TV: in pausa · evidenziato 08"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ui.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**: label `f"{'>>' if cursor else '  '}{'*' if tv_selected else ' '} {status:>10s}  {title}"`; keep `ListView.index` across `clear()`/refill; `on_list_view_selected` → `asyncio.to_thread(self.client.play, video_id)` (keep the video ids of the rows in a list in the same order); status bar segment from `/status.player.screen` (`"none"` → nothing), with the number = 1-based position in the library, two digits.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tui_client/ui.py tests/test_ui.py
git commit -m "feat: TUI mostra la riga evidenziata sulla TV e Invio la riproduce"
```

---

### Task 8: Docs and TV check

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `CLAUDE.md`**: architecture paragraph "Receiver page and pause menu" → teletext screen in pause and stopped (`crt/screen.py`, `screen{view}` message, `blank` meaning); Stop no longer blacks the page; seek while paused gone; preview copies (`_noaudio.mp4`, made by the pipeline and backfilled, kept by the three cleanups); gotchas from the TV test (muted ignored on a second `<video>`, hidden `<video>` keeps its frame on the video plane, paused-at-load needs a muted play/pause to paint, no CSS animation over video at 24-30 fps); `ActionResult` reasons `already_stopped`, `woke`; `/status.player.screen` and `selected_video_id`; testing list (`test_screen.py` replaces `test_pause_menu.py`); the spec link.

- [ ] **Step 2: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 3: TV check with Giacomo** (ask first whether he is in front of the TV; drive with `curl` against a local daemon, not the Flipper, which talks to Chet). Checklist: pause → screen on the paused frame, OK resumes; Up/Down show previews with no audio and the paused frame comes back clean; Left/Right walk both command rows with fixed colours; Elimina on another row asks in red and only OK deletes; Stop → stopped screen on the same row with the preview; end of queue notice; snow on a downloading row; 5 min → night, first key only wakes; Spegni → Chromecast backdrop, then Up reopens the stopped screen; calibration from stopped returns to the same screen.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md aggiornato per la schermata televideo"
```
