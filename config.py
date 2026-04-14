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
    """If a+b > max_sum, scale both proportionally so their sum <= max_sum."""
    if a + b <= max_sum:
        return a, b
    factor = max_sum / (a + b)
    ra = round(a * factor)
    rb = min(round(b * factor), max_sum - ra)
    return ra, rb


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


def cached_encoded_filename(video_id_or_base: str) -> str:
    """Return the cached encoded filename for a given video_id or source base.

    When all margins are zero the legacy name `{id}_pal_{mode}.mp4` is kept
    so previously encoded files remain valid.
    """
    margins = (MARGIN_TOP, MARGIN_BOTTOM, MARGIN_LEFT, MARGIN_RIGHT)
    if any(margins):
        suffix = f"_m{margins[0]}-{margins[1]}-{margins[2]}-{margins[3]}"
    else:
        suffix = ""
    return f"{video_id_or_base}_pal_{SCALE_MODE}{suffix}.mp4"
