import pytest
import config as config_module
from calibration import build_calibration_filter


@pytest.fixture(autouse=True)
def _restore_config():
    orig_top = config_module.MARGIN_TOP
    orig_bottom = config_module.MARGIN_BOTTOM
    orig_left = config_module.MARGIN_LEFT
    orig_right = config_module.MARGIN_RIGHT
    yield
    config_module.MARGIN_TOP = orig_top
    config_module.MARGIN_BOTTOM = orig_bottom
    config_module.MARGIN_LEFT = orig_left
    config_module.MARGIN_RIGHT = orig_right


def _reset_margins(top=0, bottom=0, left=0, right=0):
    config_module.MARGIN_TOP = top
    config_module.MARGIN_BOTTOM = bottom
    config_module.MARGIN_LEFT = left
    config_module.MARGIN_RIGHT = right


def test_calibration_filter_has_dark_grey_background():
    _reset_margins()
    fc = build_calibration_filter()
    assert "color=c=0x202020" in fc


def test_calibration_source_embedded_at_inner_size_when_margins_set():
    _reset_margins(top=20, bottom=10, left=5, right=15)
    fc = build_calibration_filter(duration_s=30)
    # Inner: 768-5-15=748, 576-20-10=546
    assert "s=748x546" in fc
    assert "d=30" in fc
    assert "r=25" in fc


def test_calibration_filter_pads_with_configured_offsets():
    _reset_margins(top=20, bottom=10, left=5, right=15)
    fc = build_calibration_filter()
    assert "pad=768:576:5:20:color=black" in fc


def test_calibration_filter_ends_with_stretch_to_16_9():
    _reset_margins()
    fc = build_calibration_filter()
    assert "scale=1024:576,setsar=1:1" in fc


def test_calibration_filter_includes_grid_lines():
    _reset_margins()
    fc = build_calibration_filter()
    # Grid drawn with drawgrid filter
    assert "drawgrid=" in fc


def test_calibration_filter_includes_centered_square():
    _reset_margins()
    fc = build_calibration_filter()
    # 192x192 square centered in the inner area — drawbox with w=192:h=192
    assert "drawbox=" in fc
    assert "w=192:h=192" in fc


def test_calibration_filter_margin_bars_encode_pixel_values():
    _reset_margins(top=7, bottom=11, left=3, right=4)
    fc = build_calibration_filter()
    # Margin values are encoded in the pad step and in the margin indicator
    # bars. Verify the pad step carries all four margin values.
    # pad=768:576:{left}:{top} encodes L and T; bottom bar height encodes B;
    # right bar width encodes R.
    assert "pad=768:576:3:7:color=black" in fc
    # Bottom indicator bar height == mb=11
    assert ":h=11:" in fc or "h=11" in fc
    # Right indicator bar width == mr=4
    assert ":w=4:" in fc or "w=4" in fc


def test_calibration_filter_no_margins_still_produces_valid_chain():
    _reset_margins()
    fc = build_calibration_filter()
    # With zero margins, inner == full frame
    assert "s=768x576" in fc
    assert "pad=768:576:0:0:color=black" in fc
