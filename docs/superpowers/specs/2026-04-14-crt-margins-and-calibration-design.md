# CRT overscan margins + calibration pattern

## Problem

CRT TVs have overscan: the tube physically crops the edges of the image. The user reports content is cut at the top and bottom when casting from crt-player. The pipeline currently produces a 1024×576 (stretched 4:3) file with no configurable compensation, and there's no way to visually calibrate the usable area on the specific TV.

## Goals

1. Let the user configure black margins on each edge of the video, so content is pushed into the CRT's visible area.
2. Provide a calibration test pattern (grid + reference markers) that goes through the same margin/scaling pipeline, castable from the TUI with a single keypress.

## Non-goals

- Auto-detecting overscan. Calibration stays manual.
- Per-video margins. Margins are a property of the TV, set once via env.
- Runtime editing of margins from the TUI. User edits `.env` and re-runs.

## Configuration

Four new env vars in `config.py`, all default `0`:

```python
MARGIN_TOP    = int(os.environ.get("CRT_MARGIN_TOP",    "0"))
MARGIN_BOTTOM = int(os.environ.get("CRT_MARGIN_BOTTOM", "0"))
MARGIN_LEFT   = int(os.environ.get("CRT_MARGIN_LEFT",   "0"))
MARGIN_RIGHT  = int(os.environ.get("CRT_MARGIN_RIGHT",  "0"))
```

**Units:** pixels in the logical 4:3 frame (768×576). Vertical margins stay 1:1 in the displayed image; horizontal margins end up horizontally squeezed along with the rest of the frame, so a value of `N` on left or right occupies the same perceived area on screen as `N` on top/bottom. Typical range: 0–40.

**Validation (in `config.py`):**
- Clamp each to `>= 0`.
- If `MARGIN_TOP + MARGIN_BOTTOM > 288` (50% of 576) or `MARGIN_LEFT + MARGIN_RIGHT > 384` (50% of 768): log a warning and clamp each side proportionally so the sum is at most 50%.
- Log final effective values at startup.

Documented in `.env` with a comment block showing the range and the meaning.

## Encoding pipeline

Modify `_build_video_filter(crop_detect)` in [pipeline.py:78-88](pipeline.py#L78-L88).

Logical frame stays 768×576. Let:
- `inner_w = 768 - MARGIN_LEFT - MARGIN_RIGHT`
- `inner_h = 576 - MARGIN_TOP  - MARGIN_BOTTOM`
- `out_w  = 1024`, `out_h = 576` (unchanged output geometry)

Filter chain, `crop` mode:
```
[crop_detect,]
scale=inner_w:inner_h:force_original_aspect_ratio=increase,
crop=inner_w:inner_h,
pad=768:576:LEFT:TOP:color=black,
scale=out_w:out_h,
setsar=1:1
```

`pad` mode (analogous), using `force_original_aspect_ratio=decrease` and centering the scaled content inside the inner box:
```
[crop_detect,]
scale=inner_w:inner_h:force_original_aspect_ratio=decrease,
pad=inner_w:inner_h:(inner_w-iw)/2:(inner_h-ih)/2,
pad=768:576:LEFT:TOP:color=black,
scale=out_w:out_h,
setsar=1:1
```

**Back-compat fast path:** when all four margins are 0, `_build_video_filter` short-circuits and returns the current filter string verbatim (without `pad`). This guarantees byte-identical output and preserves cached files from before this feature existed.

## Cache naming

`_prepare_one` in [pipeline.py:261+](pipeline.py#L261) composes the cached filename from `video_id` and `SCALE_MODE`. Extend:

- All margins 0 → `{video_id}_pal_{scale_mode}.mp4` (unchanged, back-compatible).
- Any margin non-zero → `{video_id}_pal_{scale_mode}_m{top}-{bottom}-{left}-{right}.mp4`.

The encoded filename used in `_prepare_one` (based on the source `base` name) gets the same suffix, so switching margins re-encodes without overwriting the previous file.

## Calibration pattern

New module `calibration.py` with:

```python
def build_calibration_filter(duration_s: int = 60) -> tuple[list[str], str]:
    """Return (ffmpeg_input_args, filter_complex) for the calibration clip."""
```

**Composition** (768×576, 4:3):
- Background: dark grey (`color=c=0x202020:s=768x576:d=60`).
- Grid: thin white lines every 48px (16 columns × 12 rows), drawn with repeated `drawbox` filters or a single `geq` expression.
- Centered square 192×192 in white outline — visual aspect-ratio check.
- Centered circle (drawn via `drawbox`-approximation or `geq`) — if it looks oval on the TV, the HW squeeze is off.
- Corner labels `"0,0"`, `"768,0"`, `"0,576"`, `"768,576"` via `drawtext`.
- Edge rulers: tick marks every 8px for the first 48px from each edge, so the user can read "16px are lost on top" directly off the screen.
- Bottom overlay text showing the currently configured margins (`T:10 B:15 L:5 R:5`) — read at generation time from `config.MARGIN_*`.

The pattern is composed directly at `inner_w × inner_h` (so grid, square, circle, corner labels, and edge rulers already describe the *visible* area after margins). Then the same `pad=768:576:LEFT:TOP:color=black` + `scale=1024:576` tail as the main pipeline is applied, so the on-screen result matches what a real video will look like under the same margin settings.

**Generation:** `calibration.py` exposes `async def generate_calibration_clip(out_path: str) -> None` which runs `ffmpeg -f lavfi -i <sources> -filter_complex <chain> -t 60 <out_path>` via `asyncio.create_subprocess_exec`. Output file: `TEMP_DIR/calibration.mp4`. Re-generated on every invocation (no cache; the pattern depends on current env values).

## TUI integration

In [ui.py]:

- Add binding: `Binding("ctrl+t", "calibrate", "Test pattern", priority=True)`.
- Add `async def action_calibrate(self) -> None` that:
  1. Sets the status label to "Generating test pattern…".
  2. Calls `await calibration.generate_calibration_clip(path)`.
  3. Ensures Chromecast is connected (`await self.chromecast.wait_for_connection()`).
  4. Calls `await asyncio.to_thread(self.chromecast.cast_url, media_url, 0.0)` using the existing local HTTP server (same URL shape as `_cast_and_wait`).
  5. Updates the status label: "Calibration pattern playing (Ctrl+T to regenerate, Ctrl+S to stop)".
- Does not touch `QueueManager` or `PipelineWorker`. The pattern is a one-shot cast outside the queue.
- If a queued item is currently playing or casting, warn the user via status and abort the calibration (avoids clobbering real playback by accident). User must stop current playback first.

## Testing

New tests in `tests/test_pipeline.py`:
- `test_build_video_filter_no_margins` — with all four margins 0, the returned filter string equals the current implementation's output (byte-for-byte) to guarantee cache back-compat.
- `test_build_video_filter_crop_with_margins` — with `MARGIN_TOP=20, MARGIN_LEFT=10, MARGIN_BOTTOM=0, MARGIN_RIGHT=0`, filter contains `scale=758:556`, `crop=758:556`, `pad=768:576:10:20:color=black`, `scale=1024:576`.
- `test_build_video_filter_pad_with_margins` — same geometry in pad mode.
- `test_margins_clamped_when_excessive` — setting `LEFT=500, RIGHT=500` in config clamps them to safe values and logs a warning.
- `test_cached_filename_includes_margins` — `_prepare_one` composes `..._m{t}-{b}-{l}-{r}.mp4` when any margin is non-zero, and the legacy name when all are zero.

New tests in `tests/test_calibration.py`:
- `test_calibration_filter_contains_current_margins` — filter_complex includes `pad=768:576:LEFT:TOP` for the configured env values.
- `test_calibration_filter_always_includes_grid_and_square` — basic sanity on the filter string.

No end-to-end ffmpeg invocation in unit tests. The pattern is verified visually.

## Implementation touch points

- [config.py](config.py) — four new vars + clamp/warn logic.
- [pipeline.py:78-88](pipeline.py#L78-L88) — `_build_video_filter` + cache filename.
- [pipeline.py:267](pipeline.py#L267) + [pipeline.py:307](pipeline.py#L307) — cached filename suffix.
- `calibration.py` (new) — pattern filter builder + generation helper.
- [ui.py] — `ctrl+t` binding + `action_calibrate`.
- `tests/test_pipeline.py` — filter and cache tests.
- `tests/test_calibration.py` (new) — calibration filter tests.
- `.env` — add documented defaults for the four new vars.
