# Playlist Cursor + Queue Reorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the CRT Player queue into a full-featured playlist: free reordering via ↑/↓ buttons per row, `done` as an informational label rather than a terminal state, implicit playback cursor, and an optional loop mode.

**Architecture:** New helpers on `QueueManager` (`advance_cursor`, `prepare_for_play`, `can_move`, `first_queued_after_cursor`, `first_ready`) replace the old `next_ready` terminal logic. The pipeline's cast loop delegates next-item selection to `advance_cursor`. The UI's `QueueListItem` gains an inline `Horizontal` with two `Button` widgets; `CRTCastApp` handles `Button.Pressed` and exposes a `ctrl+r` loop toggle.

**Tech Stack:** Python 3.11, Textual 0.x, pytest-asyncio, unittest.mock

**Spec:** `docs/superpowers/specs/2026-04-14-playlist-cursor-and-reorder-design.md`

---

## File Map

| File | Changes |
|---|---|
| `config.py` | Add `LOOP_MODE_DEFAULT` from `CRT_LOOP` env var |
| `queue_manager.py` | Update `move()`; add `can_move`, `advance_cursor`, `prepare_for_play`, `first_queued_after_cursor`, `first_ready`; remove `next_ready` |
| `pipeline.py` | Add `loop_mode`; update `run_prepare` (→ `first_queued_after_cursor`); update `run_cast` (→ `advance_cursor` + `prepare_for_play`) |
| `ui.py` | Update `QueueListItem` (init, compose, buttons, refresh); update `CRTCastApp` (handler, refresh, toggle, on_list_view_selected) |
| `tests/test_queue_manager.py` | Update stale move test; add tests for all new helpers |
| `tests/test_pipeline.py` | Add `loop_mode` test; add `LOOP_MODE_DEFAULT` to restore fixture |
| `tests/test_ui.py` | Add button tests; loop toggle tests; config restore fixture |
| `CLAUDE.md` | Document `CRT_LOOP`, new semantics, new methods |

---

## Task 1: Add LOOP_MODE_DEFAULT to config.py

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add the constant**

In `config.py`, after the existing env-var constants (look for the block with `SCALE_MODE`, `AUTO_CROP`, etc.), add:

```python
LOOP_MODE_DEFAULT: bool = os.getenv("CRT_LOOP", "0") == "1"
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
python -c "import config; print(config.LOOP_MODE_DEFAULT)"
```

Expected output: `False`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat(config): add CRT_LOOP env var for loop mode default"
```

---

## Task 2: QueueManager — simplify move() and add can_move()

**Files:**
- Modify: `queue_manager.py`
- Test: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_queue_manager.py`, replace `test_move_non_queued_item_fails` and add new tests at the bottom of the file:

```python
def test_move_non_queued_item_succeeds():
    """move() allows swapping items of any status — no status restrictions."""
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "downloading"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_move_any_status_up():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"
    assert qm.move(item2.id, "up") is True
    assert qm.items[0].url == "https://youtube.com/watch?v=2"


def test_move_any_status_down():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    qm.add("https://youtube.com/watch?v=2")
    assert qm.move(item1.id, "down") is True
    assert qm.items[1].url == "https://youtube.com/watch?v=1"


def test_can_move_middle_item():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    qm.add("https://youtube.com/watch?v=3")
    assert qm.can_move(item2.id, "up") is True
    assert qm.can_move(item2.id, "down") is True


def test_can_move_first_item_cannot_go_up():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item1.id, "up") is False
    assert qm.can_move(item1.id, "down") is True


def test_can_move_last_item_cannot_go_down():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item2.id, "up") is True
    assert qm.can_move(item2.id, "down") is False


def test_can_move_any_status():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.can_move(item1.id, "down") is True
    assert qm.can_move(item2.id, "up") is True


def test_can_move_unknown_id_returns_false():
    qm = QueueManager()
    assert qm.can_move("nonexistent", "up") is False
    assert qm.can_move("nonexistent", "down") is False
```

- [ ] **Step 2: Run tests to confirm failures**

```bash
source .venv/bin/activate && python -m pytest tests/test_queue_manager.py::test_move_non_queued_item_succeeds tests/test_queue_manager.py::test_can_move_middle_item -v
```

Expected: `test_move_non_queued_item_succeeds` FAILS (current code rejects), `test_can_move_middle_item` FAILS (`can_move` doesn't exist yet).

- [ ] **Step 3: Update move() and add can_move() in queue_manager.py**

Replace the `move()` method (lines 88–100) and add `can_move()` directly after it:

```python
def move(self, item_id: str, direction: str) -> bool:
    for i, item in enumerate(self.items):
        if item.id == item_id:
            if direction == "up" and i > 0:
                self.items[i], self.items[i - 1] = self.items[i - 1], self.items[i]
                return True
            if direction == "down" and i < len(self.items) - 1:
                self.items[i], self.items[i + 1] = self.items[i + 1], self.items[i]
                return True
            return False
    return False

def can_move(self, item_id: str, direction: str) -> bool:
    """Return True if item can be moved in that direction (border check only)."""
    for i, item in enumerate(self.items):
        if item.id == item_id:
            if direction == "up":
                return i > 0
            if direction == "down":
                return i < len(self.items) - 1
    return False
```

- [ ] **Step 4: Run all new tests**

```bash
python -m pytest tests/test_queue_manager.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add queue_manager.py tests/test_queue_manager.py
git commit -m "feat(queue): simplify move() to allow any status, add can_move()"
```

---

## Task 3: QueueManager — add advance_cursor()

**Files:**
- Modify: `queue_manager.py`
- Test: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queue_manager.py`:

```python
def test_advance_cursor_returns_next_after_playing():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    assert qm.advance_cursor(loop=False) is item2


def test_advance_cursor_returns_next_after_last_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    qm.add("https://youtube.com/watch?v=3")
    # last done = item1 (index 0), next = item2 (index 1)
    assert qm.advance_cursor(loop=False) is item2


def test_advance_cursor_uses_last_done_when_multiple():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"
    item3 = qm.add("https://youtube.com/watch?v=3")
    # last done = item2 (index 1), next = item3 (index 2)
    assert qm.advance_cursor(loop=False) is item3


def test_advance_cursor_playing_takes_priority_over_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"
    item3 = qm.add("https://youtube.com/watch?v=3")
    # playing = item2 (index 1), next = item3 (index 2)
    assert qm.advance_cursor(loop=False) is item3


def test_advance_cursor_stop_mode_returns_none_at_end():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    # cursor at last item → stop mode → None
    assert qm.advance_cursor(loop=False) is None


def test_advance_cursor_loop_mode_wraps_to_first():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"  # last item is playing
    assert qm.advance_cursor(loop=True) is item1


def test_advance_cursor_no_cursor_empty_list():
    qm = QueueManager()
    assert qm.advance_cursor(loop=False) is None
    assert qm.advance_cursor(loop=True) is None


def test_advance_cursor_no_cursor_nonempty_list():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    # No playing, no done — fresh playlist: return first item
    assert qm.advance_cursor(loop=False) is item1
```

- [ ] **Step 2: Run tests to confirm failures**

```bash
python -m pytest tests/test_queue_manager.py::test_advance_cursor_returns_next_after_playing -v
```

Expected: FAIL (`advance_cursor` not defined).

- [ ] **Step 3: Implement advance_cursor() in queue_manager.py**

Add after `can_move()`:

```python
def advance_cursor(self, loop: bool) -> "QueueItem | None":
    """Return the next item to play, or None if end of playlist (stop mode).

    Cursor = first item with status 'playing', or last item with status 'done'.
    Returns items[cursor_idx + 1], wrapping to items[0] if loop=True,
    or None if loop=False and the cursor is at the last position.
    If no cursor exists (fresh playlist), returns items[0] or None if empty.
    Does NOT mutate any item state.
    """
    cursor_idx: int | None = None
    last_done_idx: int | None = None

    for i, item in enumerate(self.items):
        if item.status == "playing":
            cursor_idx = i
            break
        if item.status == "done":
            last_done_idx = i

    if cursor_idx is None:
        cursor_idx = last_done_idx

    if cursor_idx is None:
        return self.items[0] if self.items else None

    next_idx = cursor_idx + 1
    if next_idx >= len(self.items):
        return self.items[0] if loop else None
    return self.items[next_idx]
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_queue_manager.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add queue_manager.py tests/test_queue_manager.py
git commit -m "feat(queue): add advance_cursor() for playlist-style playback advancement"
```

---

## Task 4: QueueManager — add prepare_for_play()

**Files:**
- Modify: `queue_manager.py`
- Test: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queue_manager.py`:

```python
import os
from unittest.mock import patch
import config as config_module


def test_prepare_for_play_done_with_cache_becomes_ready(tmp_path):
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "done"
    fake_mp4 = tmp_path / "cached.mp4"
    fake_mp4.touch()
    item.filename = "cached.mp4"
    with patch.object(config_module, "TEMP_DIR", str(tmp_path)):
        qm.prepare_for_play(item)
    assert item.status == "ready"


def test_prepare_for_play_done_without_cache_becomes_queued():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "done"
    item.filename = "missing.mp4"
    item.progress = 42.0
    with patch.object(config_module, "TEMP_DIR", "/nonexistent/path/that/cannot/exist"):
        qm.prepare_for_play(item)
    assert item.status == "queued"
    assert item.filename is None
    assert item.progress == 0.0


def test_prepare_for_play_error_with_cache_becomes_ready(tmp_path):
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "error"
    item.error = "download failed"
    fake_mp4 = tmp_path / "cached.mp4"
    fake_mp4.touch()
    item.filename = "cached.mp4"
    with patch.object(config_module, "TEMP_DIR", str(tmp_path)):
        qm.prepare_for_play(item)
    assert item.status == "ready"
    assert item.error is None


def test_prepare_for_play_error_without_cache_becomes_queued():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "error"
    item.error = "encode failed"
    item.filename = None
    with patch.object(config_module, "TEMP_DIR", "/nonexistent/path/that/cannot/exist"):
        qm.prepare_for_play(item)
    assert item.status == "queued"
    assert item.error is None


def test_prepare_for_play_ready_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "ready"
    item.filename = "cached.mp4"
    qm.prepare_for_play(item)
    assert item.status == "ready"
    assert item.filename == "cached.mp4"


def test_prepare_for_play_queued_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    # status is "queued" by default
    qm.prepare_for_play(item)
    assert item.status == "queued"


def test_prepare_for_play_encoding_is_unchanged():
    qm = QueueManager()
    item = qm.add("https://youtube.com/watch?v=1")
    item.status = "encoding"
    qm.prepare_for_play(item)
    assert item.status == "encoding"
```

- [ ] **Step 2: Run tests to confirm failures**

```bash
python -m pytest tests/test_queue_manager.py::test_prepare_for_play_done_with_cache_becomes_ready -v
```

Expected: FAIL (`prepare_for_play` not defined).

- [ ] **Step 3: Implement prepare_for_play() in queue_manager.py**

Add after `advance_cursor()`:

```python
def prepare_for_play(self, item: QueueItem) -> None:
    """Transition item to the correct pre-play state based on cache.

    done/error + cached MP4 → ready (instant replay)
    done/error + no cache  → queued (pipeline will re-download)
    ready / queued / downloading / encoding → unchanged
    """
    if item.status not in ("done", "error"):
        return
    if item.filename and os.path.isfile(
        os.path.join(config.TEMP_DIR, item.filename)
    ):
        item.status = "ready"
        item.error = None
    else:
        item.status = "queued"
        item.filename = None
        item.progress = 0.0
        item.error = None
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_queue_manager.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add queue_manager.py tests/test_queue_manager.py
git commit -m "feat(queue): add prepare_for_play() — restores done/error to playable state"
```

---

## Task 5: QueueManager — add first_queued_after_cursor() and first_ready()

**Files:**
- Modify: `queue_manager.py`
- Test: `tests/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_queue_manager.py`:

```python
def test_first_queued_after_cursor_no_cursor_returns_first_queued():
    """Without a cursor, behaves like the old first_queued()."""
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    qm.add("https://youtube.com/watch?v=2")
    assert qm.first_queued_after_cursor() is item1


def test_first_queued_after_cursor_skips_items_before_playing():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")  # queued — before cursor
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "playing"  # cursor
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued — after cursor
    assert qm.first_queued_after_cursor() is item3


def test_first_queued_after_cursor_skips_items_before_last_done():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")  # queued — before cursor
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"  # cursor (last done)
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued — after cursor
    assert qm.first_queued_after_cursor() is item3


def test_first_queued_after_cursor_returns_none_when_nothing_after():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "playing"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"  # not queued
    assert qm.first_queued_after_cursor() is None


def test_first_queued_after_cursor_uses_last_done_of_multiple():
    qm = QueueManager()
    item1 = qm.add("https://youtube.com/watch?v=1")
    item1.status = "done"
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "done"  # this is the last done
    item3 = qm.add("https://youtube.com/watch?v=3")  # queued
    assert qm.first_queued_after_cursor() is item3


def test_first_ready_returns_first_ready_item():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")  # queued
    item2 = qm.add("https://youtube.com/watch?v=2")
    item2.status = "ready"
    item3 = qm.add("https://youtube.com/watch?v=3")
    item3.status = "ready"
    assert qm.first_ready() is item2


def test_first_ready_returns_none_when_no_ready():
    qm = QueueManager()
    qm.add("https://youtube.com/watch?v=1")
    assert qm.first_ready() is None
```

- [ ] **Step 2: Run tests to confirm failures**

```bash
python -m pytest tests/test_queue_manager.py::test_first_queued_after_cursor_no_cursor_returns_first_queued tests/test_queue_manager.py::test_first_ready_returns_first_ready_item -v
```

Expected: FAIL (methods not defined).

- [ ] **Step 3: Implement both methods in queue_manager.py**

Add after `prepare_for_play()`. Replace the existing `first_queued()` block (lines 116–121) with this extended version, keeping `first_queued()` for any callers not yet updated, and adding the new methods after it:

```python
def first_queued(self) -> QueueItem | None:
    """First item with status 'queued' (legacy; prefer first_queued_after_cursor)."""
    for item in self.items:
        if item.status == "queued":
            return item
    return None

def first_queued_after_cursor(self) -> QueueItem | None:
    """First 'queued' item after the cursor position.

    Cursor = first playing item, or last done item. If no cursor,
    searches from the beginning (equivalent to first_queued()).
    Used by the prepare loop so prefetch targets only items ahead of
    the current playback position.
    """
    cursor_idx: int | None = None
    last_done_idx: int | None = None

    for i, item in enumerate(self.items):
        if item.status == "playing":
            cursor_idx = i
            break
        if item.status == "done":
            last_done_idx = i

    if cursor_idx is None:
        cursor_idx = last_done_idx

    start = (cursor_idx + 1) if cursor_idx is not None else 0
    for item in self.items[start:]:
        if item.status == "queued":
            return item
    return None

def first_ready(self) -> QueueItem | None:
    """First item with status 'ready', for display purposes."""
    for item in self.items:
        if item.status == "ready":
            return item
    return None
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_queue_manager.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add queue_manager.py tests/test_queue_manager.py
git commit -m "feat(queue): add first_queued_after_cursor(), first_ready()"
```

---

## Task 6: QueueManager — remove next_ready(), update callers

**Files:**
- Modify: `queue_manager.py`
- Modify: `ui.py` (line 362)

- [ ] **Step 1: Delete next_ready() from queue_manager.py**

Remove the entire `next_ready()` method (lines 123–135 in the original file — the block between `first_queued` and `next_ready`):

```python
# DELETE this entire method:
def next_ready(self) -> QueueItem | None:
    """First 'ready' item that can be cast now. ..."""
    for item in self.items:
        if item.status in ("queued", "downloading"):
            return None
        if item.status == "ready":
            return item
    return None
```

- [ ] **Step 2: Update the only remaining caller in ui.py**

In `ui.py` at line 362, replace:

```python
show = self.queue.next_ready()
```

with:

```python
show = self.queue.first_ready()
```

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all pass (pipeline.py still references `next_ready` — that will be fixed in Task 7).

Actually pipeline.py calls `self.queue.next_ready()` at line 285. Since we removed the method, running tests will show an AttributeError at runtime. To avoid breaking tests prematurely, run only the queue and UI unit tests:

```bash
python -m pytest tests/test_queue_manager.py tests/test_ui.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add queue_manager.py ui.py
git commit -m "refactor(queue): remove next_ready(), replace with first_ready() in ui.py"
```

---

## Task 7: Pipeline — add loop_mode, update run_cast and run_prepare

**Files:**
- Modify: `pipeline.py`
- Modify: `ui.py` (on_list_view_selected — use prepare_for_play)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Add LOOP_MODE_DEFAULT to the _restore_config fixture in test_pipeline.py**

In `tests/test_pipeline.py`, update the `_restore_config` fixture to also capture/restore `LOOP_MODE_DEFAULT`:

```python
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
```

- [ ] **Step 2: Write failing test for loop_mode**

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_worker_loop_mode_defaults_to_false():
    from pipeline import PipelineWorker
    from unittest.mock import MagicMock
    worker = PipelineWorker(MagicMock(), MagicMock())
    assert worker.loop_mode is False


def test_pipeline_worker_loop_mode_reads_from_config(monkeypatch):
    monkeypatch.setattr(config_module, "LOOP_MODE_DEFAULT", True)
    from pipeline import PipelineWorker
    from unittest.mock import MagicMock
    worker = PipelineWorker(MagicMock(), MagicMock())
    assert worker.loop_mode is True
```

- [ ] **Step 3: Run to confirm failure**

```bash
python -m pytest tests/test_pipeline.py::test_pipeline_worker_loop_mode_defaults_to_false -v
```

Expected: FAIL (`PipelineWorker` has no `loop_mode` attribute).

- [ ] **Step 4: Add loop_mode to PipelineWorker.__init__**

In `pipeline.py`, add `self.loop_mode` in `PipelineWorker.__init__` after `self._cast_enabled`:

```python
self._cast_enabled: bool = False  # True once user explicitly starts playback
self.loop_mode: bool = config.LOOP_MODE_DEFAULT
self._next_item_id: str | None = None  # Specific item to cast next (no reorder)
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
python -m pytest tests/test_pipeline.py::test_pipeline_worker_loop_mode_defaults_to_false tests/test_pipeline.py::test_pipeline_worker_loop_mode_reads_from_config -v
```

Expected: both pass.

- [ ] **Step 6: Update run_prepare to use first_queued_after_cursor()**

In `pipeline.py`, in `run_prepare()`, replace the call on line 264:

```python
# OLD:
item = self.queue.first_queued()

# NEW:
item = self.queue.first_queued_after_cursor()
```

- [ ] **Step 7: Update run_cast to use advance_cursor() + prepare_for_play()**

In `pipeline.py`, in `run_cast()`, replace lines 277–285:

```python
# OLD:
if self._next_item_id:
    nid = self._next_item_id
    self._next_item_id = None
    item = next(
        (i for i in self.queue.items if i.id == nid and i.status == "ready"),
        None,
    )
if item is None:
    item = self.queue.next_ready()

# NEW:
if self._next_item_id:
    nid = self._next_item_id
    self._next_item_id = None
    item = next(
        (i for i in self.queue.items if i.id == nid and i.status == "ready"),
        None,
    )
if item is None:
    candidate = self.queue.advance_cursor(loop=self.loop_mode)
    if candidate is not None:
        self.queue.prepare_for_play(candidate)
        if candidate.status == "ready":
            item = candidate
```

- [ ] **Step 8: Simplify on_list_view_selected in ui.py to use prepare_for_play()**

In `ui.py`, in `on_list_view_selected()`:

Replace:

```python
target = event.item.queue_item
if target.status not in ("queued", "ready", "done"):
    return
# Restore a "done" item to its playable state before proceeding.
if target.status == "done":
    if target.filename and os.path.isfile(
        os.path.join(config.TEMP_DIR, target.filename)
    ):
        target.status = "ready"
    else:
        target.status = "queued"
        target.filename = None
```

With:

```python
target = event.item.queue_item
if target.status not in ("queued", "ready", "done", "error"):
    return
self.queue.prepare_for_play(target)
```

Note: `error` is now included — users can manually restart a failed item via Enter/click.

- [ ] **Step 9: Run full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add pipeline.py ui.py tests/test_pipeline.py
git commit -m "feat(pipeline): add loop_mode, replace next_ready with advance_cursor in cast loop"
```

---

## Task 8: UI — QueueListItem with ↑/↓ buttons

**Files:**
- Modify: `ui.py`
- Test: `tests/test_ui.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_queue_list_item_has_up_down_buttons(app, queue):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        items = list(app.query(QueueListItem))
        assert len(items) == 2
        # Each item has up and down buttons
        item0_id = queue.items[0].id
        item1_id = queue.items[1].id
        items[0].query_one(f"#up-{item0_id}", Button)
        items[0].query_one(f"#down-{item0_id}", Button)
        items[1].query_one(f"#up-{item1_id}", Button)
        items[1].query_one(f"#down-{item1_id}", Button)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_ui.py::test_queue_list_item_has_up_down_buttons -v
```

Expected: FAIL (no buttons in QueueListItem yet).

- [ ] **Step 3: Update QueueListItem in ui.py**

Replace the entire `QueueListItem` class (lines 85–121) with:

```python
class QueueListItem(ListItem):
    def __init__(self, item: QueueItem, index: int, can_up: bool = True, can_down: bool = True) -> None:
        super().__init__()
        self.queue_item = item
        self.index = index
        self._can_up = can_up
        self._can_down = can_down

    def _build_label(self) -> str:
        title = self.queue_item.title or self.queue_item.url
        status = self.queue_item.status
        if status in ("casting", "playing"):
            prefix = "[green]▶[/green]"
        elif status in ("downloading", "encoding"):
            prefix = "[yellow]↓[/yellow]"
        elif status == "ready":
            prefix = "[cyan]✓[/cyan]"
        elif status == "error":
            prefix = "[red]✕[/red]"
        elif status == "done":
            prefix = "[dim]✓[/dim]"
        else:
            prefix = " "
        if status in ("downloading", "encoding"):
            pct = self.queue_item.progress
            bar_width = 12
            filled = int(pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            label = "DL" if status == "downloading" else "ENC"
            suffix = f"  [yellow]{bar} {label} {pct:.0f}%[/yellow]"
        else:
            suffix = ""
        return f"  {prefix} {self.index + 1}. {title}{suffix}"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="queue-row"):
            yield Label(self._build_label(), classes="queue-title")
            with Horizontal(classes="queue-actions"):
                yield Button(
                    "↑",
                    id=f"up-{self.queue_item.id}",
                    classes="queue-action-btn",
                    disabled=not self._can_up,
                )
                yield Button(
                    "↓",
                    id=f"down-{self.queue_item.id}",
                    classes="queue-action-btn",
                    disabled=not self._can_down,
                )

    def refresh_label(self) -> None:
        self.query_one(".queue-title", Label).update(self._build_label())

    def update_buttons(self, can_up: bool, can_down: bool) -> None:
        self.query_one(f"#up-{self.queue_item.id}", Button).disabled = not can_up
        self.query_one(f"#down-{self.queue_item.id}", Button).disabled = not can_down
```

- [ ] **Step 4: Add CSS for the new button layout**

In `CRTCastApp.CSS`, add these rules at the end of the CSS string (before the closing `"""`):

```css
    .queue-row {
        height: 1;
    }
    .queue-title {
        width: 1fr;
    }
    .queue-actions {
        width: auto;
        height: 1;
    }
    .queue-action-btn {
        min-width: 3;
        height: 1;
        border: none;
        background: transparent;
        color: $text;
        padding: 0;
        margin: 0 0 0 1;
    }
    .queue-action-btn:hover {
        background: $accent;
    }
    .queue-action-btn:disabled {
        color: $text-disabled;
    }
```

- [ ] **Step 5: Run failing test**

```bash
python -m pytest tests/test_ui.py::test_queue_list_item_has_up_down_buttons -v
```

Expected: PASS.

- [ ] **Step 6: Run full UI test suite**

```bash
python -m pytest tests/test_ui.py -v
```

Expected: all pass. Fix any regressions (typically `refresh_label` was calling `self.query_one(Label)` — now must use `.queue-title` selector; the replacement in Step 3 already handles this).

- [ ] **Step 7: Commit**

```bash
git add ui.py tests/test_ui.py
git commit -m "feat(ui): add up/down action buttons to each QueueListItem"
```

---

## Task 9: UI — on_button_pressed handler + _refresh_queue_list disabled state

**Files:**
- Modify: `ui.py`
- Test: `tests/test_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_up_button_moves_item_up(app, queue, mock_pipeline):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        item1_id = queue.items[0].id
        item2_id = queue.items[1].id
        # Click ↑ on the second item
        await pilot.click(f"#up-{item2_id}")
        await pilot.pause()
        assert queue.items[0].id == item2_id
        assert queue.items[1].id == item1_id


@pytest.mark.asyncio
async def test_down_button_moves_item_down(app, queue, mock_pipeline):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        item1_id = queue.items[0].id
        # Click ↓ on the first item
        await pilot.click(f"#down-{item1_id}")
        await pilot.pause()
        assert queue.items[1].id == item1_id


@pytest.mark.asyncio
async def test_up_button_disabled_for_first_item(app, queue):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        item0_id = queue.items[0].id
        up_btn = app.query_one(f"#up-{item0_id}", Button)
        assert up_btn.disabled is True


@pytest.mark.asyncio
async def test_down_button_disabled_for_last_item(app, queue):
    queue.add("https://youtube.com/watch?v=1")
    queue.items[0].title = "First"
    queue.add("https://youtube.com/watch?v=2")
    queue.items[1].title = "Second"
    async with app.run_test() as pilot:
        app._refresh_all()
        await pilot.pause()
        item1_id = queue.items[1].id
        down_btn = app.query_one(f"#down-{item1_id}", Button)
        assert down_btn.disabled is True
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_ui.py::test_up_button_moves_item_up tests/test_ui.py::test_up_button_disabled_for_first_item -v
```

Expected: FAIL.

- [ ] **Step 3: Add up/down handling to on_button_pressed in ui.py**

In `ui.py`, at the top of `on_button_pressed()` (line 429), add the queue action handling before the existing `btn-prev` check:

```python
async def on_button_pressed(self, event: Button.Pressed) -> None:
    btn_id = event.button.id or ""
    # Queue reorder buttons (↑/↓ on each row)
    if btn_id.startswith("up-") or btn_id.startswith("down-"):
        direction, _, item_id = btn_id.partition("-")
        if self.queue.move(item_id, direction):
            self._refresh_queue_list()
            self.pipeline.wake()
        event.stop()
        return
    # Playback control buttons
    if btn_id == "btn-prev":
        self.action_prev_video()
    elif btn_id == "btn-back":
        await self.action_seek_back()
    elif btn_id == "btn-pause":
        await self.action_pause()
    elif btn_id == "btn-fwd":
        await self.action_seek_forward()
    elif btn_id == "btn-next":
        self.action_next_video()
```

- [ ] **Step 4: Update _refresh_queue_list to pass can_up/can_down and call update_buttons**

In `ui.py`, update `_refresh_queue_list()` to maintain button disabled state in both the "same order" fast path and the "rebuild" path:

```python
def _refresh_queue_list(self) -> None:
    list_view = self.query_one("#queue-list", ListView)
    existing = list(list_view.query(QueueListItem))
    queue_ids = [item.id for item in self.queue.items]
    existing_ids = [li.queue_item.id for li in existing]

    if queue_ids == existing_ids:
        for i, (li, item) in enumerate(zip(existing, self.queue.items)):
            li.queue_item = item
            li.index = i
            li.refresh_label()
            li.update_buttons(
                can_up=self.queue.can_move(item.id, "up"),
                can_down=self.queue.can_move(item.id, "down"),
            )
        return

    prev_index = list_view.index
    had_focus = list_view.has_focus
    list_view.clear()
    n = len(self.queue.items)
    for i, item in enumerate(self.queue.items):
        list_view.append(QueueListItem(
            item, i,
            can_up=self.queue.can_move(item.id, "up"),
            can_down=self.queue.can_move(item.id, "down"),
        ))
    if self.queue.items:
        list_view.index = min(prev_index or 0, n - 1)
    if had_focus:
        list_view.focus()
```

- [ ] **Step 5: Run new tests**

```bash
python -m pytest tests/test_ui.py::test_up_button_moves_item_up tests/test_ui.py::test_down_button_moves_item_down tests/test_ui.py::test_up_button_disabled_for_first_item tests/test_ui.py::test_down_button_disabled_for_last_item -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add ui.py tests/test_ui.py
git commit -m "feat(ui): wire up/down buttons to queue.move(), update disabled state on refresh"
```

---

## Task 10: UI — loop toggle, header indicator, propagation to pipeline

**Files:**
- Modify: `ui.py`
- Test: `tests/test_ui.py`

- [ ] **Step 1: Add _restore_config fixture to test_ui.py**

At the top of `tests/test_ui.py`, add after the imports:

```python
import config as config_module


@pytest.fixture(autouse=True)
def _restore_config():
    orig_loop = config_module.LOOP_MODE_DEFAULT
    yield
    config_module.LOOP_MODE_DEFAULT = orig_loop
```

- [ ] **Step 2: Write failing tests**

Append to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_loop_toggle_flips_mode(app, mock_pipeline):
    async with app.run_test() as pilot:
        assert app.loop_mode is False
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.loop_mode is True
        assert mock_pipeline.loop_mode is True
        # Toggle back
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert app.loop_mode is False


@pytest.mark.asyncio
async def test_loop_toggle_shows_indicator_in_header(app):
    async with app.run_test() as pilot:
        from textual.widgets import Static
        header = app.query_one("#queue-header", Static)
        # OFF: no loop indicator
        assert "⟳" not in str(header.renderable)
        await pilot.press("ctrl+r")
        await pilot.pause()
        # ON: loop indicator present
        assert "⟳" in str(header.renderable)


@pytest.mark.asyncio
async def test_loop_toggle_notifies_user(app):
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        # The notification mechanism fires; we just verify no exception raised.
        assert app.loop_mode is True
```

- [ ] **Step 3: Run to confirm failures**

```bash
python -m pytest tests/test_ui.py::test_loop_toggle_flips_mode -v
```

Expected: FAIL (`app.loop_mode` attribute does not exist).

- [ ] **Step 4: Add loop_mode to CRTCastApp.__init__**

In `ui.py`, add `self.loop_mode` in `CRTCastApp.__init__()` after `self._pending_display`:

```python
def __init__(
    self,
    queue: QueueManager,
    pipeline: PipelineWorker,
    chromecast: ChromecastManager,
) -> None:
    super().__init__()
    self.queue = queue
    self.pipeline = pipeline
    self.chromecast = chromecast
    self._pending_display: QueueItem | None = None
    self.loop_mode: bool = config.LOOP_MODE_DEFAULT
```

- [ ] **Step 5: Add ctrl+r binding to BINDINGS**

In `ui.py`, add to the `BINDINGS` list (after the `ctrl+j` move_down line):

```python
Binding("ctrl+r", "toggle_loop", "Loop", show=True, priority=True),
```

- [ ] **Step 6: Add action_toggle_loop and _refresh_loop_indicator to CRTCastApp**

Add these two methods to `CRTCastApp` (after `action_move_down` for organization):

```python
def _refresh_loop_indicator(self) -> None:
    text = " CODA ⟳" if self.loop_mode else " CODA"
    self.query_one("#queue-header", Static).update(text)

def action_toggle_loop(self) -> None:
    self.loop_mode = not self.loop_mode
    self.pipeline.loop_mode = self.loop_mode
    self._refresh_loop_indicator()
    self.notify(f"Loop: {'ON' if self.loop_mode else 'OFF'}")
    if self.loop_mode:
        self.pipeline.wake()
```

`Static` is already imported at the top of `ui.py` (used for `NowPlayingWidget`). Verify the import includes it; if not, add `Static` to the Textual imports.

- [ ] **Step 7: Run new tests**

```bash
python -m pytest tests/test_ui.py::test_loop_toggle_flips_mode tests/test_ui.py::test_loop_toggle_shows_indicator_in_header -v
```

Expected: both pass.

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add ui.py tests/test_ui.py
git commit -m "feat(ui): add loop mode toggle (ctrl+r) with header indicator and pipeline propagation"
```

---

## Task 11: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add CRT_LOOP to the Configuration section**

In `CLAUDE.md`, in the "Configuration" section (the bullet list of env vars), add after `CRT_AUTO_CROP`:

```
- `CRT_LOOP` (`1`|`0`, default `0`) — when enabled, the playlist loops back to the first item after the last item finishes. Togglable at runtime via `Ctrl+R` in the TUI (session-local, not persisted to state.json).
```

- [ ] **Step 2: Update the done status semantics in the Encoding/Architecture sections**

In the `Architecture` section, update the **Data flow** description to note that `done` is informational:

Find the paragraph about playback end and add a note that `done` items are re-playable via cursor advance or manual selection.

In the **Key integration point** paragraph or nearby, add:

```
**Playlist model:** `done` is informational only — not a terminal state. The cast loop uses `advance_cursor()` on `QueueManager` to find the next item by queue position, looping back if `loop_mode=True`. `prepare_for_play()` transitions `done`/`error` items to `ready` (cache hit) or `queued` (cache miss) before casting.
```

- [ ] **Step 3: Add new QueueManager methods to the Encoding cache section**

In the `Architecture` section, add a note about the new helpers after the `cached_encoded_filename()` mention:

```
**Queue helpers:** `advance_cursor(loop)` returns the next item by position after the current cursor (playing or last done). `prepare_for_play(item)` transitions done/error to ready/queued based on cache. `can_move(item_id, direction)` returns bool for UI disabled state. `first_queued_after_cursor()` is used by the prefetch loop to skip items before the cursor.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): document CRT_LOOP, playlist cursor model, new QueueManager helpers"
```

---

## Task 12: Final verification

**Files:** none

- [ ] **Step 1: Run complete unit test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v --ignore=tests/test_integration.py
```

Expected: all tests pass (should be ≥ 50 tests — the original count + ~30 new ones).

- [ ] **Step 2: Smoke test — start the app**

```bash
./run.sh
```

Add 2 URLs, verify the ↑/↓ buttons appear on each row. Click ↑ on the second item — it should move to position 1. Click ↓ — it should return to position 2. Press `Ctrl+R` — the header should show ` CODA ⟳`. Press `Ctrl+R` again — indicator disappears.

- [ ] **Step 3: Commit any final fixes**

If the smoke test reveals visual issues (button sizing, alignment), fix CSS and commit:

```bash
git add ui.py
git commit -m "fix(ui): adjust queue action button CSS after smoke test"
```
