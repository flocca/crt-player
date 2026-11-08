import pathlib
import re

PAGE = pathlib.Path(__file__).parent.parent / "crt" / "receiver_page" / "index.html"


def test_page_handles_every_message_type():
    src = PAGE.read_text()
    for t in ("config", "load", "play", "pause", "seek", "blank", "screen", "calibration"):
        assert re.search(rf"['\"]{t}['\"]", src), t
    # The old ring menu is gone: the daemon no longer sends it.
    assert not re.search(r"['\"]menu['\"]", src)


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


def test_same_file_shortcut_skips_a_failed_element():
    """Retrying after a media error must really reload the file."""
    src = PAGE.read_text()
    assert re.search(r"currentSrc\(\) === msg\.url && main\.readyState >= 1 && !main\.error", src)


def test_page_unloads_preview_instead_of_hiding():
    """A hidden <video> leaves its last frame on the Chromecast video plane."""
    src = PAGE.read_text()
    assert re.search(r"preview\.removeAttribute\(\s*['\"]src['\"]\s*\)", src)
    assert re.search(r"preview\.load\(\)", src)


def test_page_has_no_css_animation_or_transition():
    """The page runs at 24-30 fps: anything animated over the video stutters."""
    src = PAGE.read_text()
    assert "@keyframes" not in src
    assert "transition:" not in src
    assert "animation:" not in src


def test_page_loads_bedstead_from_receiver_route():
    assert "/receiver/bedstead.otf" in PAGE.read_text()
