import pathlib
import re

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
