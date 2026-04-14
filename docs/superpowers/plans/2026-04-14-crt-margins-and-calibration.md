# CRT Margins + Calibration Pattern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four configurable black-margin env vars (top/bottom/left/right) to compensate for CRT overscan, and a TUI-triggered test pattern to calibrate them visually.

**Architecture:** Config layer validates margins (clamp to ≤50% per axis) at startup. The encoding filter builder short-circuits to the current filter when all margins are 0 (cache back-compat), otherwise scales content into an inner rectangle then pads it with black. Cache filenames carry a margin suffix only when non-zero. A new `calibration.py` module builds an ffmpeg `lavfi` filter-complex for a grid/square/marker test pattern (composed at inner-size, padded, then stretched). A `ctrl+t` binding in the TUI generates and casts the pattern without touching the queue.

**Tech Stack:** Python 3, ffmpeg (lavfi + drawbox/drawtext/geq), pytest, Textual, pychromecast.

**Spec reference:** [docs/superpowers/specs/2026-04-14-crt-margins-and-calibration-design.md](../specs/2026-04-14-crt-margins-and-calibration-design.md)

---

## File Structure

- **Create:** `calibration.py` — builds the lavfi filter-complex for the test pattern and runs ffmpeg to produce `{TEMP_DIR}/calibration.mp4`. One responsibility: the calibration clip.
- **Create:** `tests/test_calibration.py` — unit tests for the filter-complex builder.
- **Modify:** `config.py` — add four margin env vars + startup clamp/warn logic.
- **Modify:** `pipeline.py` — update `_build_video_filter` (back-compat fast path + inner/pad geometry) and both cached-filename call sites in `_prepare_one`.
- **Modify:** `ui.py` — add `ctrl+t` binding, `action_calibrate()`, and gate via `check_action`.
- **Modify:** `tests/test_pipeline.py` — filter-string + cache-naming tests.
- **Modify:** `.env` — document the four new vars with range hints.

---

## Task 1: Config — margin env vars with validation

**Files:**
- Modify: `config.py`
- Test: `tests/test_config.py` (create)

- [ ] **Step 1: Create failing test for default values**

Create `tests/test_config.py`:

```python
import importlib
import logging
import os

import pytest


def _reload_config(monkeypatch, **env):
    for k in (
        "CRT_MARGIN_TOP", "CRT_MARGIN_BOTTOM",
        "CRT_MARGIN_LEFT", "CRT_MARGIN_RIGHT",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    importlib.reload(config)
    return config


def test_margins_default_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.MARGIN_TOP == 0
    assert cfg.MARGIN_BOTTOM == 0
    assert cfg.MARGIN_LEFT == 0
    assert cfg.MARGIN_RIGHT == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py::test_margins_default_to_zero -v`
Expected: FAIL with `AttributeError: module 'config' has no attribute 'MARGIN_TOP'`.

- [ ] **Step 3: Add margin env vars to config.py**

Edit `config.py`. Replace the full file contents with:

```python
import logging
import os

log = logging.getLogger(__name__)

CHROMECAST_NAME = os.environ.get("CRT_CHROMECAST_NAME", "Living Room TV")
MAX_VIDEO_HEIGHT = int(os.environ.get("CRT_MAX_VIDEO_HEIGHT", "576"))
TEMP_DIR = os.environ.get("CRT_TEMP_DIR", "/tmp/crt_cast")
FILE_TTL_HOURS = int(os.environ.get("CRT_FILE_TTL_HOURS", "24"))
SCALE_MODE = os.environ.get("CRT_SCALE_MODE", "crop")  # "crop" or "pad"
SERVER_PORT = int(os.environ.get("CRT_SERVER_PORT", "8765"))
STATE_FILE = os.environ.get(
    "CRT_STATE_FILE",
    os.path.join(os.path.expanduser("~"), ".local", "share", "crt-player", "state.json"),
)

# Logical frame is 768x576 (4:3). Margins are pixels in that frame that will be
# rendered as black borders to compensate for CRT overscan.
_FRAME_W = 768
_FRAME_H = 576
_MAX_H_SUM = _FRAME_W // 2  # 384
_MAX_V_SUM = _FRAME_H // 2  # 288


def _load_margin(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, "0")))
    except ValueError:
        log.warning("Invalid value for %s, using 0", name)
        return 0


MARGIN_TOP = _load_margin("CRT_MARGIN_TOP")
MARGIN_BOTTOM = _load_margin("CRT_MARGIN_BOTTOM")
MARGIN_LEFT = _load_margin("CRT_MARGIN_LEFT")
MARGIN_RIGHT = _load_margin("CRT_MARGIN_RIGHT")


def _clamp_axis_pair(a: int, b: int, max_sum: int) -> tuple[int, int]:
    """If a+b > max_sum, scale both proportionally so their sum == max_sum."""
    if a + b <= max_sum:
        return a, b
    factor = max_sum / (a + b)
    return int(a * factor), int(b * factor)


_ct, _cb = _clamp_axis_pair(MARGIN_TOP, MARGIN_BOTTOM, _MAX_V_SUM)
_cl, _cr = _clamp_axis_pair(MARGIN_LEFT, MARGIN_RIGHT, _MAX_H_SUM)

if (_ct, _cb) != (MARGIN_TOP, MARGIN_BOTTOM):
    log.warning(
        "Vertical margins %d+%d exceed 50%% of frame height, clamped to %d+%d",
        MARGIN_TOP, MARGIN_BOTTOM, _ct, _cb,
    )
    MARGIN_TOP, MARGIN_BOTTOM = _ct, _cb

if (_cl, _cr) != (MARGIN_LEFT, MARGIN_RIGHT):
    log.warning(
        "Horizontal margins %d+%d exceed 50%% of frame width, clamped to %d+%d",
        MARGIN_LEFT, MARGIN_RIGHT, _cl, _cr,
    )
    MARGIN_LEFT, MARGIN_RIGHT = _cl, _cr

if any((MARGIN_TOP, MARGIN_BOTTOM, MARGIN_LEFT, MARGIN_RIGHT)):
    log.info(
        "CRT margins active: top=%d bottom=%d left=%d right=%d",
        MARGIN_TOP, MARGIN_BOTTOM, MARGIN_LEFT, MARGIN_RIGHT,
    )
```

- [ ] **Step 4: Run test to verify default-values test passes**

Run: `python -m pytest tests/test_config.py::test_margins_default_to_zero -v`
Expected: PASS.

- [ ] **Step 5: Add tests for reading env + clamping**

Append to `tests/test_config.py`:

```python
def test_margins_read_from_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        CRT_MARGIN_TOP="10", CRT_MARGIN_BOTTOM="15",
        CRT_MARGIN_LEFT="5", CRT_MARGIN_RIGHT="8",
    )
    assert (cfg.MARGIN_TOP, cfg.MARGIN_BOTTOM) == (10, 15)
    assert (cfg.MARGIN_LEFT, cfg.MARGIN_RIGHT) == (5, 8)


def test_margins_negative_clamped_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch, CRT_MARGIN_TOP="-10")
    assert cfg.MARGIN_TOP == 0


def test_margins_invalid_string_fallback_to_zero(monkeypatch):
    cfg = _reload_config(monkeypatch, CRT_MARGIN_TOP="not-a-number")
    assert cfg.MARGIN_TOP == 0


def test_vertical_margins_clamped_to_half_frame(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="config")
    cfg = _reload_config(
        monkeypatch, CRT_MARGIN_TOP="500", CRT_MARGIN_BOTTOM="500",
    )
    # 500+500=1000, max_sum=288, factor=0.288 → each becomes int(500*0.288)=144
    assert cfg.MARGIN_TOP + cfg.MARGIN_BOTTOM <= 288
    assert cfg.MARGIN_TOP == 144
    assert cfg.MARGIN_BOTTOM == 144
    assert any("exceed 50%" in r.getMessage() for r in caplog.records)


def test_horizontal_margins_clamped_to_half_frame(monkeypatch):
    cfg = _reload_config(
        monkeypatch, CRT_MARGIN_LEFT="400", CRT_MARGIN_RIGHT="400",
    )
    assert cfg.MARGIN_LEFT + cfg.MARGIN_RIGHT <= 384
    assert cfg.MARGIN_LEFT == 192
    assert cfg.MARGIN_RIGHT == 192
```

- [ ] **Step 6: Run full test file to verify all pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat(config): add CRT_MARGIN_{TOP,BOTTOM,LEFT,RIGHT} env vars with clamp"
```

---

## Task 2: Pipeline — filter builder with margins

**Files:**
- Modify: `pipeline.py:78-88` (`_build_video_filter`)
- Test: `tests/test_pipeline.py` (extend)

- [ ] **Step 1: Write failing test for no-margin back-compat**

Append to `tests/test_pipeline.py`:

```python
import importlib

import config as config_module
from pipeline import _build_video_filter


def _reset_margins(top=0, bottom=0, left=0, right=0):
    config_module.MARGIN_TOP = top
    config_module.MARGIN_BOTTOM = bottom
    config_module.MARGIN_LEFT = left
    config_module.MARGIN_RIGHT = right


def test_build_filter_no_margins_crop_mode_is_backcompat():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter(None)
    expected = (
        "scale=768:576:force_original_aspect_ratio=increase,"
        "crop=768:576,scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_no_margins_pad_mode_is_backcompat():
    _reset_margins()
    config_module.SCALE_MODE = "pad"
    result = _build_video_filter(None)
    expected = (
        "scale=768:576:force_original_aspect_ratio=decrease,"
        "pad=768:576:(768-iw)/2:(576-ih)/2,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected
```

- [ ] **Step 2: Run test to verify pass (no change yet)**

Run: `python -m pytest tests/test_pipeline.py::test_build_filter_no_margins_crop_mode_is_backcompat tests/test_pipeline.py::test_build_filter_no_margins_pad_mode_is_backcompat -v`
Expected: PASS (guardrails the current behavior before changes).

- [ ] **Step 3: Write failing tests for margins + crop_detect**

Append to `tests/test_pipeline.py`:

```python
def test_build_filter_crop_mode_with_margins():
    _reset_margins(top=20, bottom=0, left=10, right=0)
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter(None)
    expected = (
        "scale=758:556:force_original_aspect_ratio=increase,"
        "crop=758:556,"
        "pad=768:576:10:20:color=black,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_pad_mode_with_margins():
    _reset_margins(top=10, bottom=10, left=20, right=20)
    config_module.SCALE_MODE = "pad"
    result = _build_video_filter(None)
    expected = (
        "scale=728:556:force_original_aspect_ratio=decrease,"
        "pad=728:556:(728-iw)/2:(556-ih)/2,"
        "pad=768:576:20:10:color=black,"
        "scale=1024:576,setsar=1:1"
    )
    assert result == expected


def test_build_filter_prepends_crop_detect_when_given():
    _reset_margins(top=20, left=10)
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter("crop=640:480:0:0")
    assert result.startswith("crop=640:480:0:0,scale=758:556")


def test_build_filter_no_margins_prepends_crop_detect():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    result = _build_video_filter("crop=640:480:0:0")
    assert result.startswith("crop=640:480:0:0,scale=768:576")
```

- [ ] **Step 4: Run to verify they fail**

Run: `python -m pytest tests/test_pipeline.py -k "build_filter" -v`
Expected: 4 new tests FAIL (the two backcompat ones PASS), because the current builder ignores margins.

- [ ] **Step 5: Rewrite `_build_video_filter` in `pipeline.py`**

Replace lines 78–88 of `pipeline.py` with:

```python
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
```

- [ ] **Step 6: Run the filter tests — all must pass**

Run: `python -m pytest tests/test_pipeline.py -k "build_filter" -v`
Expected: 6 PASS.

- [ ] **Step 7: Run the full pipeline test file to catch regressions**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: all PASS (existing `test_fetch_title`, `test_download_video`, `test_encode_video` still green).

- [ ] **Step 8: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): apply CRT margins via pad in video filter chain"
```

---

## Task 3: Pipeline — cache filename includes margin suffix

**Files:**
- Modify: `pipeline.py:267` and `pipeline.py:307` inside `_prepare_one`
- Test: `tests/test_pipeline.py` (extend)

- [ ] **Step 1: Write failing test for cache filename helper**

Append to `tests/test_pipeline.py`:

```python
from pipeline import _cached_encoded_filename


def test_cached_filename_no_margins_is_legacy_shape():
    _reset_margins()
    config_module.SCALE_MODE = "crop"
    assert _cached_encoded_filename("abc123") == "abc123_pal_crop.mp4"


def test_cached_filename_with_margins_has_suffix():
    _reset_margins(top=10, bottom=15, left=5, right=8)
    config_module.SCALE_MODE = "crop"
    assert _cached_encoded_filename("abc123") == "abc123_pal_crop_m10-15-5-8.mp4"


def test_cached_filename_pad_mode_no_margins():
    _reset_margins()
    config_module.SCALE_MODE = "pad"
    assert _cached_encoded_filename("xyz") == "xyz_pal_pad.mp4"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_pipeline.py -k "cached_filename" -v`
Expected: FAIL with `ImportError: cannot import name '_cached_encoded_filename' from 'pipeline'`.

- [ ] **Step 3: Add helper + use it in `_prepare_one`**

Add near the other helpers in `pipeline.py` (just before `async def _get_duration`):

```python
def _cached_encoded_filename(video_id_or_base: str) -> str:
    """Return the cached encoded filename for a given video_id or source base.

    When all margins are zero the legacy name `{id}_pal_{mode}.mp4` is kept
    so previously encoded files remain valid.
    """
    mode = config.SCALE_MODE
    margins = (
        config.MARGIN_TOP, config.MARGIN_BOTTOM,
        config.MARGIN_LEFT, config.MARGIN_RIGHT,
    )
    if any(margins):
        suffix = f"_m{margins[0]}-{margins[1]}-{margins[2]}-{margins[3]}"
    else:
        suffix = ""
    return f"{video_id_or_base}_pal_{mode}{suffix}.mp4"
```

Then update `_prepare_one`:

Line 267 (current):
```python
cached_encoded = os.path.join(config.TEMP_DIR, f"{video_id}_pal_{config.SCALE_MODE}.mp4")
```
Replace with:
```python
cached_encoded = os.path.join(config.TEMP_DIR, _cached_encoded_filename(video_id))
```

Line 307 (current):
```python
encoded_path = os.path.join(config.TEMP_DIR, f"{base}_pal_{config.SCALE_MODE}.mp4")
```
Replace with:
```python
encoded_path = os.path.join(config.TEMP_DIR, _cached_encoded_filename(base))
```

- [ ] **Step 4: Run cache-filename tests**

Run: `python -m pytest tests/test_pipeline.py -k "cached_filename" -v`
Expected: 3 PASS.

- [ ] **Step 5: Run full test suite to catch regressions**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): encode cache filename carries margin suffix"
```

---

## Task 4: Calibration module — filter builder

**Files:**
- Create: `calibration.py`
- Create: `tests/test_calibration.py`

- [ ] **Step 1: Write failing test for filter-complex structure**

Create `tests/test_calibration.py`:

```python
import config as config_module
from calibration import build_calibration_filter


def _reset_margins(top=0, bottom=0, left=0, right=0):
    config_module.MARGIN_TOP = top
    config_module.MARGIN_BOTTOM = bottom
    config_module.MARGIN_LEFT = left
    config_module.MARGIN_RIGHT = right


def test_calibration_filter_has_dark_grey_background():
    _reset_margins()
    fc = build_calibration_filter()
    assert "color=c=0x202020" in fc


def test_calibration_source_uses_inner_size_when_margins_set():
    from calibration import build_calibration_source
    _reset_margins(top=20, bottom=10, left=5, right=15)
    src = build_calibration_source(duration_s=30)
    # Inner: 768-5-15=748, 576-20-10=546
    assert "s=748x546" in src
    assert "d=30" in src
    assert "r=25" in src


def test_calibration_filter_pads_with_configured_offsets():
    _reset_margins(top=20, bottom=10, left=5, right=15)
    fc = build_calibration_filter()
    assert "pad=768:576:5:20:color=black" in fc


def test_calibration_filter_ends_with_stretch_to_16_9():
    _reset_margins()
    fc = build_calibration_filter()
    assert fc.rstrip().endswith("scale=1024:576,setsar=1:1")


def test_calibration_filter_includes_grid_lines():
    _reset_margins()
    fc = build_calibration_filter()
    # Grid drawn with drawgrid filter
    assert "drawgrid=" in fc


def test_calibration_filter_includes_centered_square():
    _reset_margins()
    fc = build_calibration_filter()
    # 192x192 square centered in the inner area — use drawbox with w=192:h=192
    assert "drawbox=" in fc
    assert "w=192:h=192" in fc


def test_calibration_filter_overlays_current_margin_values():
    _reset_margins(top=7, bottom=11, left=3, right=4)
    fc = build_calibration_filter()
    # Expect the text "T:7 B:11 L:3 R:4" somewhere in a drawtext filter
    assert "T:7 B:11 L:3 R:4" in fc


def test_calibration_filter_no_margins_still_produces_valid_chain():
    from calibration import build_calibration_source
    _reset_margins()
    src = build_calibration_source(duration_s=60)
    fc = build_calibration_filter()
    # With zero margins, inner == full frame
    assert "s=768x576" in src
    assert "pad=768:576:0:0:color=black" in fc
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_calibration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'calibration'`.

- [ ] **Step 3: Create `calibration.py`**

```python
"""Calibration test pattern generator for CRT overscan tuning.

Produces a short ffmpeg clip showing a grid, a centered square, corner
coordinates, edge rulers, and the currently configured margin values. The
pattern is composed at the inner (post-margin) size so black borders match
exactly what real video content will look like.
"""
from __future__ import annotations

import asyncio
import logging
import os

import config

log = logging.getLogger(__name__)

_FRAME_W = 768
_FRAME_H = 576
_OUT_W = 1024
_OUT_H = 576


def _inner_dims() -> tuple[int, int]:
    return (
        _FRAME_W - config.MARGIN_LEFT - config.MARGIN_RIGHT,
        _FRAME_H - config.MARGIN_TOP - config.MARGIN_BOTTOM,
    )


def build_calibration_filter(duration_s: int = 60) -> str:
    """Return the ffmpeg filter_complex string for the calibration clip.

    The chain expects a single lavfi color source (piped as ffmpeg's -i input)
    and emits a finished clip ready to be muxed into an mp4. The caller is
    responsible for supplying `-f lavfi -i color=...` with matching duration.
    """
    inner_w, inner_h = _inner_dims()
    cx = inner_w // 2
    cy = inner_h // 2
    sq = 192  # centered aspect-ratio reference square

    # Current margin values to overlay at the bottom of the inner area.
    mt, mb, ml, mr = (
        config.MARGIN_TOP, config.MARGIN_BOTTOM,
        config.MARGIN_LEFT, config.MARGIN_RIGHT,
    )
    overlay_text = f"T:{mt} B:{mb} L:{ml} R:{mr}"

    # Corner labels: approximate coordinates so the user can see whether
    # pixels 0 or 768 are clipped by the tube.
    corners = [
        ("0\\,0", 4, 4),
        (f"{_FRAME_W}\\,0", inner_w - 80, 4),
        (f"0\\,{_FRAME_H}", 4, inner_h - 24),
        (f"{_FRAME_W}\\,{_FRAME_H}", inner_w - 120, inner_h - 24),
    ]
    drawtexts = ",".join(
        f"drawtext=text='{txt}':x={x}:y={y}:fontsize=20:fontcolor=white"
        for txt, x, y in corners
    )

    # Edge rulers: short tick marks every 8px for the first 48px from each
    # side. Vertical ticks along top/bottom, horizontal ticks along left/right.
    tick = 2
    rulers = []
    for i in range(1, 7):
        off = i * 8
        # top edge tick (centered horizontally, 0..48 down)
        rulers.append(
            f"drawbox=x={cx}:y={off}:w=1:h={tick}:color=white@0.8:t=fill"
        )
        # bottom edge tick
        rulers.append(
            f"drawbox=x={cx}:y={inner_h - off - tick}:w=1:h={tick}:color=white@0.8:t=fill"
        )
        # left edge tick
        rulers.append(
            f"drawbox=x={off}:y={cy}:w={tick}:h=1:color=white@0.8:t=fill"
        )
        # right edge tick
        rulers.append(
            f"drawbox=x={inner_w - off - tick}:y={cy}:w={tick}:h=1:color=white@0.8:t=fill"
        )
    rulers_chain = ",".join(rulers)

    # Centered reference square (outline via drawbox t=2).
    square = (
        f"drawbox=x={cx - sq // 2}:y={cy - sq // 2}:w={sq}:h={sq}"
        ":color=white:t=2"
    )

    # 48px grid across the inner area.
    grid = f"drawgrid=w=48:h=48:t=1:c=white@0.35"

    # Bottom overlay with the currently configured margins.
    overlay = (
        f"drawtext=text='{overlay_text}':x=(w-text_w)/2:y={inner_h - 36}"
        ":fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=6"
    )

    # Final geometry: pad to full frame at the configured offsets, then
    # stretch to 16:9 the same way the main pipeline does.
    pad_and_stretch = (
        f"pad={_FRAME_W}:{_FRAME_H}:{ml}:{mt}:color=black,"
        f"scale={_OUT_W}:{_OUT_H},setsar=1:1"
    )

    # The input is a lavfi color source at inner size (see generator below).
    return ",".join([
        grid,
        square,
        rulers_chain,
        drawtexts,
        overlay,
        pad_and_stretch,
    ])


def build_calibration_source(duration_s: int = 60) -> str:
    """Return the ffmpeg lavfi source spec (the part after `-i`)."""
    inner_w, inner_h = _inner_dims()
    return f"color=c=0x202020:s={inner_w}x{inner_h}:d={duration_s}:r=25"


async def generate_calibration_clip(out_path: str, duration_s: int = 60) -> str:
    """Render the calibration clip to `out_path` synchronously via ffmpeg."""
    source = build_calibration_source(duration_s)
    filter_chain = build_calibration_filter(duration_s)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", source,
        "-vf", filter_chain,
        "-r", "25",
        "-pix_fmt", "yuv420p",
        "-loglevel", "quiet",
        out_path,
    ]
    log.info("Rendering calibration clip: %s", out_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed rendering calibration (exit {proc.returncode})")
    return out_path
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `python -m pytest tests/test_calibration.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Smoke-test the actual ffmpeg invocation (manual)**

Run (one-off, not part of automated suite):
```bash
source .venv/bin/activate
python -c "import asyncio, calibration; asyncio.run(calibration.generate_calibration_clip('/tmp/calib.mp4'))"
ls -la /tmp/calib.mp4
```
Expected: file present, non-zero size, plays in any video player showing the grid/square/text overlay.

If ffmpeg rejects a filter, fix the offending expression in `build_calibration_filter` before continuing. Leave the `-loglevel quiet` in, but during debugging switch to `-loglevel error` temporarily.

- [ ] **Step 6: Commit**

```bash
git add calibration.py tests/test_calibration.py
git commit -m "feat(calibration): test pattern generator with grid, square, rulers"
```

---

## Task 5: TUI — ctrl+t binding to cast calibration pattern

**Files:**
- Modify: `ui.py:186-200` (BINDINGS), add `action_calibrate`, update `check_action`
- Test: `tests/test_ui.py` (extend)

- [ ] **Step 1: Check existing TUI test conventions**

Read: `tests/test_ui.py` and `tests/conftest.py` — note how `app.run_test()` is used and how `PipelineWorker` / `ChromecastManager` are mocked. Use the same fixture style.

- [ ] **Step 2: Write failing test for action_calibrate**

Append to `tests/test_ui.py` (adapt imports/fixtures from the existing file if they differ):

```python
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_ctrl_t_triggers_calibration(app_with_mocks):
    """Pressing ctrl+t should generate and cast the calibration pattern."""
    app, pipeline, chromecast = app_with_mocks
    chromecast.connected = True
    chromecast.wait_for_connection = AsyncMock()
    chromecast.cast_url = MagicMock()

    with patch("ui.calibration.generate_calibration_clip", new=AsyncMock()) as gen, \
         patch("ui.get_local_ip", return_value="127.0.0.1"):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await pilot.pause()

    gen.assert_awaited_once()
    chromecast.cast_url.assert_called_once()
    args, _ = chromecast.cast_url.call_args
    assert args[0].endswith("/media/calibration.mp4")


@pytest.mark.asyncio
async def test_ctrl_t_blocked_while_video_playing(app_with_mocks):
    """If a queue item is casting/playing, ctrl+t should not cast the pattern."""
    app, pipeline, chromecast = app_with_mocks
    item = app.queue.add("https://youtube.com/watch?v=abc")
    item.status = "playing"
    chromecast.cast_url = MagicMock()

    with patch("ui.calibration.generate_calibration_clip", new=AsyncMock()) as gen:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await pilot.pause()

    gen.assert_not_awaited()
    chromecast.cast_url.assert_not_called()
```

If the existing test file uses a different fixture name than `app_with_mocks`, adapt to match. Add any missing imports (`pytest`, `MagicMock`).

- [ ] **Step 3: Run to verify fail**

Run: `python -m pytest tests/test_ui.py -k "calibration or ctrl_t" -v`
Expected: FAIL (binding/action not present).

- [ ] **Step 4: Add binding + action in `ui.py`**

In the imports block of `ui.py`, add:

```python
import calibration
from pipeline import PipelineWorker, get_local_ip
```

(The file already imports `PipelineWorker`; just add `get_local_ip` to the same line or a new import line as appropriate for the file's style. Also add `import calibration` near the other local-module imports.)

In the `BINDINGS` list (currently lines 186–200), add after the `prev_video` binding:

```python
Binding("ctrl+t", "calibrate", "Calibra", show=True, priority=True),
```

Add this method in the App class (next to the other `action_*` methods):

```python
async def action_calibrate(self) -> None:
    active = self.queue.active_item()
    if active and active.status in ("casting", "playing"):
        self._set_status("Ferma il video attuale prima di calibrare.")
        return

    self._set_status("Generazione pattern di calibrazione…")
    out_path = os.path.join(config.TEMP_DIR, "calibration.mp4")
    try:
        await calibration.generate_calibration_clip(out_path)
    except Exception as e:  # noqa: BLE001 - surfacing to UI
        self._set_status(f"Errore calibrazione: {e}")
        return

    if not self.chromecast.connected:
        await self.chromecast.wait_for_connection()

    media_url = f"http://{get_local_ip()}:{config.SERVER_PORT}/media/calibration.mp4"
    await asyncio.to_thread(self.chromecast.cast_url, media_url, 0.0)
    self._set_status("Pattern di calibrazione in riproduzione (Ctrl+T per rigenerare, Ctrl+S per fermare).")
```

If the App class does not already have a `_set_status(msg)` helper, add one that updates whichever Static/Label already displays transient status text. If there's no existing status surface, fall back to `self.notify(msg)` which Textual provides natively.

Update `check_action` to allow `calibrate` unconditionally (the action itself handles the "playing" gate):

```python
if action == "calibrate":
    return True
```

Place it before the final `return True`.

- [ ] **Step 5: Run calibration tests**

Run: `python -m pytest tests/test_ui.py -k "calibration or ctrl_t" -v`
Expected: 2 PASS.

- [ ] **Step 6: Run the whole UI test suite to catch regressions**

Run: `python -m pytest tests/test_ui.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add ui.py tests/test_ui.py
git commit -m "feat(ui): ctrl+t casts the CRT calibration pattern"
```

---

## Task 6: Document env vars in `.env`

**Files:**
- Modify: `.env`

- [ ] **Step 1: Append margin vars with documentation**

Append to `.env`:

```bash

# CRT overscan compensation (pixels in the logical 768x576 frame).
# Increase to push content inward when the tube cuts it off.
# Press Ctrl+T in the TUI to display a calibration grid.
# Typical range: 0-40. Each axis is clamped to 50% of the frame.
CRT_MARGIN_TOP=0
CRT_MARGIN_BOTTOM=0
CRT_MARGIN_LEFT=0
CRT_MARGIN_RIGHT=0
```

- [ ] **Step 2: Run full test suite one more time**

Run: `source .venv/bin/activate && python -m pytest tests/ -v`
Expected: all PASS (unit suite should be green; integration tests skip without `.env.integration`).

- [ ] **Step 3: Commit**

```bash
git add .env
git commit -m "docs(env): add CRT_MARGIN_* with usage hints"
```

---

## Task 7: Manual end-to-end validation

Not committed — a manual checklist to run on the actual CRT before declaring the feature done.

- [ ] **Step 1: Set a small test margin**

Edit `.env`:
```bash
CRT_MARGIN_TOP=20
CRT_MARGIN_BOTTOM=20
CRT_MARGIN_LEFT=0
CRT_MARGIN_RIGHT=0
```

- [ ] **Step 2: Launch the app**

Run: `./run.sh`
Expected: app starts without errors; `crt_cast.log` shows `CRT margins active: top=20 bottom=20 left=0 right=0`.

- [ ] **Step 3: Press `ctrl+t`**

Expected: status line shows "Generazione pattern di calibrazione…" then "Pattern di calibrazione in riproduzione…". On the CRT you see the grid with 20px black bars top/bottom, a centered square, corner labels, and the text `T:20 B:20 L:0 R:0` near the bottom.

- [ ] **Step 4: Queue a real YouTube video**

Add a URL, press Enter to play.
Expected: video plays with the same 20px top/bottom borders. Cache file in `TEMP_DIR` is named `{id}_pal_crop_m20-20-0-0.mp4`.

- [ ] **Step 5: Change margins and re-play same video**

Exit the app. Edit `.env`: set `CRT_MARGIN_TOP=30`. Relaunch. Queue the same URL.
Expected: re-encode happens (different cache filename), new file present alongside the old one.

- [ ] **Step 6: Reset margins to 0 and verify back-compat**

Edit `.env`: all margins = 0. Relaunch. Queue a URL that was cached before this feature existed (if any) OR a URL newly added.
Expected: encoded filename has the legacy form `{id}_pal_crop.mp4`. Pre-existing cached files from before this feature remain valid.

- [ ] **Step 7: Clean up / report**

If everything looks right, feature is complete. If something looks off on the CRT, tune margins interactively by editing `.env` and using `ctrl+t`.
