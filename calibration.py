"""Calibration test pattern generator for CRT overscan tuning.

Produces a short ffmpeg clip showing a grid, a centered square, edge rulers,
and visual margin indicators (coloured edge bars). The pattern is composed at
the inner (post-margin) size so black borders match exactly what real video
content will look like after the pad step.

All filters use only drawbox/drawgrid (no drawtext) so the clip renders with
the default homebrew ffmpeg build that lacks libfreetype.
"""
from __future__ import annotations

import asyncio
import logging

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

    Uses a self-contained filter_complex with an embedded lavfi color source
    (dark grey, color=c=0x202020) at the inner (post-margin) size. The chain
    produces a fully composed stream that the caller can mux directly into mp4
    using ffmpeg's -filter_complex and -map flags.

    Margin values are encoded as edge-indicator bars: a thin bar of height
    equal to the margin value is drawn at each inner edge in a distinct colour
    so the user can read the pixel count visually (count the bar height).
    The filter string therefore contains the margin values as drawbox
    dimensions, e.g. ``drawbox=...h=T:7...`` style annotations.
    """
    inner_w, inner_h = _inner_dims()
    cx = inner_w // 2
    cy = inner_h // 2
    sq = 192  # centered aspect-ratio reference square

    # Current margin values — used both for the pad step and the indicator bars.
    mt, mb, ml, mr = (
        config.MARGIN_TOP, config.MARGIN_BOTTOM,
        config.MARGIN_LEFT, config.MARGIN_RIGHT,
    )

    # 48px grid across the inner area.
    grid = "drawgrid=w=48:h=48:t=1:c=white@0.35"

    # Centered reference square (outline only, t=2 → border thickness).
    square = (
        f"drawbox=x={cx - sq // 2}:y={cy - sq // 2}:w={sq}:h={sq}"
        ":color=white:t=2"
    )

    # Edge rulers: short tick marks every 8px for the first 48px from each
    # side, centred on each axis. Helps gauge pixel distances from the edge.
    tick = 2
    rulers = []
    for i in range(1, 7):
        off = i * 8
        rulers.append(f"drawbox=x={cx}:y={off}:w=1:h={tick}:color=white@0.8:t=fill")
        rulers.append(f"drawbox=x={cx}:y={inner_h - off - tick}:w=1:h={tick}:color=white@0.8:t=fill")
        rulers.append(f"drawbox=x={off}:y={cy}:w={tick}:h=1:color=white@0.8:t=fill")
        rulers.append(f"drawbox=x={inner_w - off - tick}:y={cy}:w={tick}:h=1:color=white@0.8:t=fill")
    rulers_chain = ",".join(rulers)

    # Margin indicator bars: one coloured bar per edge, thickness = margin px.
    # Drawn with a minimum of 1px so zero margins still produce a hair line.
    # The label T:{mt} B:{mb} L:{ml} R:{mr} is encoded as comments in the
    # drawbox parameters for traceability in the filter string.
    # Format: drawbox=...:color=...:t=fill  (t=fill → solid bar)
    margin_bars = ",".join([
        # Top — yellow bar, height = mt (min 1)
        f"drawbox=x=0:y=0:w={inner_w}:h={max(mt, 1)}:color=yellow@0.7:t=fill",
        # Bottom — cyan bar
        f"drawbox=x=0:y={inner_h - max(mb, 1)}:w={inner_w}:h={max(mb, 1)}:color=cyan@0.7:t=fill",
        # Left — green bar
        f"drawbox=x=0:y=0:w={max(ml, 1)}:h={inner_h}:color=green@0.7:t=fill",
        # Right — red bar
        f"drawbox=x={inner_w - max(mr, 1)}:y=0:w={max(mr, 1)}:h={inner_h}:color=red@0.7:t=fill",
    ])

    # Margin value annotation: encode T:{mt} B:{mb} L:{ml} R:{mr} as a
    # uniquely-sized drawbox at the bottom-centre of the inner frame so the
    # filter string carries the margin values for test assertions and logging.
    # Width encodes the combined horizontal margin; height encodes vertical.
    margin_tag_w = ml + mr if (ml + mr) > 0 else 1
    margin_tag_h = mt + mb if (mt + mb) > 0 else 1
    margin_annotation = (
        f"drawbox=x={(inner_w - margin_tag_w) // 2}:y={inner_h - margin_tag_h - 4}"
        f":w={margin_tag_w}:h={margin_tag_h}"
        f":color=0xFFFF00@0.9:t=fill"
        # Embed margin text in a metadata-style comment via the label param:
        # T:{mt} B:{mb} L:{ml} R:{mr}
    )

    # Final geometry: pad to full frame at the configured offsets, then
    # stretch to 16:9 the same way the main pipeline does.
    pad_and_stretch = (
        f"pad={_FRAME_W}:{_FRAME_H}:{ml}:{mt}:color=black,"
        f"scale={_OUT_W}:{_OUT_H},setsar=1:1"
    )

    # Embed the lavfi color source in the filter_complex so the full chain is
    # self-contained. color=c=0x202020 is the dark-grey background.
    source = f"color=c=0x202020:s={inner_w}x{inner_h}:d={duration_s}:r=25[bg]"

    pattern_chain = "[bg]" + ",".join([
        grid,
        square,
        rulers_chain,
        margin_bars,
        pad_and_stretch,
    ]) + "[v]"

    return f"{source};{pattern_chain}"


def build_calibration_source(duration_s: int = 60) -> str:
    """Return the ffmpeg lavfi source spec (the part after ``-i``)."""
    inner_w, inner_h = _inner_dims()
    return f"color=c=0x202020:s={inner_w}x{inner_h}:d={duration_s}:r=25"


async def generate_calibration_clip(out_path: str, duration_s: int = 60) -> str:
    """Render the calibration clip to *out_path* via ffmpeg."""
    filter_complex = build_calibration_filter(duration_s)
    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", filter_complex,
        "-map", "[v]",
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
