# Pause Menu on the TV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Default Media Receiver with our own page opened on the Chromecast through DashCast, so that every pause shows a full-screen menu (playlist + commands) driven by the existing Flipper/TUI endpoints.

**Architecture:** The daemon serves a single page (`GET /receiver`) that holds a `<video>` plus the menu overlay. The daemon talks to it over Server-Sent Events (`GET /receiver/events`) and the page reports back with POSTs (`POST /receiver/report`); `ReceiverChannel` is the daemon-side end of that link. `ChromecastManager` only discovers the device, launches DashCast on the page URL with `force=True`, and quits the app. `PlayerCore` keeps its public transport API but drives the page instead of `media_controller`, owns the menu state (`PauseMenu`) and reinterprets next/prev/toggle/seek while the menu is open.

**Tech Stack:** Python 3.12, asyncio, FastAPI/Starlette 1.0 (`StreamingResponse`, `FileResponse` with Range), pychromecast 14 (`pychromecast.controllers.dashcast.DashCastController`), plain HTML/CSS/JS for Chrome 90 (CrKey 1.56).

**Spec:** `docs/superpowers/specs/2026-11-07-pause-menu-design.md` (Italian). Read it before starting; the spike section records what was verified on the real Chromecast.

## Global Constraints

- Target Chromecast: "TV in Ufficio", Chromecast Ultra. Receiver browser is Chrome 90, viewport 1280x720 CSS px, `devicePixelRatio` 1.5. No web features newer than Chrome 90 (no `:has()`, container queries, `structuredClone`, top-level `await`).
- DashCast app ID `84912283` (`pychromecast.config.APP_DASHCAST`), always launched with `load_url(url, force=True)`. Every `load_url` restarts the app and shows the DashCast logo: the page is loaded once per app session and everything else happens inside it.
- Page and media are plain HTTP from the daemon's own uvicorn on `config.SERVER_PORT` (8765), at the LAN IP from `_get_local_ip()`.
- Overlay geometry: the TV squeezes 16:9 into 4:3, so everything the page draws over the video goes in a container `width: 75%` with `transform: scaleX(1.3333); transform-origin: left top`. The video uses `object-fit: fill`.
- Overscan margins (`config.MARGIN_TOP/BOTTOM/LEFT/RIGHT`, pixels of the 768x576 logical frame) map to page CSS px as vertical × 1.25 and horizontal × 1.6667.
- UI copy on the TV is Italian, exactly: command labels `Stop`, `Elimina video`, `Calibrate`, `Toggle loop`, `Sync now`; notices `non ancora pronto`, `Loop attivo`, `Loop disattivato`, `Sync avviato`, `Sync non disponibile`; confirm prompt `Confermi? OK sì`.
- Status symbols: `▶` paused/current video, `○` ready, `✓` done (row dimmed), `…` queued, `… NN%` downloading/encoding (`progress` is 0-100), `!` error.
- Timings: notice visible 2 s; calibration auto-ends after 60 s; receiver considered lost 10 s after its last SSE subscriber disconnects; page connect timeout after launch 20 s; SSE ping every 5 s; app closed after `CRT_RECEIVER_IDLE_CLOSE_S` (default 1800) seconds without playback.
- Player states become exactly `idle | playing | paused | calibrating` (`casting` disappears).
- The Flipper FAP and the bridge (`../homelab/services/crt-flipper-bridge/bridge.py`) do not change. `/control/delete/current` keeps deleting without confirmation; only the TV menu asks for it.
- Code comments, logs and identifiers in English (repo convention).
- Run tests with `source .venv/bin/activate && python -m pytest tests/ -q` from the repo root.

## Review Focus

- A late `ended` report for the previous video arriving after a new `load` must not advance the playlist (the page reports `src`; the channel ignores `ended` whose `src` is not the last loaded URL). Test in Task 1.
- An SSE blip while paused (page reconnects within 10 s) must keep the player paused with the menu: on reconnect the daemon re-sends `load` with `autoplay: false` at the saved position and the current menu view. Test in Task 7.
- The playlist changing while the menu is open (sync removes the selected video or the cursor video) must not crash the view; selection falls back to the cursor video, else the first entry. Test in Task 4.
- Toggle after the app was closed (idle close or another app took the Chromecast) must relaunch DashCast and resume from the saved position, not from 0. Test in Task 7.
- Seeking while paused past the end or before 0 must clamp to `[0, duration]` (duration from the page; no upper clamp when it is 0). Test in Task 5.

---

### Task 1: `ReceiverChannel`, the daemon side of the page link

**Files:**
- Create: `crt/receiver.py`
- Test: `tests/test_receiver.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class ReceiverChannel(margins: dict[str, int], clock: Callable[[], float] = time.monotonic)`
  - `connected: bool` (property, True while at least one subscriber)
  - `subscribe() -> asyncio.Queue[dict]` (first enqueues `{"type": "config", "margins": margins}`; fires the connect callback when going 0→1 subscribers)
  - `unsubscribe(q) -> None` (fires the disconnect callback when going 1→0)
  - `set_callbacks(on_connect: Callable[[], None] | None, on_disconnect: Callable[[], None] | None) -> None`
  - `send(msg: dict) -> None` (puts to every subscriber; no-op without subscribers; also updates local state for `load` and `seek`, see below)
  - `handle_report(report: dict) -> None`
  - `live_position: float | None` (property)
  - `duration: float`, `paused: bool` (attributes)
  - `async wait_connected(timeout_s: float) -> bool`
  - `async wait_ended() -> None`, `reset_ended() -> None`
  - `async def sse_events(channel: ReceiverChannel, ping_s: float = 5.0) -> AsyncIterator[str]` (module function: subscribes, yields `"data: <json>\n\n"` per message and `": ping\n\n"` every `ping_s` without messages, unsubscribes in `finally`)
  - Message types daemon→page (dicts, exact keys): `config{margins}`, `load{url, position, autoplay}`, `play{}`, `pause{}`, `seek{position}`, `blank{}`, `menu{view}` (`view` is a dict or `None`), `calibration{url}` (`url` is a str or `None`).
  - Report schema page→daemon: `{"event": "hello"|"status"|"ended"|"error", "position": float, "duration": float, "paused": bool, "src": str | None, "error": str | None}`.

State rules the tests pin:
- `send(load)` sets the expected src to `msg["url"]`, position to `msg["position"]`, `paused = not msg["autoplay"]`, timestamp `clock()`. `send(seek)` sets position to `msg["position"]` and the timestamp. `send(pause)` freezes the extrapolated position; `send(play)` restarts extrapolation from it.
- `handle_report` with `status`/`hello` updates position, duration, paused and timestamp only when `src` equals the expected src (or nothing has been loaded yet).
- `live_position`: `None` before any load or report; when paused, the stored position; when playing, `position + (clock() - timestamp)`, clamped to `duration` when `duration > 0`.
- `ended` sets the ended event only when `src` equals the expected src.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_receiver.py
import asyncio
import pytest
from crt.receiver import ReceiverChannel, sse_events

MARGINS = {"top": 10, "bottom": 12, "left": 8, "right": 6}

class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t

def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out

def test_subscribe_sends_config_first_and_marks_connected():
    ch = ReceiverChannel(MARGINS)
    q = ch.subscribe()
    assert ch.connected
    assert drain(q) == [{"type": "config", "margins": MARGINS}]

def test_connect_and_disconnect_callbacks_fire_on_edges():
    ch = ReceiverChannel(MARGINS)
    events = []
    ch.set_callbacks(lambda: events.append("on"), lambda: events.append("off"))
    a = ch.subscribe(); b = ch.subscribe()
    ch.unsubscribe(a); ch.unsubscribe(b)
    assert events == ["on", "off"]
    assert not ch.connected

def test_send_without_subscribers_is_noop():
    ReceiverChannel(MARGINS).send({"type": "play"})  # must not raise

def test_live_position_extrapolates_while_playing_and_clamps_to_duration():
    clock = Clock(); ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 30.0, "autoplay": True})
    clock.t += 5
    assert ch.live_position == pytest.approx(35.0)
    ch.handle_report({"event": "status", "position": 90.0, "duration": 100.0, "paused": False, "src": "u1"})
    clock.t += 50
    assert ch.live_position == pytest.approx(100.0)

def test_pause_freezes_live_position():
    clock = Clock(); ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u1", "position": 10.0, "autoplay": True})
    clock.t += 4
    ch.send({"type": "pause"})
    clock.t += 60
    assert ch.live_position == pytest.approx(14.0)

def test_live_position_none_before_anything_loaded():
    assert ReceiverChannel(MARGINS).live_position is None

def test_report_for_previous_src_is_ignored():
    clock = Clock(); ch = ReceiverChannel(MARGINS, clock=clock)
    ch.send({"type": "load", "url": "u2", "position": 0.0, "autoplay": True})
    ch.handle_report({"event": "status", "position": 500.0, "duration": 600.0, "paused": False, "src": "u1"})
    assert ch.live_position == pytest.approx(0.0)

@pytest.mark.asyncio
async def test_late_ended_from_previous_video_does_not_fire():
    ch = ReceiverChannel(MARGINS)
    ch.send({"type": "load", "url": "u2", "position": 0.0, "autoplay": True})
    ch.handle_report({"event": "ended", "position": 600.0, "duration": 600.0, "paused": True, "src": "u1"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ch.wait_ended(), 0.05)
    ch.handle_report({"event": "ended", "position": 600.0, "duration": 600.0, "paused": True, "src": "u2"})
    await asyncio.wait_for(ch.wait_ended(), 0.05)

@pytest.mark.asyncio
async def test_wait_connected_times_out_and_succeeds():
    ch = ReceiverChannel(MARGINS)
    assert await ch.wait_connected(0.01) is False
    ch.subscribe()
    assert await ch.wait_connected(0.01) is True

@pytest.mark.asyncio
async def test_sse_events_yields_data_and_pings_then_unsubscribes():
    ch = ReceiverChannel(MARGINS)
    gen = sse_events(ch, ping_s=0.01)
    first = await gen.__anext__()
    assert first.startswith("data: ") and '"config"' in first and first.endswith("\n\n")
    assert await gen.__anext__() == ": ping\n\n"
    ch.send({"type": "play"})
    assert await gen.__anext__() == 'data: {"type": "play"}\n\n'
    await gen.aclose()
    assert not ch.connected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_receiver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crt.receiver'`

- [ ] **Step 3: Implement `crt/receiver.py`**

`asyncio.Queue` per subscriber, `asyncio.Event` for connected/ended. `sse_events` uses `asyncio.wait_for(q.get(), ping_s)` and serializes with `json.dumps(msg, ensure_ascii=False)`. Callbacks are plain callables invoked synchronously on the event loop.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_receiver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/receiver.py tests/test_receiver.py
git commit -m "feat: ReceiverChannel, canale fra daemon e pagina sul Chromecast"
```

---

### Task 2: HTTP endpoints for the page

**Files:**
- Modify: `crt/api.py` (`create_app` signature and three new routes)
- Create: `crt/receiver_page/index.html` (placeholder `<!doctype html><title>crt receiver</title>` here; Task 3 writes the real page). The folder is not `crt/receiver/` because the module `crt/receiver.py` owns that name.
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `ReceiverChannel`, `sse_events` (Task 1).
- Produces:
  - `create_app(library, player=None, sync_engine=None, pipeline=None, media_dir=None, receiver: ReceiverChannel | None = None) -> FastAPI`
  - `GET /receiver` → `HTMLResponse` with the contents of `crt/receiver_page/index.html` (path resolved from `os.path.dirname(__file__)`), header `Cache-Control: no-store`.
  - `GET /receiver/events` → `StreamingResponse(sse_events(receiver), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})`; 503 if `receiver is None`.
  - `POST /receiver/report` → `receiver.handle_report(body)`, returns `{"ok": True}`; 503 if `receiver is None`.

- [ ] **Step 1: Write the failing tests** (in `tests/test_api.py`, using the file's existing `TestClient` pattern)

```python
def test_receiver_page_is_served_as_html(...):
    r = client.get("/receiver")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"

def test_receiver_report_is_forwarded_to_channel(...):
    receiver = MagicMock()
    # app built with receiver=receiver
    r = client.post("/receiver/report", json={"event": "status", "position": 1.0, "duration": 2.0, "paused": False, "src": "u"})
    assert r.json() == {"ok": True}
    receiver.handle_report.assert_called_once_with({"event": "status", "position": 1.0, "duration": 2.0, "paused": False, "src": "u"})

def test_receiver_endpoints_503_without_channel(...):
    assert client.post("/receiver/report", json={}).status_code == 503

def test_media_supports_range_requests(tmp_path, ...):
    # write 1000 bytes to media_dir/v.mp4
    r = client.get("/media/v.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 100-199/1000"
    assert len(r.content) == 100
```

The SSE route is a thin wrapper over `sse_events`, already tested in Task 1; do not test the infinite stream through `TestClient`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_api.py -v -k "receiver or range"`
Expected: FAIL (404 on `/receiver`; the Range test may already PASS: Starlette 1.0 `FileResponse` handles Range. If it passes, keep it as a regression test).

- [ ] **Step 3: Implement the routes in `crt/api.py`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/api.py crt/receiver_page/index.html tests/test_api.py
git commit -m "feat: endpoint /receiver, /receiver/events e /receiver/report"
```

---

### Task 3: The page (`crt/receiver_page/index.html`)

**Files:**
- Modify: `crt/receiver_page/index.html` (single file, inline CSS and JS)
- Modify: `pyproject.toml` (add `[tool.setuptools.package-data] crt = ["receiver_page/*.html"]` so a non-editable install ships it; the Dockerfile's `pip install -e .` already sees it)
- Test: `tests/test_receiver_page.py`

**Interfaces:**
- Consumes: message types and report schema from Task 1; routes from Task 2.
- Produces: the page behaviour below. The spike page (spec, "Prova lampo") is the visual reference: same font sizes, same overlay colours.

Behaviour:
- `<video id="main" playsinline>` full screen, `object-fit: fill`, black background. `<video id="cal" loop muted playsinline>` stacked above it, hidden unless calibrating.
- `EventSource("/receiver/events")`; on `open` POST a `hello` report; dispatch each message by `type`:
  - `config`: set `#menu` padding to `calc(5% + {top*1.25}px) calc(8% + {right*1.6667}px) calc(5% + {bottom*1.25}px) calc(8% + {left*1.6667}px)`.
  - `load`: set `main.src = url`; on `loadedmetadata` set `main.currentTime = position`, then `play()` if `autoplay` else `pause()`.
  - `play` / `pause` / `seek` (`main.currentTime = position`).
  - `blank`: pause, remove `src`, `main.load()`, hide menu and calibration.
  - `menu`: `view === null` hides the overlay; otherwise render it (below).
  - `calibration`: `url` shows `#cal` with that src and plays it; `null` stops and hides it.
- Reports (`POST /receiver/report`, JSON, `src` = current `main.src` or `null`): `status` every 1 s while playing and on `playing`, `pause`, `seeked`, `loadedmetadata`; `ended` on `main` `ended`; `error` on `main` `error` and on `window.onerror` (with message).
- Menu overlay (`#menu`, background `rgba(16,16,16,.85)`, inner container with the 4/3 pre-stretch): line 1 `▶ {view.title}` (44px bold); line 2 `mm:ss / mm:ss` from `main.currentTime`/`main.duration` (34px), refreshed on `timeupdate` and `seeked`; progress bar (12px); `view.rows` (36px bold, `selected` → inverse colours, `dim` → opacity .6, text `{symbol} {title}` with ellipsis); separator; `view.commands` on one line joined by ` · ` (30px bold, the selected one inverse); `view.confirm` (when not null) on its own inverse line under the commands; `view.notice` (when not null) on its own line under the commands. The page never times out notices: the daemon clears them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_receiver_page.py
import pathlib, re
PAGE = pathlib.Path(__file__).parent.parent / "crt" / "receiver_page" / "index.html"

def test_page_handles_every_message_type():
    src = PAGE.read_text()
    for t in ("config", "load", "play", "pause", "seek", "blank", "menu", "calibration"):
        assert re.search(rf"['\"]{t}['\"]", src), t

def test_page_reports_every_event():
    src = PAGE.read_text()
    for e in ("hello", "status", "ended", "error"):
        assert re.search(rf"['\"]{e}['\"]", src), e
    assert "/receiver/report" in src and "/receiver/events" in src

def test_page_keeps_the_4_3_prestretch():
    assert "scaleX(1.3333)" in PAGE.read_text()

def test_page_avoids_post_chrome_90_features():
    src = PAGE.read_text()
    for banned in (":has(", "@container", "structuredClone"):
        assert banned not in src
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_receiver_page.py -v`
Expected: FAIL on the placeholder page

- [ ] **Step 3: Write the page**

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_receiver_page.py -v`
Expected: all PASS

- [ ] **Step 5: Check it in a desktop browser against a stub**

Start a throwaway FastAPI app in the scratchpad that mounts `create_app(..., receiver=ReceiverChannel(margins))` with a sample MP4 in `media_dir`, open `http://localhost:<port>/receiver` in Chrome, and push `load`, `pause`, `menu` (with a 7-row view), `seek`, `calibration`, `blank` from a Python REPL via `channel.send`. Expected: video plays, menu renders as in the spec mock-up (stretched horizontally on a normal monitor, which is correct), reports arrive. Save a screenshot as `pause_menu_desktop.png` in the scratchpad and show it to Giacomo.

- [ ] **Step 6: Commit**

```bash
git add crt/receiver_page/index.html pyproject.toml tests/test_receiver_page.py
git commit -m "feat: pagina receiver con video e menu in pausa"
```

---

### Task 4: `PauseMenu`, menu logic and view

**Files:**
- Create: `crt/pause_menu.py`
- Test: `tests/test_pause_menu.py`

**Interfaces:**
- Consumes: `QueueItem` (`video_id`, `title`, `status`, `progress`).
- Produces:
  - `COMMANDS: tuple[tuple[str, str], ...] = (("stop", "Stop"), ("delete", "Elimina video"), ("calibrate", "Calibrate"), ("loop", "Toggle loop"), ("sync", "Sync now"))`
  - `VISIBLE_ROWS = 7`, `CONFIRM_DELETE = "Confermi? OK sì"`
  - `@dataclass(frozen=True) class Entry: kind: Literal["video", "command"]; key: str`
  - `status_symbol(item: QueueItem, is_cursor: bool) -> str`
  - `class PauseMenu(current_video_id: str)`: attributes `selected: Entry` (starts on `Entry("video", current_video_id)`), `confirming_delete: bool = False`, `notice: str | None = None`
  - `PauseMenu.entries(video_ids: list[str]) -> list[Entry]` (videos in library order, then the 5 commands)
  - `PauseMenu.move(video_ids: list[str], delta: int) -> None` (ring over `entries`; clears `confirming_delete` and `notice`)
  - `PauseMenu.view(items: list[QueueItem], cursor_video_id: str | None) -> dict` (MenuView: `{"title", "rows", "commands", "notice", "confirm"}`, rows are `{"symbol", "title", "selected", "dim"}`, commands are `{"label", "selected"}`)

Rules:
- If `selected` is a video no longer in `video_ids`, `move` and `view` first reset it to the cursor video, else to the first entry.
- Row window: selection on video index `i` → start `min(max(i - 3, 0), max(n - 7, 0))`; selection on a command → start `max(n - 7, 0)`.
- `title` is the cursor item's title (empty string if none).
- `status_symbol`: cursor → `▶`; `ready`, `casting`, `playing`, `paused` → `○`; `done` → `✓`; `queued` → `…`; `downloading`/`encoding` → `… {int(progress)}%`; `error` → `!`. `dim` is `status == "done"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pause_menu.py
from crt.library_store import QueueItem
from crt.pause_menu import PauseMenu, Entry, status_symbol, COMMANDS

def items(*specs):  # specs: (video_id, status)
    return [QueueItem(video_id=v, title=f"T{v}", status=s) for v, s in specs]
# (adapt the QueueItem constructor to its real required fields)

def test_menu_opens_on_current_video():
    m = PauseMenu("b")
    assert m.selected == Entry("video", "b")

def test_move_walks_videos_then_commands_and_wraps():
    m = PauseMenu("c")
    ids = ["a", "b", "c"]
    m.move(ids, +1)
    assert m.selected == Entry("command", "stop")
    m.move(ids, +5)
    assert m.selected == Entry("video", "a")
    m.move(ids, -1)
    assert m.selected == Entry("command", "sync")

def test_move_clears_confirm_and_notice():
    m = PauseMenu("a"); m.confirming_delete = True; m.notice = "x"
    m.move(["a"], +1)
    assert not m.confirming_delete and m.notice is None

def test_view_rows_window_is_seven_centered_on_selection():
    lib = items(*[(str(i), "ready") for i in range(19)])
    m = PauseMenu("10")
    v = m.view(lib, "10")
    assert [r["title"] for r in v["rows"]] == [f"T{i}" for i in range(7, 14)]
    assert [r["selected"] for r in v["rows"]].index(True) == 3

def test_view_window_on_command_shows_last_rows():
    lib = items(*[(str(i), "ready") for i in range(19)])
    m = PauseMenu("18"); m.move([i.video_id for i in lib], +1)
    v = m.view(lib, "18")
    assert v["rows"][-1]["title"] == "T18"
    assert [c["selected"] for c in v["commands"]] == [True, False, False, False, False]
    assert [c["label"] for c in v["commands"]] == [label for _, label in COMMANDS]

def test_view_confirm_and_notice():
    m = PauseMenu("a"); m.confirming_delete = True; m.notice = "Loop attivo"
    v = m.view(items(("a", "paused")), "a")
    assert v["confirm"] == "Confermi? OK sì" and v["notice"] == "Loop attivo"

def test_selected_video_removed_from_library_falls_back_to_cursor():
    m = PauseMenu("a"); m.move(["a", "b"], +1)  # on "b"
    v = m.view(items(("a", "paused")), "a")
    assert m.selected == Entry("video", "a")
    assert v["rows"][0]["selected"]

def test_selection_falls_back_to_first_entry_when_cursor_gone_too():
    m = PauseMenu("a")
    m.view(items(("z", "ready")), None)
    assert m.selected == Entry("video", "z")

def test_status_symbols():
    a, b, c, d, e, f = items(("a", "ready"), ("b", "done"), ("c", "queued"), ("d", "encoding"), ("e", "error"), ("f", "ready"))
    d.progress = 43.7
    assert status_symbol(a, False) == "○"
    assert status_symbol(b, False) == "✓"
    assert status_symbol(c, False) == "…"
    assert status_symbol(d, False) == "… 43%"
    assert status_symbol(e, False) == "!"
    assert status_symbol(f, True) == "▶"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_pause_menu.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `crt/pause_menu.py`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pause_menu.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/pause_menu.py tests/test_pause_menu.py
git commit -m "feat: logica e vista del menu in pausa"
```

---

### Task 5: PlayerCore plays through the page, pause opens the menu

**Files:**
- Modify: `crt/chromecast_mgr.py` (add DashCast launch; old media-controller methods stay until Task 9)
- Modify: `crt/player_core.py`
- Test: `tests/test_player_core.py`, `tests/test_chromecast_mgr.py`

**Interfaces:**
- Consumes: `ReceiverChannel` (Task 1), `PauseMenu` (Task 4).
- Produces:
  - `ChromecastManager.launch_receiver(url: str) -> None` (blocking: `DashCastController.load_url(url, force=True)` on a controller registered with `self.cast.register_handler` in `_discover_sync`; raises `RuntimeError` when not connected)
  - `ChromecastManager.receiver_app_active: bool` (property: `self.cast is not None and self.cast.app_id == APP_DASHCAST`)
  - `ChromecastManager.quit_app() -> None` (`_safe_cmd(self.cast.quit_app)` when connected)
  - `PlayerCore(library, chromecast, receiver: ReceiverChannel, youtube_client=None, on_cache_miss=None, on_sync_request: Callable[[], None] | None = None, clock: Callable[[], float] = time.monotonic)`
  - `PlayerCore.state: str` in `idle | playing | paused | calibrating`
  - `PlayerCore.menu: PauseMenu | None`
  - `PlayerCore.on_sync_request` (public attribute, settable after construction)
  - `PlayerCore.live_position() -> float` (`receiver.live_position or 0.0` when state is `playing`/`paused`, else `0.0`)
  - `PlayerCore._ensure_receiver() -> bool` (async; returns True at once if `receiver.connected and chromecast.receiver_app_active`; otherwise `await chromecast.wait_for_connection()`, `to_thread(chromecast.launch_receiver, f"http://{_get_local_ip()}:{config.SERVER_PORT}/receiver")`, `await receiver.wait_connected(20.0)`)
  - Module constants in `player_core.py`: `RECEIVER_CONNECT_TIMEOUT_S = 20.0`, `NOTICE_S = 2.0`

Behaviour in this task (menu selection comes in Task 6):
- `_cast_current()`: same cache checks as today; then `_ensure_receiver()` (False → log and return False); `receiver.reset_ended()`; `receiver.send({"type": "menu", "view": None})`; `receiver.send({"type": "load", "url": f"http://{ip}:{port}/media/{item.filename}", "position": item.playback_position, "autoplay": True})`; `menu = None`; `item.status = "playing"`; `state = "playing"`.
- `toggle()` from `playing`: `item.playback_position = receiver.live_position or item.playback_position`; send `pause`; `item.status = "paused"`; `state = "paused"`; `menu = PauseMenu(cursor)`; send `menu` with `menu.view(...)`. From `idle`: today's scan and `_cast_current()`. (`paused` → Task 6.)
- `next()`/`prev()` while `paused` with a menu: `menu.move(ids, +1 / -1)`, send the view, return `ActionResult(True)`; otherwise today's behaviour.
- `seek_relative(seconds)`: `playing` → send `seek` to `live_position + seconds` clamped; `paused` → same, also `item.playback_position = new`, `menu.confirming_delete = False`, re-send the view; `idle` → `_NO_CC_SESSION`. Clamp to `[0, receiver.duration]`, no upper clamp when `duration` is 0.
- `stop()`: snapshot like `toggle` when the item is `playing`/`paused`, `item.status = "ready"`; if `receiver.connected` send `menu` None and `blank`; `menu = None`; `state = "idle"`. The app stays open.
- `stop_and_remove()`: same condition as today on `playing`/`paused`/`calibrating`, sends `blank`, `menu = None`, `state = "idle"`.
- `watch_natural_end()`: `await receiver.wait_ended()`, `reset_ended()`, then `on_playback_finished()` only if `state == "playing"`.
- `_recast_after_session_loss` and every `is_session_lost` call go away (Task 7 handles a lost page).

- [ ] **Step 1: Rewrite the test fixtures and write the failing tests**

Replace `_make_chromecast()` in `tests/test_player_core.py` with a mock exposing `wait_for_connection = AsyncMock()`, `launch_receiver = MagicMock()`, `receiver_app_active = True`, `quit_app = MagicMock()`. Add `_make_player(library)` returning `(player, receiver, q)` where `receiver = ReceiverChannel(margins)`, `q = receiver.subscribe()` drained of the config message. Port every existing test that asserted `cc.cast_url` / `pause_or_resume` / `stop` calls to assert the messages in `q` instead (`load` with the right url/position, `pause`, `blank`). Delete the session-loss tests (`is_session_lost`, `_recast_after_session_loss`). New tests:

```python
async def test_cast_sends_load_with_saved_position():
    # cursor item with filename "a.mp4" (cache present) and playback_position 42.0
    await player.play("a")
    load = [m for m in drain(q) if m["type"] == "load"][-1]
    assert load["url"].endswith(":8765/media/a.mp4")
    assert load["position"] == 42.0 and load["autoplay"] is True
    assert player.state == "playing"

async def test_cast_launches_dashcast_when_page_not_connected():
    # receiver with no subscriber; cc.launch_receiver side_effect subscribes a queue
    await player.play("a")
    cc.launch_receiver.assert_called_once()
    assert cc.launch_receiver.call_args.args[0].endswith(":8765/receiver")

async def test_cast_fails_cleanly_when_page_never_connects(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_CONNECT_TIMEOUT_S", 0.01)
    # no subscriber, launch does nothing
    result = await player.play("a")
    assert result.did_action is False and player.state == "idle"

async def test_pause_snapshots_position_and_opens_menu_on_current_video():
    # playing "b" in library a, b, c; receiver live position 77.0
    await player.toggle()
    msgs = drain(q)
    assert {"type": "pause"} in msgs
    menu_msg = [m for m in msgs if m["type"] == "menu"][-1]
    assert [r["selected"] for r in menu_msg["view"]["rows"]] == [False, True, False]
    assert player.state == "paused" and item_b.playback_position == 77.0

async def test_next_and_prev_move_highlight_while_paused():
    # paused on "b"; next → "c" highlighted, cursor unchanged, no load sent
    r = await player.next()
    assert r.did_action and library.cursor_video_id == "b"
    assert not [m for m in drain(q) if m["type"] == "load"]
    assert player.menu.selected == Entry("video", "c")

async def test_seek_while_paused_clamps_and_updates_saved_position():
    # paused at 590 of duration 600 (receiver.handle_report status duration 600)
    await player.seek_relative(30)
    assert item.playback_position == 600.0
    await player.seek_relative(-1000)
    assert item.playback_position == 0.0

async def test_stop_blanks_page_and_keeps_app_open():
    await player.stop()
    assert {"type": "blank"} in drain(q)
    cc.quit_app.assert_not_called()
    assert player.state == "idle"

async def test_natural_end_ignored_unless_playing():
    # state paused, receiver ended event set for the loaded src → no cursor change
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_player_core.py tests/test_chromecast_mgr.py -v`
Expected: FAIL (constructor signature, missing `launch_receiver`)

- [ ] **Step 3: Implement the ChromecastManager additions and the PlayerCore changes**

Add tests in `tests/test_chromecast_mgr.py` for `launch_receiver` (calls `load_url(url, force=True)` on the registered controller; `RuntimeError` without cast) and `receiver_app_active`.

- [ ] **Step 4: Run the full unit suite**

Run: `python -m pytest tests/ -q -m "not integration"`
Expected: `test_player_core.py` and `test_chromecast_mgr.py` PASS. `test_daemon.py`/`test_api.py` failures caused by the constructor change are fixed in Task 8; list them in the commit message body if any remain.

- [ ] **Step 5: Commit**

```bash
git add crt/chromecast_mgr.py crt/player_core.py tests/test_player_core.py tests/test_chromecast_mgr.py
git commit -m "feat: PlayerCore riproduce dalla pagina DashCast e apre il menu in pausa"
```

---

### Task 6: Menu selection, commands and confirm

**Files:**
- Modify: `crt/player_core.py`
- Test: `tests/test_player_core.py`

**Interfaces:**
- Consumes: Task 5's `PlayerCore`, `PauseMenu`, `COMMANDS`.
- Produces:
  - `PlayerCore._menu_select() -> ActionResult` (async), called by `toggle()` when `state == "paused"` and `menu is not None`
  - `PlayerCore._flash_notice(text: str) -> None` (sets `menu.notice`, sends the view, schedules clearing + re-send after `NOTICE_S` with `loop.call_later`; a newer notice cancels the pending clear)

Behaviour of `_menu_select()` by selected entry:
- Cursor video → resume: send `menu` None, send `play`, `item.status = "playing"`, `state = "playing"`, `menu = None`.
- Other video with `cache_file_present()` → `cursor_video_id = it`; `_cast_current()` (the previous item goes back to `ready` through the existing loop in `_cast_current`).
- Other video without cache → `_flash_notice("non ancora pronto")`, return `ActionResult(False, "no_playable_item")`; cursor unchanged.
- `stop` → `await self.stop()`.
- `delete` → first OK sets `menu.confirming_delete = True` and sends the view; second OK (still confirming) → `menu = None`, `await self.delete_current()`. Any `next`/`prev`/`seek` clears the confirm (Task 5's `move` and seek already do).
- `calibrate` → `await self.calibrate()` (Task 7).
- `loop` → flip `library.loop_mode`, `_flash_notice("Loop attivo" if on else "Loop disattivato")`.
- `sync` → call `on_sync_request()` and `_flash_notice("Sync avviato")`; when `on_sync_request is None`, `_flash_notice("Sync non disponibile")`.
- `delete_current()` stops first when `state in ("playing", "paused", "calibrating")` (today's tuple names `casting`, which no longer exists) and sets `menu = None`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_ok_on_current_video_resumes():
    # paused on "b", selection on "b"
    await player.toggle()
    msgs = drain(q)
    assert {"type": "menu", "view": None} in msgs and {"type": "play"} in msgs
    assert player.state == "playing" and player.menu is None

async def test_ok_on_other_ready_video_plays_it():
    await player.next(); await player.toggle()
    assert library.cursor_video_id == "c"
    assert [m for m in drain(q) if m["type"] == "load"][-1]["url"].endswith("/media/c.mp4")

async def test_ok_on_unready_video_shows_notice_and_keeps_cursor():
    # "c" queued, no filename
    await player.next(); r = await player.toggle()
    assert r.did_action is False and library.cursor_video_id == "b"
    assert [m for m in drain(q) if m["type"] == "menu"][-1]["view"]["notice"] == "non ancora pronto"

async def test_notice_clears_after_notice_s(monkeypatch):
    monkeypatch.setattr("crt.player_core.NOTICE_S", 0.01)
    # trigger notice, then await asyncio.sleep(0.05)
    assert [m for m in drain(q) if m["type"] == "menu"][-1]["view"]["notice"] is None

async def test_delete_needs_two_oks_and_targets_paused_video():
    # move selection to the "delete" command, highlight passing over other rows
    await player.toggle()
    assert player.menu.confirming_delete and library.cursor_video_id == "b"
    await player.toggle()
    assert "b" not in [i.video_id for i in library.items]

async def test_delete_confirm_cancelled_by_other_key():
    # on "delete": toggle (confirm), then prev → confirm cleared, nothing deleted
    assert not player.menu.confirming_delete and "b" in [i.video_id for i in library.items]

async def test_loop_command_toggles_and_keeps_menu_open():
    await player.toggle()
    assert library.loop_mode is True and player.state == "paused"
    assert [m for m in drain(q) if m["type"] == "menu"][-1]["view"]["notice"] == "Loop attivo"

async def test_sync_command_calls_callback():
    kick = MagicMock(); player.on_sync_request = kick
    await player.toggle()
    kick.assert_called_once()

async def test_stop_command_blanks():
    await player.toggle()
    assert player.state == "idle" and {"type": "blank"} in drain(q)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_player_core.py -v -k "ok_on or notice or delete_ or loop_command or sync_command or stop_command"`
Expected: FAIL

- [ ] **Step 3: Implement `_menu_select` and `_flash_notice`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_player_core.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/player_core.py tests/test_player_core.py
git commit -m "feat: scelta dal menu in pausa, comandi e conferma di Elimina"
```

---

### Task 7: Calibration, lost page, idle close

**Files:**
- Modify: `crt/player_core.py`
- Modify: `crt/config.py` (`RECEIVER_IDLE_CLOSE_S = int(os.environ.get("CRT_RECEIVER_IDLE_CLOSE_S", "1800"))`)
- Test: `tests/test_player_core.py`, `tests/test_calibration.py` (only if `calibrate` assertions there break)

**Interfaces:**
- Consumes: Task 5-6 `PlayerCore`; `calibration.generate_calibration_clip(out_path)` unchanged; `ReceiverChannel.set_callbacks`.
- Produces:
  - Module constants: `CALIBRATION_TIMEOUT_S = 60.0`, `RECEIVER_LOST_GRACE_S = 10.0`
  - `PlayerCore.calibrate() -> None` (async, new behaviour)
  - `PlayerCore._end_calibration() -> None` (async)
  - `PlayerCore.watch_idle(close_after_s: float, poll_s: float = 30.0) -> None` (async, runs forever)
  - `PlayerCore.__init__` registers `receiver.set_callbacks(self._on_receiver_connect, self._on_receiver_disconnect)`

Behaviour:
- `calibrate()`: if `playing`, pause exactly as `toggle()` does but without opening the menu; remember the return target (`"paused"` if a cursor item was playing/paused, else `"idle"`); `_ensure_receiver()` (False → log and return); generate the clip as today; send `menu` None and `{"type": "calibration", "url": <media url of calibration_pattern.mp4>}`; `state = "calibrating"`; schedule `_end_calibration` after `CALIBRATION_TIMEOUT_S`.
- While `calibrating`, `toggle`/`next`/`prev`/`seek_relative` only call `_end_calibration()` and return `ActionResult(True)`.
- `_end_calibration()`: cancel the timer; send `calibration` None; back to `paused` with a fresh `PauseMenu(cursor)` and its view, or to `idle` with `blank`.
- `live_position()` returns 0.0 while `calibrating` (the periodic save must not write the clip time).
- Natural end never fires during calibration (the page loops `#cal` and `watch_natural_end` only acts in `playing`).
- `_on_receiver_disconnect()`: schedule a task that sleeps `RECEIVER_LOST_GRACE_S` and, if `receiver.connected` is still False, runs the lost handling: when `playing`/`paused`/`calibrating`, `item.playback_position = receiver.live_position or item.playback_position`, `item.status = "ready"`, cancel calibration timer, `menu = None`, `state = "idle"`.
- `_on_receiver_connect()`: cancel a pending lost task; when `playing` or `paused`, re-send `load` at `item.playback_position` (after snapshotting `receiver.live_position` into it) with `autoplay = (state == "playing")`, and when `paused` also the menu view.
- `watch_idle(close_after_s, poll_s)`: track `self._inactive_since` (set to `clock()` on every transition into `idle` or `paused`, cleared on `playing`/`calibrating`). Every `poll_s`, if `state in ("idle", "paused")` and `clock() - _inactive_since >= close_after_s` and (`receiver.connected` or `chromecast.receiver_app_active`): if paused, snapshot position and `item.status = "ready"`; `menu = None`; `state = "idle"`; `await to_thread(chromecast.quit_app)`; `_inactive_since = None`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_calibrate_while_playing_pauses_saves_position_and_returns_to_menu(monkeypatch):
    # patch calibration.generate_calibration_clip to an AsyncMock returning the path
    # playing "b" at live position 50.0
    await player.calibrate()
    assert player.state == "calibrating" and item_b.playback_position == 50.0
    assert [m for m in drain(q) if m["type"] == "calibration"][-1]["url"].endswith("calibration_pattern.mp4")
    await player.next()  # any key ends it
    msgs = drain(q)
    assert {"type": "calibration", "url": None} in msgs
    assert player.state == "paused" and player.menu.selected == Entry("video", "b")
    assert library.cursor_video_id == "b" and item_b.status != "done"

async def test_calibration_times_out(monkeypatch):
    monkeypatch.setattr("crt.player_core.CALIBRATION_TIMEOUT_S", 0.01)
    await player.calibrate(); await asyncio.sleep(0.05)
    assert player.state in ("paused", "idle")

async def test_live_position_is_zero_while_calibrating():
    assert player.live_position() == 0.0

async def test_receiver_blip_within_grace_keeps_pause_and_resends(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.05)
    # paused on "b" at 120.0
    receiver.unsubscribe(q); q2 = receiver.subscribe(); await asyncio.sleep(0.1)
    msgs = drain(q2)
    load = [m for m in msgs if m["type"] == "load"][-1]
    assert load["autoplay"] is False and load["position"] == 120.0
    assert [m for m in msgs if m["type"] == "menu"][-1]["view"] is not None
    assert player.state == "paused"

async def test_receiver_lost_after_grace_goes_idle_and_keeps_position(monkeypatch):
    monkeypatch.setattr("crt.player_core.RECEIVER_LOST_GRACE_S", 0.01)
    # paused on "b" at 120.0
    receiver.unsubscribe(q); await asyncio.sleep(0.05)
    assert player.state == "idle" and item_b.playback_position == 120.0 and item_b.status == "ready"

async def test_toggle_after_lost_relaunches_and_resumes_from_saved_position():
    # continue from the previous state; cc.launch_receiver side_effect subscribes q3
    await player.toggle()
    cc.launch_receiver.assert_called_once()
    assert [m for m in drain(q3) if m["type"] == "load"][-1]["position"] == 120.0

async def test_watch_idle_closes_app_after_timeout():
    clock = Clock()  # same fake clock as tests/test_receiver.py, injected into PlayerCore
    # paused on "b"; clock.t += 1800
    task = asyncio.create_task(player.watch_idle(1800, poll_s=0.01)); await asyncio.sleep(0.05); task.cancel()
    cc.quit_app.assert_called_once()
    assert player.state == "idle" and item_b.status == "ready"

async def test_watch_idle_leaves_playing_alone():
    # playing; clock far ahead → quit_app not called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_player_core.py -v -k "calibrat or receiver_ or watch_idle or after_lost"`
Expected: FAIL

- [ ] **Step 3: Implement calibration, the connect/disconnect handling and `watch_idle`; add `RECEIVER_IDLE_CLOSE_S` to `crt/config.py`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_player_core.py tests/test_calibration.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/player_core.py crt/config.py tests/test_player_core.py tests/test_calibration.py
git commit -m "feat: calibrazione nella pagina, pagina persa e chiusura per inattività (issue #18)"
```

---

### Task 8: Daemon and `/status` wiring

**Files:**
- Modify: `crt/daemon.py` (`periodic_save_loop`, `main_async`)
- Modify: `crt/api.py` (`/status`)
- Modify: `tests/conftest.py` (`integration_daemon` builds a `ReceiverChannel` and passes it to `PlayerCore` and `create_app`)
- Test: `tests/test_daemon.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `periodic_save_loop(library, position: Callable[[], float], state_file: str, interval_s: float) -> None` (calls `library.save_state(state_file, playback_position=position())`)
  - `/status.player`: `state` = `player.state`; `current_time_s` = `player.live_position()`; `duration_s` = `receiver.duration`; new `"menu_open": player.menu is not None`; `chromecast` unchanged.

`main_async` changes: build `receiver = ReceiverChannel({"top": config.MARGIN_TOP, "bottom": config.MARGIN_BOTTOM, "left": config.MARGIN_LEFT, "right": config.MARGIN_RIGHT})`; pass it to `PlayerCore` and `create_app`; after creating `sync_engine` set `player.on_sync_request = sync_engine.kick`; pass `player.live_position` to `periodic_save_loop`; add task `player.watch_idle(config.RECEIVER_IDLE_CLOSE_S)` named `player_idle_close`; on shutdown save with `player.live_position()` and replace the `cast.quit_app()` block with `chromecast.quit_app()`.

- [ ] **Step 1: Update the daemon tests and write the failing ones**

Port `test_periodic_save_loop_*` to the new `position` callable (the "handles none current_time" test becomes "position returns 0.0 → stored position untouched"). In `tests/test_api.py`:

```python
def test_status_reports_player_position_duration_and_menu(...):
    # player mock: state "paused", live_position() -> 61.5, menu not None; receiver.duration = 600.0
    p = client.get("/status").json()["player"]
    assert p["state"] == "paused" and p["current_time_s"] == 61.5
    assert p["duration_s"] == 600.0 and p["menu_open"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_daemon.py tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the wiring**

- [ ] **Step 4: Run the full unit suite**

Run: `python -m pytest tests/ -q -m "not integration"`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add crt/daemon.py crt/api.py tests/test_daemon.py tests/test_api.py tests/conftest.py
git commit -m "feat: daemon e /status collegati alla pagina receiver"
```

---

### Task 9: Remove the Default Media Receiver path

**Files:**
- Modify: `crt/chromecast_mgr.py` (remove `StatusListener`, `_on_media_status`, `poll_status`, `cast_url`, `stop`, `pause`, `resume`, `pause_or_resume`, `is_session_lost`, `seek`, `seek_to`, `live_current_time`, `seek_relative`, `reset_playback_ended`, `wait_for_playback_end`, the media fields `player_state`/`current_time`/`duration`/`_previous_state`/`_playback_ended_event`, and the `register_status_listener` call; keep discovery, connection events, `adjust_volume`, `launch_receiver`, `receiver_app_active`, `quit_app`, `shutdown`)
- Modify: `crt/pipeline.py` (remove the unused cast loop: `run_cast`, `_cast_and_wait`, `_wait_for_playback_end`, `cancel_cast`, `_cast_enabled`, `_cast_wake`, `_cast_cancel`, the cast half of `wake()` and of `cancel_current()`, the `self._cast_wake.set()` in `run_prepare`; `PipelineWorker` no longer needs `chromecast`, drop the parameter and update `crt/daemon.py` and `tests/conftest.py`)
- Modify: `tests/conftest.py` (the `real_chromecast*` fixtures: drop the per-test recreation of `_playback_ended_event`, and tear down with `quit_app()` instead of `stop()`)
- Test: `tests/test_chromecast_mgr.py` (delete the tests of removed methods), `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Tasks 5-8.
- Produces: `PipelineWorker(queue: LibraryStore)`.

- [ ] **Step 1: Delete the code and the tests of removed methods**

- [ ] **Step 2: Verify nothing references the removed names**

Run: `grep -rnE "cast_url|pause_or_resume|is_session_lost|live_current_time|poll_status|wait_for_playback_end|reset_playback_ended|run_cast|_cast_and_wait|media_controller" crt tui_client tests`
Expected: no output

- [ ] **Step 3: Run the full unit suite**

Run: `python -m pytest tests/ -q -m "not integration"`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add -A crt tests
git commit -m "refactor: tolto il percorso del Default Media Receiver e il cast loop inutilizzato della pipeline"
```

---

### Task 10: Docs, integration test, on-device check

**Files:**
- Modify: `CLAUDE.md` (Architecture data flow, threading model, "Control acks" reasons, Gotchas)
- Modify: `tests/test_integration.py` (`state in ("playing",)`; add a pause → `/status.player.menu_open is True` → toggle → `state == "playing"` check)
- Modify: `../homelab/services/crt-player/CLAUDE.md` only if Giacomo asks (other repo)

**Interfaces:**
- Consumes: everything above.
- Produces: up-to-date agent docs; a verified build on the real TV.

- [ ] **Step 1: Update `CLAUDE.md`**

Replace the gotchas that describe the Default Media Receiver (`media_controller.status.current_time` staleness, `live_current_time` as the one true source, `idle_reason` FINISHED vs CANCELLED, `block_until_active` + `seek_to`, `play_media(current_time=...)`, `pause_or_resume` cached state, `MediaStatusListener` firing only on state changes, pychromecast command timeouts on media commands, Chromecast always outputs 16:9 pillarboxing note stays) with the receiver ones: DashCast `force=True` and the logo on every launch; page is Chrome 90; the 4/3 pre-stretch of the overlay; `PlayerCore.live_position()` is the position source (from page reports, extrapolated by `ReceiverChannel`); `ended` filtered by `src`; reconnect resends `load`; idle close after `CRT_RECEIVER_IDLE_CLOSE_S`; calibration is a page state, not a playlist item. Remove the `action_calibrate` gotcha about the pipeline cast loop (the loop no longer exists). Add `CRT_RECEIVER_IDLE_CLOSE_S` to the env var list. Follow the `writing-for-agents` skill.

- [ ] **Step 2: Run the full unit suite**

Run: `python -m pytest tests/ -q -m "not integration"`
Expected: all PASS

- [ ] **Step 3: On-device check with Giacomo**

The production daemon on Chet drives the same Chromecast: ask Giacomo before stopping it (`ssh flocca@chet.tailc1c72f.ts.net "docker stop lodge-crt-player"`), and only when he says he is in front of the TV. Run the branch locally with `./run.sh` (`.env` with `CRT_CHROMECAST_NAME="TV in Ufficio"`) and the TUI pointed at `http://localhost:8765`. Checklist, each confirmed by Giacomo:
1. First play shows the DashCast logo once, then the video.
2. Pause → menu at once, highlight on the paused video, title and `mm:ss / mm:ss` right, text readable, nothing cut by overscan.
3. Up/Down move the highlight; Left/Right seek and the time updates; OK on the paused video resumes at once without black screen.
4. OK on another ready video plays it; OK on a queued one shows `non ancora pronto` for 2 s.
5. `Elimina video` asks `Confermi? OK sì`; another key cancels (do not confirm on a video he wants to keep).
6. `Toggle loop` and `Sync now` show their notice and keep the menu open.
7. `Calibrate` during playback: pattern, any key returns to the paused menu at the same position.
8. Stop → black screen; next play starts without the logo.
9. Natural end of a short video advances to the next one.
Then restart the production container (`docker start lodge-crt-player`) unless Giacomo says otherwise.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md tests/test_integration.py
git commit -m "docs: CLAUDE.md descrive la riproduzione tramite pagina DashCast"
```

Deploy (`git push` + `lodge crt-player update`) only on Giacomo's explicit go-ahead.
