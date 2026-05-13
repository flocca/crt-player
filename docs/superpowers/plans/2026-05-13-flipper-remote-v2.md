# Flipper Remote v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three new media-remote commands (seek -15s, seek +30s, delete current video) and redesign the Flipper FAP UI with a 90° counter-clockwise rotated layout and an in-app extras menu, end-to-end across daemon, BLE bridge, and FAP.

**Architecture:** Daemon gains three new HTTP endpoints under `/control/*` and YouTube write-scope OAuth. FAP is refactored into a two-scene state machine (`SceneHome`, `SceneExtraMenu`) with `canvas_set_orientation` for the rotated layout. BLE byte protocol extended from 7 to 10 bytes (0x08–0x0A added); framing unchanged.

**Tech Stack:** Python 3.12 + FastAPI + pychromecast + googleapiclient (daemon, this repo), C + ufbt + Flipper SDK (FAP, this repo `flipper_app/`), Python + bleak (bridge, sibling repo `lodge-tools/services/crt-flipper-bridge/`).

**Spec:** [docs/superpowers/specs/2026-05-13-flipper-remote-v2-design.md](../specs/2026-05-13-flipper-remote-v2-design.md)

**Prerequisite reading:** [flipper_app/CLAUDE.md](../../../flipper_app/CLAUDE.md) for the BLE byte mapping and BtSrv-bypass MAC fork rationale; [CLAUDE.md](../../../CLAUDE.md) root for daemon/TUI architecture and Lodge deployment.

**Conventions:**
- Python: type hints (`from __future__ import annotations`), `pytest` + `pytest-asyncio`, `MagicMock` / `AsyncMock` for collaborators. Don't run `pytest` without `source .venv/bin/activate` first; commands assume the venv is sourced.
- One concept per commit; commit after each task's red → green → docs cycle.
- Don't skip tests, don't `--no-verify` past hook failures.

---

## File map (write down once; consult per task)

**Daemon (this repo):**

| File | Action | Why |
|---|---|---|
| `crt/youtube_client.py` | modify | Scope readonly→youtube, add `playlist_item_id` to `PlaylistEntry`, add `delete_playlist_item()`. |
| `crt/library_store.py` | modify | Add `playlist_item_id` to `QueueItem`, add `cursor_item()` helper, bump state version to 3 with back-compat v2 read. |
| `crt/sync_engine.py` | modify | Propagate `playlist_item_id` from `PlaylistEntry` into `QueueItem`. |
| `crt/chromecast_mgr.py` | modify | Add `seek_relative(delta)` method. |
| `crt/player_core.py` | modify | Add `seek_relative(seconds)` + `delete_current()` + `_delete_local()` helper. |
| `crt/api.py` | modify | Add three new `POST /control/...` endpoints. |
| `tests/test_youtube_client.py` | modify | Tests for `delete_playlist_item`, updated `PlaylistEntry` shape. |
| `tests/test_library_store.py` | modify | Tests for `playlist_item_id` field, `cursor_item()` helper. |
| `tests/test_state_v2_migration.py` | modify | Add v2→v3 back-compat read tests. |
| `tests/test_sync_engine.py` | modify | Test `playlist_item_id` propagation. |
| `tests/test_chromecast_mgr.py` | **create** | New file for `seek_relative` unit tests (no existing test for this module). |
| `tests/test_player_core.py` | modify | Tests for `seek_relative` and `delete_current`. |
| `tests/test_api.py` | modify | Tests for three new endpoints. |

**FAP (this repo):**

| File | Action | Why |
|---|---|---|
| `flipper_app/crt_remote_app.c` | modify | Scene model, rotated drawing, new input dispatch, three new CMD_* defines. |

**Cross-repo (lodge-tools repo, sibling):**

| File | Action | Why |
|---|---|---|
| `../lodge-tools/services/crt-flipper-bridge/bridge.py` | modify | Add 3 rows to `COMMAND_TABLE`. |
| `../lodge-tools/services/crt-flipper-bridge/tests/test_bridge.py` *(name approximate)* | modify | Extend `parse_command` fixture with the 3 new bytes. |

**Docs (this repo, post-implementation):**

| File | Action |
|---|---|
| `flipper_app/CLAUDE.md` | modify (Task 9) — new button mapping table, scene model, rotation note. |
| `CLAUDE.md` (root) | modify (Task 9) — new endpoints listed under "Production deployment / HTTP control surface", re-OAuth note. |

---

## Task 1 — YouTube client: write scope + delete_playlist_item

**Files:**
- Modify: `crt/youtube_client.py`
- Modify: `tests/test_youtube_client.py`

### Steps

- [ ] **Step 1: Write failing tests for `PlaylistEntry.playlist_item_id` and `delete_playlist_item`**

Append to `tests/test_youtube_client.py`:

```python
def test_list_playlist_items_populates_playlist_item_id():
    api_mock = MagicMock()
    raw_item = {
        "id": "PLITEM_ID_42",
        "snippet": {
            "title": "Title 1",
            "position": 0,
            "resourceId": {"videoId": "vid1"},
        },
    }
    api_mock.playlistItems.return_value.list.return_value.execute.return_value = {"items": [raw_item]}

    client = YouTubeClient(api_service=api_mock)
    entries = client.list_playlist_items("PLxxx")

    assert entries == [
        PlaylistEntry(video_id="vid1", title="Title 1", position=0, playlist_item_id="PLITEM_ID_42"),
    ]


def test_delete_playlist_item_calls_api():
    api_mock = MagicMock()
    client = YouTubeClient(api_service=api_mock)

    client.delete_playlist_item("PLITEM_42")

    api_mock.playlistItems.return_value.delete.assert_called_once_with(id="PLITEM_42")
    api_mock.playlistItems.return_value.delete.return_value.execute.assert_called_once()


def test_delete_playlist_item_404_is_swallowed():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 404
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"not found"
    )
    client = YouTubeClient(api_service=api_mock)

    # Should not raise
    client.delete_playlist_item("PLITEM_GONE")


def test_delete_playlist_item_401_raises_auth_error():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 401
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"unauthorized"
    )
    client = YouTubeClient(api_service=api_mock)

    with pytest.raises(YouTubeAuthError):
        client.delete_playlist_item("PLITEM_X")


def test_delete_playlist_item_500_propagates():
    api_mock = MagicMock()
    resp = MagicMock()
    resp.status = 500
    api_mock.playlistItems.return_value.delete.return_value.execute.side_effect = HttpError(
        resp=resp, content=b"server error"
    )
    client = YouTubeClient(api_service=api_mock)

    with pytest.raises(HttpError):
        client.delete_playlist_item("PLITEM_X")
```

Update the existing `_build_item` helper in the same file to include the `id` key so the older list tests still work:

```python
def _build_item(video_id, title, position, playlist_item_id=None):
    return {
        "id": playlist_item_id or f"plitem-{video_id}",
        "snippet": {
            "title": title,
            "position": position,
            "resourceId": {"videoId": video_id},
        },
    }
```

And update the existing assertions in `test_list_playlist_items_single_page` / `test_list_playlist_items_paginates` to include `playlist_item_id=f"plitem-{video_id}"` in the expected `PlaylistEntry` instances.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source .venv/bin/activate
python -m pytest tests/test_youtube_client.py -v
```

Expected: the new tests fail with `TypeError: ... unexpected keyword argument 'playlist_item_id'` and `AttributeError: 'YouTubeClient' object has no attribute 'delete_playlist_item'`.

- [ ] **Step 3: Implement scope change + PlaylistEntry field + _list_inner + delete_playlist_item**

Edit `crt/youtube_client.py`:

```python
SCOPES = ["https://www.googleapis.com/auth/youtube"]


@dataclass(frozen=True)
class PlaylistEntry:
    video_id: str
    title: str
    position: int
    playlist_item_id: str
```

In `_list_inner` change the loop body so the `PlaylistEntry` carries `raw["id"]`:

```python
for raw in resp.get("items", []):
    snippet = raw["snippet"]
    entries.append(PlaylistEntry(
        video_id=snippet["resourceId"]["videoId"],
        title=snippet["title"],
        position=snippet["position"],
        playlist_item_id=raw["id"],
    ))
```

Add a new method at the bottom of the `YouTubeClient` class:

```python
def delete_playlist_item(self, playlist_item_id: str) -> None:
    try:
        self._api.playlistItems().delete(id=playlist_item_id).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        if status in (401, 403):
            raise YouTubeAuthError(f"YouTube auth error ({status}): {e}") from e
        if status == 404:
            log.info("playlist item %s already gone (404)", playlist_item_id)
            return
        raise
```

- [ ] **Step 4: Run tests to confirm green**

```bash
python -m pytest tests/test_youtube_client.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add crt/youtube_client.py tests/test_youtube_client.py
git commit -m "youtube_client: write scope + delete_playlist_item + carry playlist_item_id"
```

---

## Task 2 — LibraryStore: playlist_item_id on QueueItem + cursor_item() + state v3

**Files:**
- Modify: `crt/library_store.py`
- Modify: `tests/test_library_store.py`
- Modify: `tests/test_state_v2_migration.py`
- Modify: `tests/test_state_persistence.py`

### Steps

- [ ] **Step 1: Write failing tests for the new field and helper**

Append to `tests/test_library_store.py`:

```python
def test_queue_item_default_playlist_item_id_is_none():
    item = QueueItem(url="https://youtube.com/watch?v=abc")
    assert item.playlist_item_id is None


def test_queue_item_accepts_playlist_item_id():
    item = QueueItem(url="https://youtube.com/watch?v=abc", playlist_item_id="PLITEM_42")
    assert item.playlist_item_id == "PLITEM_42"


def test_cursor_item_returns_item_matching_cursor_video_id():
    ls = LibraryStore()
    a = QueueItem(url="u/A", video_id="A", title="A")
    b = QueueItem(url="u/B", video_id="B", title="B")
    ls.items.extend([a, b])
    ls.cursor_video_id = "B"

    assert ls.cursor_item() is b


def test_cursor_item_returns_none_when_cursor_unset():
    ls = LibraryStore()
    ls.items.append(QueueItem(url="u/A", video_id="A"))
    assert ls.cursor_item() is None


def test_cursor_item_returns_none_when_cursor_video_id_not_in_items():
    ls = LibraryStore()
    ls.items.append(QueueItem(url="u/A", video_id="A"))
    ls.cursor_video_id = "ZZZ"
    assert ls.cursor_item() is None
```

Append to `tests/test_state_persistence.py`:

```python
def test_save_state_writes_version_3(tmp_path):
    path = str(tmp_path / "state.json")
    ls = LibraryStore()
    ls.add("https://youtube.com/watch?v=1")
    ls.save_state(path)

    with open(path) as f:
        data = json.load(f)
    assert data["version"] == 3


def test_queue_item_to_dict_includes_playlist_item_id():
    item = QueueItem(url="u/A", video_id="A", playlist_item_id="PLITEM_42")
    d = item.to_dict()
    assert d["playlist_item_id"] == "PLITEM_42"


def test_queue_item_from_dict_reads_playlist_item_id():
    d = {"url": "u/A", "playlist_item_id": "PLITEM_42"}
    item = QueueItem.from_dict(d)
    assert item.playlist_item_id == "PLITEM_42"


def test_queue_item_from_dict_missing_playlist_item_id_defaults_none():
    d = {"url": "u/A"}
    item = QueueItem.from_dict(d)
    assert item.playlist_item_id is None
```

Append to `tests/test_state_v2_migration.py`:

```python
def test_load_state_v2_treated_as_v3_compatible(tmp_path):
    """v2 state files should load without backup (back-compat read), items get playlist_item_id=None."""
    path = tmp_path / "state.json"
    payload = {
        "version": 2,
        "cursor_video_id": "A",
        "loop_mode": False,
        "items": [
            {"url": "u/A", "video_id": "A", "title": "A", "status": "ready", "filename": "A_pal_crop.mp4"},
        ],
        "history": [],
    }
    path.write_text(json.dumps(payload))

    ls = LibraryStore()
    ls.load_state(str(path))

    assert len(ls.items) == 1
    assert ls.items[0].playlist_item_id is None
    # No backup created on the v2→v3 silent upgrade
    assert not (tmp_path / "state.json.v1.bak").exists()
    assert not (tmp_path / "state.json.v2.bak").exists()


def test_load_state_unknown_version_backs_up_and_resets(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 99, "items": []}))

    ls = LibraryStore()
    ls.load_state(str(path))

    assert len(ls.items) == 0
    assert (tmp_path / "state.json.v1.bak").exists()
```

Also update any existing assertion in `tests/test_state_v2_migration.py` that asserts `data["version"] == 2` after a save — bump it to `== 3`.

- [ ] **Step 2: Run tests to confirm failures**

```bash
python -m pytest tests/test_library_store.py tests/test_state_persistence.py tests/test_state_v2_migration.py -v
```

Expected: new tests fail; older `test_save_state_writes_version_2` (if present in v2 migration file) now also fails because of the version bump assertion you just edited.

- [ ] **Step 3: Implement the field, the helper, the v3 bump, and back-compat read**

Edit `crt/library_store.py`. In the `QueueItem` dataclass add the field:

```python
@dataclass
class QueueItem:
    url: str
    video_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None
    playback_position: float = 0.0
    downloaded_path: str | None = None
    playlist_item_id: str | None = None
```

Extend `to_dict` to include it:

```python
"playlist_item_id": self.playlist_item_id,
```

Extend `from_dict` to read it (defaulting to `None`):

```python
playlist_item_id=d.get("playlist_item_id"),
```

Add the `cursor_item` method to `LibraryStore` (anywhere near `active_item()` is fine):

```python
def cursor_item(self) -> QueueItem | None:
    if self.cursor_video_id is None:
        return None
    return next((i for i in self.items if i.video_id == self.cursor_video_id), None)
```

Bump `save_state` to write `"version": 3`:

```python
data = {
    "version": 3,
    ...
}
```

Modify `load_state` to accept both v2 and v3, only backing up unknown versions:

```python
version = data.get("version", 1)
if version not in (2, 3):
    backup = path + ".v1.bak"
    log.warning(
        "Unknown state version v%s; backing up to %s and starting fresh",
        version, backup,
    )
    os.replace(path, backup)
    return 0.0
```

(The `log.info("Loaded state v2: ...")` line at the bottom of `load_state` can stay as-is or be updated to `"Loaded state v%s"` with the version interpolated; either is fine. Update it if you want clean logs but it's not strictly required.)

- [ ] **Step 4: Run tests to confirm green**

```bash
python -m pytest tests/test_library_store.py tests/test_state_persistence.py tests/test_state_v2_migration.py -v
```

Expected: all green.

- [ ] **Step 5: Run the full suite once to catch any cross-file regression**

```bash
python -m pytest tests/ -v
```

Expected: all green except possibly `test_sync_engine.py` (will be fixed in Task 3 if it references `PlaylistEntry(...)` with the old 3-arg shape). If sync_engine tests fail with a `TypeError` about `playlist_item_id`, that's expected — move on to Task 3.

- [ ] **Step 6: Commit**

```bash
git add crt/library_store.py tests/test_library_store.py tests/test_state_persistence.py tests/test_state_v2_migration.py
git commit -m "library_store: playlist_item_id on QueueItem + cursor_item() + state v3"
```

---

## Task 3 — SyncEngine: propagate playlist_item_id

**Files:**
- Modify: `crt/sync_engine.py`
- Modify: `tests/test_sync_engine.py`

### Steps

- [ ] **Step 1: Identify where `PlaylistEntry` becomes `QueueItem`**

Read `crt/sync_engine.py` (it's ~170 lines). Find the block where the engine iterates `PlaylistEntry` from `youtube_client.list_playlist_items()` and creates/updates `QueueItem`s in `library`. Typically it's the function that handles a successful poll. The variable will be something like `entry` (a `PlaylistEntry`).

- [ ] **Step 2: Write failing test that the sync sets `playlist_item_id` on new items**

Append to `tests/test_sync_engine.py` (use the existing fixtures/patterns — mock the YouTube client to return a known `PlaylistEntry` list):

```python
def test_sync_populates_playlist_item_id_on_new_items():
    from crt.youtube_client import PlaylistEntry
    from crt.library_store import LibraryStore
    from crt.sync_engine import SyncEngine  # or whatever the exported class is

    library = LibraryStore()
    yt = MagicMock()
    yt.list_playlist_items.return_value = [
        PlaylistEntry(video_id="A", title="Alpha", position=0, playlist_item_id="PLITEM_A"),
    ]
    engine = SyncEngine(library=library, youtube_client=yt, playlist_id="PL_X")
    # If the engine has an explicit sync method, call it directly. Otherwise call the
    # internal one used by the existing tests in this file (do NOT invent a new entry point).
    engine._do_sync()  # ← replace with the actual sync method name found in the file

    assert library.items[0].playlist_item_id == "PLITEM_A"
```

If the existing tests in `tests/test_sync_engine.py` already construct `PlaylistEntry(...)` with the old 3-argument shape, update those call sites to add `playlist_item_id="..."`. Use distinct values so a future failure is obvious (e.g. `f"plitem-{video_id}"`).

- [ ] **Step 3: Run test to confirm it fails**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: the new assertion fails (item's `playlist_item_id` is `None`), or an older test fails with `TypeError` due to the new required-keyword on `PlaylistEntry`.

- [ ] **Step 4: Implement the propagation**

In `crt/sync_engine.py`, find the line(s) where a `QueueItem` is created from a `PlaylistEntry`. Add `playlist_item_id=entry.playlist_item_id` to the constructor call. If the engine instead updates existing items in-place, also set `existing.playlist_item_id = entry.playlist_item_id` so re-syncs keep the latest ID.

- [ ] **Step 5: Run test to confirm green**

```bash
python -m pytest tests/test_sync_engine.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add crt/sync_engine.py tests/test_sync_engine.py
git commit -m "sync_engine: propagate playlist_item_id from PlaylistEntry to QueueItem"
```

---

## Task 4 — ChromecastManager: seek_relative

**Files:**
- Modify: `crt/chromecast_mgr.py`
- Create: `tests/test_chromecast_mgr.py`

### Steps

- [ ] **Step 1: Write failing tests in a new test file**

Create `tests/test_chromecast_mgr.py`:

```python
from unittest.mock import MagicMock

import pytest

from crt.chromecast_mgr import ChromecastManager


def _make_manager(current_time):
    """Build a ChromecastManager with the cast machinery mocked out."""
    mgr = ChromecastManager.__new__(ChromecastManager)  # skip __init__
    mgr.cast = MagicMock()
    mgr.cast.media_controller = MagicMock()
    mgr.current_time = current_time
    return mgr


def test_seek_relative_forward_calls_seek_with_sum():
    mgr = _make_manager(current_time=10.0)
    mgr.seek_relative(30)
    mgr.cast.media_controller.seek.assert_called_once_with(40.0)


def test_seek_relative_backward_calls_seek_with_difference():
    mgr = _make_manager(current_time=60.0)
    mgr.seek_relative(-15)
    mgr.cast.media_controller.seek.assert_called_once_with(45.0)


def test_seek_relative_backward_clamps_to_zero():
    mgr = _make_manager(current_time=5.0)
    mgr.seek_relative(-15)
    mgr.cast.media_controller.seek.assert_called_once_with(0.0)


def test_seek_relative_with_none_current_time_is_noop():
    mgr = _make_manager(current_time=None)
    mgr.seek_relative(30)
    mgr.cast.media_controller.seek.assert_not_called()
```

The bypass via `__new__` avoids triggering pychromecast discovery in `__init__`. If `ChromecastManager` is structured so that `_safe_cmd` is what wraps the seek call, the test assertion above (`mgr.cast.media_controller.seek.assert_called_once_with(...)`) still passes because `_safe_cmd` ultimately calls the lambda, which calls `seek()`. Confirm during implementation that the call path reaches the same mock.

- [ ] **Step 2: Run tests to confirm failure**

```bash
python -m pytest tests/test_chromecast_mgr.py -v
```

Expected: `AttributeError: 'ChromecastManager' object has no attribute 'seek_relative'`.

- [ ] **Step 3: Implement seek_relative**

Add to `crt/chromecast_mgr.py` near the existing `seek` wrappers:

```python
def seek_relative(self, delta_seconds: float) -> None:
    if self.current_time is None:
        log.info("seek_relative: no current_time, skipping")
        return
    new_pos = max(0.0, self.current_time + delta_seconds)
    self._safe_cmd(lambda: self.cast.media_controller.seek(new_pos))
```

- [ ] **Step 4: Run tests to confirm green**

```bash
python -m pytest tests/test_chromecast_mgr.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add crt/chromecast_mgr.py tests/test_chromecast_mgr.py
git commit -m "chromecast_mgr: seek_relative with clamp-to-zero and current_time guard"
```

---

## Task 5 — PlayerCore: seek_relative + delete_current

**Files:**
- Modify: `crt/player_core.py`
- Modify: `tests/test_player_core.py`

### Steps

- [ ] **Step 1: Write failing tests for seek_relative and delete_current**

Append to `tests/test_player_core.py`:

```python
@pytest.mark.asyncio
async def test_seek_relative_calls_chromecast_via_to_thread():
    library = _make_library(["A"])
    cc = _make_chromecast()
    cc.seek_relative = MagicMock()
    pc = PlayerCore(library, cc)

    await pc.seek_relative(-15)

    cc.seek_relative.assert_called_once_with(-15)


@pytest.mark.asyncio
async def test_delete_current_full_path(tmp_path, monkeypatch):
    import crt.config as cfg
    import os

    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A", "B"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLITEM_A"
    library.items[0].filename = "A_pal_crop.mp4"  # matches cached_encoded_filename("A")
    cache_file = tmp_path / "A_pal_crop.mp4"
    cache_file.write_text("dummy")

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)

    await pc.delete_current()

    # local removal
    assert all(i.video_id != "A" for i in library.items)
    assert not cache_file.exists()
    # remote removal
    yt.delete_playlist_item.assert_called_once_with("PLITEM_A")
    # stop was called
    cc.stop.assert_called()


@pytest.mark.asyncio
async def test_delete_current_missing_playlist_item_id_skips_remote(tmp_path, monkeypatch):
    import crt.config as cfg
    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = None

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)

    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)
    yt.delete_playlist_item.assert_not_called()


@pytest.mark.asyncio
async def test_delete_current_youtube_failure_keeps_local_removal(tmp_path, monkeypatch):
    import crt.config as cfg
    monkeypatch.setattr(cfg, "TEMP_DIR", str(tmp_path))

    library = _make_library(["A"])
    library.cursor_video_id = "A"
    library.items[0].playlist_item_id = "PLITEM_A"

    cc = _make_chromecast()
    yt = MagicMock()
    yt.delete_playlist_item.side_effect = RuntimeError("boom")
    pc = PlayerCore(library, cc, youtube_client=yt)

    # Should NOT raise
    await pc.delete_current()

    assert all(i.video_id != "A" for i in library.items)


@pytest.mark.asyncio
async def test_delete_current_with_no_cursor_is_noop():
    library = _make_library(["A"])
    library.cursor_video_id = None

    cc = _make_chromecast()
    yt = MagicMock()
    pc = PlayerCore(library, cc, youtube_client=yt)

    await pc.delete_current()  # should not raise

    yt.delete_playlist_item.assert_not_called()
    assert len(library.items) == 1
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
python -m pytest tests/test_player_core.py -v -k "seek_relative or delete_current"
```

Expected: failures because `PlayerCore` doesn't have `seek_relative`/`delete_current` and may not accept `youtube_client=` in its constructor.

- [ ] **Step 3: Implement in `crt/player_core.py`**

Update the `PlayerCore.__init__` signature to accept an optional `youtube_client`:

```python
def __init__(self, library, chromecast, youtube_client=None):
    self.library = library
    self.chromecast = chromecast
    self.youtube = youtube_client
    # ... preserve existing init body ...
```

Add the two new methods:

```python
async def seek_relative(self, seconds: int) -> None:
    await asyncio.to_thread(self.chromecast.seek_relative, seconds)

async def delete_current(self) -> None:
    item = self.library.cursor_item()
    if item is None:
        log.info("delete_current: no cursor item, skipping")
        return
    await self.stop()
    await asyncio.to_thread(self._delete_local, item)
    if item.playlist_item_id and self.youtube is not None:
        try:
            await asyncio.to_thread(self.youtube.delete_playlist_item, item.playlist_item_id)
        except Exception as e:
            log.error("YouTube remote delete failed for %s: %s", item.video_id, e)
    elif self.youtube is None:
        log.warning("delete_current: no youtube_client, remote delete skipped")
    else:
        log.warning("delete_current: playlist_item_id missing for %s; remote delete skipped", item.video_id)

def _delete_local(self, item) -> None:
    import os
    from crt import config
    self.library.remove(item.id)
    cache_path = os.path.join(config.TEMP_DIR, config.cached_encoded_filename(item.video_id))
    if os.path.isfile(cache_path):
        try:
            os.unlink(cache_path)
        except OSError as e:
            log.warning("Failed to unlink cache %s: %s", cache_path, e)
```

Wire `youtube_client` through in `crt/daemon.py`. The existing order creates `yt_client` (line ~68) *after* `PlayerCore` (line 57). Reorder so the YouTube client is built first, then pass it to `PlayerCore`:

```python
# Before (current):
chromecast = ChromecastManager()
pipeline = PipelineWorker(library, chromecast)
player = PlayerCore(library, chromecast)
...
sync_engine = None
if config.YT_PLAYLIST_ID:
    try:
        yt_client = YouTubeClient.from_token_file(...)
        ...
        sync_engine = SyncEngine(library, yt_client, ...)
    except (YouTubeAuthError, FileNotFoundError) as e:
        log.warning("SyncEngine disabled: %s", e)

# After:
chromecast = ChromecastManager()
pipeline = PipelineWorker(library, chromecast)

yt_client = None
if config.YT_PLAYLIST_ID:
    try:
        yt_client = YouTubeClient.from_token_file(config.YT_TOKEN_FILE, config.YT_CLIENT_SECRETS)
    except (YouTubeAuthError, FileNotFoundError) as e:
        log.warning("YouTube client unavailable, remote delete + sync disabled: %s", e)

player = PlayerCore(library, chromecast, youtube_client=yt_client)

sync_engine = None
if yt_client is not None:
    def _on_yt_remove(video_id: str):
        asyncio.run_coroutine_threadsafe(player.stop_and_remove(video_id), main_loop)
    def _on_yt_add():
        pipeline.wake_prepare()
    sync_engine = SyncEngine(
        library, yt_client, config.YT_PLAYLIST_ID,
        on_remove=_on_yt_remove,
        on_add=_on_yt_add,
    )
    log.info("SyncEngine ready (playlist=%s)", config.YT_PLAYLIST_ID)
```

Note that `main_loop = asyncio.get_running_loop()` must stay before the closure definitions; keep it in place.

- [ ] **Step 4: Run tests to confirm green**

```bash
python -m pytest tests/test_player_core.py -v
```

Expected: all green (including the older tests that don't pass `youtube_client=` because it now has a `None` default).

- [ ] **Step 5: Commit**

```bash
git add crt/player_core.py crt/daemon.py tests/test_player_core.py
git commit -m "player_core: seek_relative + delete_current with remote YouTube cleanup"
```

---

## Task 6 — API: three new control endpoints

**Files:**
- Modify: `crt/api.py`
- Modify: `tests/test_api.py`

### Steps

- [ ] **Step 1: Write failing tests for the three endpoints**

Append to `tests/test_api.py`:

```python
# ─── /control/seek/* ──────────────────────────────────────────────

def test_seek_back_calls_player_with_negative_seconds():
    library = LibraryStore()
    player = MagicMock()
    player.seek_relative = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/seek/back/15")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    player.seek_relative.assert_awaited_once_with(-15)


def test_seek_forward_calls_player_with_positive_seconds():
    library = LibraryStore()
    player = MagicMock()
    player.seek_relative = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/seek/forward/30")

    assert resp.status_code == 200
    player.seek_relative.assert_awaited_once_with(30)


def test_seek_back_503_when_no_player():
    library = LibraryStore()
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/seek/back/15")

    assert resp.status_code == 503


# ─── /control/delete/current ──────────────────────────────────────

def test_delete_current_success():
    library = LibraryStore()
    library.items.append(QueueItem(url="u/A", video_id="A", title="A"))
    library.cursor_video_id = "A"
    player = MagicMock()
    player.delete_current = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted_video_id": "A"}
    player.delete_current.assert_awaited_once()


def test_delete_current_404_when_no_cursor():
    library = LibraryStore()  # cursor unset
    player = MagicMock()
    player.delete_current = AsyncMock()
    client = TestClient(_make_app(library, player=player))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 404
    player.delete_current.assert_not_awaited()


def test_delete_current_503_when_no_player():
    library = LibraryStore()
    library.cursor_video_id = "A"
    client = TestClient(_make_app(library, player=None))

    resp = client.post("/control/delete/current")

    assert resp.status_code == 503
```

If `AsyncMock` isn't already imported at the top of the file, add `from unittest.mock import AsyncMock, MagicMock`.

- [ ] **Step 2: Run tests to confirm failure**

```bash
python -m pytest tests/test_api.py -v -k "seek or delete"
```

Expected: 404s on the new paths because the endpoints don't exist yet.

- [ ] **Step 3: Implement the endpoints**

Add to `crt/api.py` before the `# ─── Media file serving ──` section:

```python
@app.post("/control/seek/back/{seconds}")
async def control_seek_back(seconds: int):
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.seek_relative(-seconds)
    return {"ok": True}


@app.post("/control/seek/forward/{seconds}")
async def control_seek_forward(seconds: int):
    if player is None:
        raise HTTPException(503, "player unavailable")
    await player.seek_relative(seconds)
    return {"ok": True}


@app.post("/control/delete/current")
async def control_delete_current():
    if player is None:
        raise HTTPException(503, "player unavailable")
    video_id = library.cursor_video_id
    if not video_id:
        raise HTTPException(404, "no current video")
    await player.delete_current()
    return {"ok": True, "deleted_video_id": video_id}
```

- [ ] **Step 4: Run tests to confirm green**

```bash
python -m pytest tests/test_api.py -v
```

Expected: all green.

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
python -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add crt/api.py tests/test_api.py
git commit -m "api: /control/seek/{back,forward}/{n} and /control/delete/current"
```

---

## Task 7 — Bridge: extend COMMAND_TABLE (lodge-tools repo)

**Files (in sibling repo `../lodge-tools/`):**
- Modify: `../lodge-tools/services/crt-flipper-bridge/bridge.py`
- Modify: bridge tests file (locate it; conventional path: `../lodge-tools/services/crt-flipper-bridge/test_bridge.py` or `tests/test_bridge.py`)

This task crosses repos. The crt-player repo doesn't see lodge-tools changes; commit there separately. Before starting, confirm with `ls ../lodge-tools/services/crt-flipper-bridge/` that the sibling repo is checked out.

### Steps

- [ ] **Step 1: Locate `COMMAND_TABLE` and tests**

```bash
cd ../lodge-tools/services/crt-flipper-bridge
grep -n "COMMAND_TABLE\|parse_command" bridge.py *.py
ls -la
```

Note the exact path of the test file; the rest of the steps assume `test_bridge.py` — adjust if it's `tests/test_bridge.py` or similar.

- [ ] **Step 2: Add failing test rows for the three new bytes**

In the bridge's test file, find the parametrize/fixture that exercises `parse_command` and add three rows:

```python
(0x08, "/control/seek/back/15"),
(0x09, "/control/seek/forward/30"),
(0x0A, "/control/delete/current"),
```

If the test file uses one `@pytest.mark.parametrize` decorator with a list, append to that list. If it uses individual functions, add three new test functions following the same pattern.

- [ ] **Step 3: Run tests to confirm failures**

```bash
python -m pytest -v
```

Expected: the three new cases fail because `parse_command` returns `None` for unknown bytes.

- [ ] **Step 4: Add the three rows to `COMMAND_TABLE` in `bridge.py`**

```python
COMMAND_TABLE = {
    0x01: "/control/next",
    0x02: "/control/prev",
    0x03: "/control/toggle",
    0x04: "/control/stop",
    0x05: "/control/loop/toggle",
    0x06: "/control/sync",
    0x07: "/control/calibrate",
    0x08: "/control/seek/back/15",
    0x09: "/control/seek/forward/30",
    0x0A: "/control/delete/current",
}
```

(Match existing ordering/style if the file has it.)

- [ ] **Step 5: Run tests to confirm green**

```bash
python -m pytest -v
```

Expected: all green.

- [ ] **Step 6: Commit in lodge-tools repo**

```bash
git add services/crt-flipper-bridge/bridge.py services/crt-flipper-bridge/test_bridge.py  # adjust paths
git commit -m "crt-flipper-bridge: add seek and delete commands (0x08-0x0A)"
```

Push when convenient — deploy happens in Task 10. Return to the crt-player working dir:

```bash
cd -
```

---

## Task 8 — FAP: rotation, scene model, new commands, drawing

**Files:**
- Modify: `flipper_app/crt_remote_app.c`

No unit tests (toolchain limitation). Validation is on-device smoke at Step 5.

This task touches one file but is bigger than a typical TDD red-green. Treat it as: write the full refactor in one shot, build clean, then validate on-device.

### Steps

- [ ] **Step 1: Add new constants and types**

Open `flipper_app/crt_remote_app.c`. Just below the existing `#define CMD_CALIBRATE 0x07` line, add:

```c
#define CMD_SEEK_BACK_15     0x08
#define CMD_SEEK_FORWARD_30  0x09
#define CMD_DELETE           0x0A
```

Below the `BleState` enum, add the scene enum, menu item type, and menu table:

```c
typedef enum {
    SceneHome = 0,
    SceneExtraMenu,
} Scene;

typedef struct {
    const char* label;
    uint8_t cmd_byte;
} MenuItem;

static const MenuItem MENU_ITEMS[] = {
    {"Stop",          CMD_STOP},
    {"Elimina video", CMD_DELETE},
    {"Calibrate",     CMD_CALIBRATE},
    {"Toggle loop",   CMD_LOOP},
    {"Sync now",      CMD_SYNC},
};
#define MENU_ITEMS_COUNT (sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]))
```

Extend `CrtRemoteApp` struct with two new fields:

```c
typedef struct {
    FuriMessageQueue* input_queue;
    ViewPort* view_port;
    Gui* gui;
    Bt* bt;
    FuriHalBleProfileBase* profile;
    BleState ble_state;
    Scene scene;
    uint8_t menu_index;
} CrtRemoteApp;
```

The struct already zero-initializes (`CrtRemoteApp app = {0};` in `crt_remote_app`), so `scene = SceneHome` and `menu_index = 0` at startup with no extra code.

- [ ] **Step 2: Replace `draw_callback` with a scene-aware drawer that rotates the canvas**

Replace the entire existing `draw_callback` function with:

```c
static void draw_home(Canvas* canvas, CrtRemoteApp* app) {
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 32, 10, AlignCenter, AlignTop, "CRT Remote");

    canvas_set_font(canvas, FontSecondary);
    const char* state_line;
    switch(app->ble_state) {
        case BleStateActive:   state_line = "BLE: active";   break;
        case BleStateFailed:   state_line = "BLE: failed";   break;
        case BleStateStarting:
        default:               state_line = "BLE: starting"; break;
    }
    canvas_draw_str_aligned(canvas, 32, 22, AlignCenter, AlignTop, state_line);

    canvas_draw_str(canvas, 4, 42, "< -15s");
    canvas_draw_str(canvas, 4, 54, "> +30s");
    canvas_draw_str(canvas, 4, 66, "^ prev");
    canvas_draw_str(canvas, 4, 78, "v next");

    canvas_draw_str_aligned(canvas, 32, 100, AlignCenter, AlignTop, "OK = play/pause");
    canvas_draw_str_aligned(canvas, 32, 115, AlignCenter, AlignTop, "hold OK: extras");
}

static void draw_extra_menu(Canvas* canvas, CrtRemoteApp* app) {
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 32, 10, AlignCenter, AlignTop, "Comandi");

    canvas_set_font(canvas, FontSecondary);
    const int y_base = 30;
    const int y_step = 12;
    for(size_t i = 0; i < MENU_ITEMS_COUNT; i++) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%s %s",
                 (i == app->menu_index) ? ">" : " ",
                 MENU_ITEMS[i].label);
        canvas_draw_str(canvas, 4, y_base + (int)i * y_step, buf);
    }

    canvas_draw_str_aligned(canvas, 32, 110, AlignCenter, AlignTop, "OK conferma");
    canvas_draw_str_aligned(canvas, 32, 120, AlignCenter, AlignTop, "Back annulla");
}

static void draw_callback(Canvas* canvas, void* ctx) {
    CrtRemoteApp* app = ctx;
    canvas_set_orientation(canvas, CanvasOrientationVertical);
    canvas_clear(canvas);
    if(app->scene == SceneExtraMenu) {
        draw_extra_menu(canvas, app);
    } else {
        draw_home(canvas, app);
    }
}
```

Use plain ASCII `<`, `>`, `^`, `v` for the direction labels in the home view — UTF-8 arrow glyphs may not render in the Flipper's default font. If they do render (verify at Step 5), swap to `◀▶▲▼`.

**Empirical rotation note:** the spec assumes `CanvasOrientationVertical` is 90° counter-clockwise. If on-device the rotation is wrong-way (the user holds the Flipper as designed but the display reads as 90° CW), swap to `CanvasOrientationVerticalFlip`. This is a one-line change and is the only thing to fix.

- [ ] **Step 3: Replace the input loop body with a scene-aware dispatch**

In `crt_remote_app()`, replace the `while(running)` body — everything inside `if(furi_message_queue_get(...) == FuriStatusOk)` — with:

```c
if(event.type == InputTypeShort) {
    if(app.scene == SceneHome) {
        switch(event.key) {
            case InputKeyUp:    ble_serial_send_byte(&app, CMD_SEEK_BACK_15);    break;
            case InputKeyDown:  ble_serial_send_byte(&app, CMD_SEEK_FORWARD_30); break;
            case InputKeyLeft:  ble_serial_send_byte(&app, CMD_NEXT);            break;
            case InputKeyRight: ble_serial_send_byte(&app, CMD_PREV);            break;
            case InputKeyOk:    ble_serial_send_byte(&app, CMD_TOGGLE);          break;
            case InputKeyBack:  running = false;                                 break;
            default: break;
        }
    } else { // SceneExtraMenu
        switch(event.key) {
            case InputKeyRight: // user "Up"
                if(app.menu_index > 0) app.menu_index--;
                view_port_update(app.view_port);
                break;
            case InputKeyLeft: // user "Down"
                if(app.menu_index + 1 < MENU_ITEMS_COUNT) app.menu_index++;
                view_port_update(app.view_port);
                break;
            case InputKeyOk:
                ble_serial_send_byte(&app, MENU_ITEMS[app.menu_index].cmd_byte);
                app.scene = SceneHome;
                view_port_update(app.view_port);
                break;
            case InputKeyBack:
                app.scene = SceneHome;
                view_port_update(app.view_port);
                break;
            default: break;
        }
    }
} else if(event.type == InputTypeLong) {
    if(app.scene == SceneHome && event.key == InputKeyOk) {
        app.scene = SceneExtraMenu;
        app.menu_index = 0;
        view_port_update(app.view_port);
    }
    // Long-press Back in either scene is intentionally unbound (was STOP in v1).
}
```

The previous `InputTypeLong InputKeyBack → CMD_STOP` mapping is removed because `Stop` now lives in the extras menu. The previous `InputTypeLong InputKeyOk → CMD_CALIBRATE` is removed because long-press OK now opens the menu (Calibrate is a menu entry).

- [ ] **Step 4: Build clean**

```bash
cd flipper_app
ufbt
```

Expected: `dist/crt_remote.fap` produced with no warnings. If build fails, fix C compile errors. Common pitfalls:
- Missing `#include <stdio.h>` for `snprintf` — Flipper SDK pulls it transitively via `<furi.h>`, but if the compiler complains, add it.
- `CanvasOrientationVertical` not found → the enum is `CanvasOrientation` in `gui/canvas.h`. Verify spelling.

- [ ] **Step 5: Flash + on-device smoke test**

```bash
ufbt launch
```

Quit qFlipper first if it's running (otherwise USB CLI is taken and launch hangs — see [flipper_app/CLAUDE.md](../../../flipper_app/CLAUDE.md)).

On the Flipper, hold it in the intended ruotated orientation (90° CCW; the original right edge is now the top). Verify:

- [ ] Home screen draws right-side up (text reads naturally for the user holding rotated).
- [ ] Each short-press in `SceneHome` sends the expected byte: cross-check with bridge logs on Lodge (`docker logs lodge-crt-flipper-bridge --tail 20` after each press). Expected pairs:
  - physical Up → byte 0x08
  - physical Down → byte 0x09
  - physical Left → byte 0x01
  - physical Right → byte 0x02
  - physical OK short → byte 0x03
- [ ] Long-press OK on Home → menu appears with `> Stop` selected.
- [ ] In menu, physical Right (user "Up") moves cursor up, physical Left (user "Down") moves cursor down. Cursor doesn't wrap or run past the bounds.
- [ ] OK on each menu item sends the correct byte and returns to Home. Expected pairs:
  - `Stop` → 0x04
  - `Elimina video` → 0x0A
  - `Calibrate` → 0x07
  - `Toggle loop` → 0x05
  - `Sync now` → 0x06
- [ ] Back in menu returns to Home with no byte sent.
- [ ] Back on Home exits the app.

If the rotation reads the wrong way, change `CanvasOrientationVertical` to `CanvasOrientationVerticalFlip` and re-flash.

If glyphs render as boxes/garbage, leave the ASCII fallbacks.

- [ ] **Step 6: Commit**

```bash
cd ..
git add flipper_app/crt_remote_app.c
git commit -m "flipper_app: rotated UI + extras menu + seek/delete commands"
```

---

## Task 9 — Doc updates

**Files:**
- Modify: `flipper_app/CLAUDE.md`
- Modify: `CLAUDE.md` (root)

No tests; the docs are reference material. Skip for `lodge-tools` CLAUDE.md updates here — those live in the sibling repo and should be done in Task 7's commit batch if possible.

### Steps

- [ ] **Step 1: Update `flipper_app/CLAUDE.md` button mapping table**

Find the "Button → command byte mapping" section. Replace the table with the rotated mapping:

```markdown
The FAP runs in two scenes — `SceneHome` and `SceneExtraMenu` — selected by long-press OK on Home. Mapping below is from the user's POV with the Flipper rotated 90° counter-clockwise (original right edge becomes the user's top).

### SceneHome

| Physical key | User sees | Byte | Bridge endpoint |
|---|---|---|---|
| Up (short) | "Left" | `0x08` | `/control/seek/back/15` |
| Down (short) | "Right" | `0x09` | `/control/seek/forward/30` |
| Left (short) | "Down" | `0x01` | `/control/next` |
| Right (short) | "Up" | `0x02` | `/control/prev` |
| OK (short) | OK | `0x03` | `/control/toggle` |
| OK (long) | OK held | — | enters `SceneExtraMenu` (in-FAP only) |
| Back (short) | Back | — | exit app |

### SceneExtraMenu

| Physical key | Action |
|---|---|
| Right (short) — user "Up" | move cursor up |
| Left (short) — user "Down" | move cursor down |
| OK (short) | send selected byte, return to `SceneHome` |
| Back (short) | return to `SceneHome` without sending |

Menu entries (hardcoded order): `Stop` (0x04), `Elimina video` (0x0A), `Calibrate` (0x07), `Toggle loop` (0x05), `Sync now` (0x06).

`Stop`/`Loop`/`Sync`/`Calibrate` no longer have dedicated keys — all four moved into the extras menu in v2.
```

Also add a short subsection under "Architecture" (above "Forked Serial profile"):

```markdown
### Display rotation

`draw_callback` first calls `canvas_set_orientation(canvas, CanvasOrientationVertical)`, so all subsequent drawing uses logical coordinates 64×128 (user POV with the Flipper held 90° CCW from default). If on-device tests show the rotation runs the wrong way, swap to `CanvasOrientationVerticalFlip` — Flipper SDK does not document which enum corresponds to which direction.
```

- [ ] **Step 2: Update root `CLAUDE.md`**

Find the "HTTP control surface" sentence under "Production deployment (Lodge)". Replace the endpoint list:

```markdown
**HTTP control surface** consumed by TUI + Flipper bridge: `GET /status`, `GET /library/items`, `POST /control/{next,prev,toggle,stop,loop/toggle,sync,calibrate,seek/back/{n},seek/forward/{n},delete/current}`.
```

Find the "OAuth bootstrap" paragraph. Add a sentence at the end:

```markdown
As of v2 (2026-05-13) the OAuth scope is `youtube` (full read+write) instead of `youtube.readonly` — required so `/control/delete/current` can remove the item from the YouTube playlist. A scope-bumped re-bootstrap is required when upgrading from a v1 deployment: re-run `python -m crt.bootstrap` on the Mac, then `lodge crt-player install` (or `scp oauth_token.json` to `/opt/lodge/crt-player/secrets/`) and restart the container.
```

Find the Flipper-related bullet in the Gotchas section. Replace the byte mapping enumeration with:

```markdown
**Flipper command byte → HTTP endpoint** (in `../lodge-tools/services/crt-flipper-bridge/bridge.py` COMMAND_TABLE): `0x01`→next, `0x02`→prev, `0x03`→toggle, `0x04`→stop, `0x05`→loop/toggle, `0x06`→sync, `0x07`→calibrate, `0x08`→seek/back/15, `0x09`→seek/forward/30, `0x0A`→delete/current.
```

- [ ] **Step 3: Commit**

```bash
git add flipper_app/CLAUDE.md CLAUDE.md
git commit -m "docs: update CLAUDE.md for Flipper remote v2 (rotated UI + seek/delete)"
```

---

## Task 10 — Rollout (manual)

No code; this is the checklist the operator runs after the previous tasks land. Document execution in commits or a separate ops log; do not auto-run from this plan.

### Steps

- [ ] **Step 1: Deploy daemon to Lodge**

```bash
cd ../lodge-tools
lodge crt-player update    # builds + restarts container
lodge crt-player logs --tail 50
```

Verify: container restarts cleanly, `/status` responds 200. The new endpoints are reachable but not yet exercised (bridge still v1).

- [ ] **Step 2: Re-OAuth with the new write scope**

On the Mac:

```bash
cd ~/src/crt-player
source .venv/bin/activate
python -m crt.bootstrap
```

Browser opens to Google consent screen. Confirm the requested scope now includes "Manage your YouTube account" (or similar wording for full `youtube` scope). Authorize. The script writes `~/.local/share/crt-player/oauth_token.json`.

Distribute to Lodge:

```bash
cd ../lodge-tools
lodge crt-player install   # picks up the new token + scp's it
# OR manually:
scp ~/.local/share/crt-player/oauth_token.json lodge:/opt/lodge/crt-player/secrets/
ssh lodge "sudo chown root:root /opt/lodge/crt-player/secrets/oauth_token.json && sudo chmod 600 /opt/lodge/crt-player/secrets/oauth_token.json"
lodge crt-player restart
lodge crt-player logs --tail 50
```

Verify in logs: a subsequent sync cycle (default every 5 min, or trigger immediately via `curl -X POST http://lodge.<tailnet>.ts.net:8765/control/sync`) succeeds without 401/403.

- [ ] **Step 3: Deploy bridge to Lodge**

```bash
cd ../lodge-tools
lodge crt-flipper-bridge update
lodge crt-flipper-bridge logs --tail 50
```

Verify: bridge reconnects to the Flipper MAC and logs the COMMAND_TABLE size or equivalent startup line.

- [ ] **Step 4: Flash the new FAP**

On the Mac with Flipper connected via USB:

```bash
cd ~/src/crt-player/flipper_app
ufbt launch
```

Quit qFlipper first if running. Verify the new home screen renders rotated.

- [ ] **Step 5: End-to-end smoke**

Cue up a video (use `crt-tui` to add a YouTube URL, or kick a sync). Once a video is playing on the CRT:

- [ ] Up on Flipper → playback jumps back ~15s.
- [ ] Down on Flipper → playback jumps forward ~30s.
- [ ] Left → next video starts.
- [ ] Right → previous video.
- [ ] OK → pauses, OK again → resumes.
- [ ] Long-press OK → extras menu appears.
- [ ] In menu, select "Elimina video" → cast stops, item disappears from `crt-tui` library, item disappears from the YouTube playlist (verify in YouTube web UI).
- [ ] In menu, select "Calibrate" → calibration grid appears on the CRT.
- [ ] In menu, select "Toggle loop" / "Sync now" → behavior matches v1 toggles.

Any failure here points to a specific layer:
- Wrong byte arrives at bridge → FAP issue (Task 8).
- Right byte at bridge but no HTTP call → bridge issue (Task 7).
- HTTP 404/500 → daemon issue (Tasks 1–6).
- HTTP 200 but no visible action → pychromecast / hardware issue (out of scope; check `lodge crt-player logs` for `seek_relative`, `delete_current`, `_safe_cmd` log lines).

---

## Self-review checklist (run before declaring done)

- [ ] All 14 daemon-side tests added by this plan pass.
- [ ] Full `python -m pytest tests/ -v` passes with no regressions outside the plan's scope.
- [ ] State file v3 round-trips correctly; an existing v2 state file loads without backup.
- [ ] Bridge `COMMAND_TABLE` and FAP `CMD_*` defines agree on all 10 byte values.
- [ ] FAP builds cleanly with `ufbt`.
- [ ] On-device smoke test in Task 8 Step 5 passes every checkbox.
- [ ] End-to-end smoke in Task 10 Step 5 passes every checkbox.
- [ ] `flipper_app/CLAUDE.md` and root `CLAUDE.md` reflect the new mapping and endpoints.
