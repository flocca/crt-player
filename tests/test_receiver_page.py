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


def test_priming_has_a_fallback_timeout():
    """If `playing` never fires, priming must still end (unmute, report)."""
    src = PAGE.read_text()
    m = re.search(r"PRIME_TIMEOUT_MS\s*=\s*(\d+)", src)
    assert m and 1000 <= int(m.group(1)) <= 5000
    assert re.search(r"setTimeout\([\s\S]{0,120}PRIME_TIMEOUT_MS\)", src)


def test_priming_starts_before_the_saved_position():
    """The primed pause must land on the saved position, not past it."""
    src = PAGE.read_text()
    assert re.search(r"PRIME_LEAD_S\s*=\s*0\.25", src)
    assert re.search(r"SEEK_END_MARGIN_S\s*=\s*0\.5", src)
    assert re.search(r"\(position \|\| 0\) - PRIME_LEAD_S", src)


def test_no_preview_loaded_while_main_primes():
    """The Chromecast ignores `muted` on a second loaded <video>."""
    src = PAGE.read_text()
    assert re.search(r"v\.preview && priming === null", src)
    prime = src[src.index("function primeFrame()"):src.index("function startAt(")]
    assert "previewOff()" in prime


# The Chromecast has one hardware video decoder: when #preview or #cal loads
# a file, #main's pipeline is revoked and becomes a dummy that shows and plays
# nothing (black, silent) until #main is reloaded (TV check, 2026-11-08).

def _between(src, start, end):
    return src[src.index(start):src.index(end, src.index(start))]


def test_second_video_marks_main_stale():
    src = PAGE.read_text()
    assert "markMainStale()" in _between(src, "function showPreview(", 'preview.addEventListener("timeupdate"')
    assert "markMainStale()" in _between(src, "function calibration(", "function dispatch(")


def test_stale_main_is_reloaded_where_it_shows_or_plays():
    src = PAGE.read_text()
    background = _between(src, "function applyBackground(", "function showScreen(")
    assert background.count("reloadMain(false)") == 2  # the paused frame, and the night
    assert "reloadMain(true)" in _between(src, 'case "play":', 'case "pause":')


def test_same_file_load_reloads_a_stale_main():
    src = PAGE.read_text()
    load = _between(src, "function load(", "function blank(")
    assert re.search(r"!reload && currentSrc\(\) === msg\.url", load)
    # Cleared before cancelPriming, whose background redraw would reload again.
    assert load.index("mainStale = null") < load.index("cancelPriming()")


def test_blank_forgets_a_stale_main():
    src = PAGE.read_text()
    assert "mainStale = null" in _between(src, "function blank(", "function stopCalibration(")


def test_stale_marker_keeps_a_pending_load_position():
    """While #main waits for its metadata, currentTime is 0: a preview or the
    calibration landing then must keep the position the load is heading to."""
    src = PAGE.read_text()
    mark = _between(src, "function markMainStale(", "\n  }\n")
    assert re.search(r"pending !== null \? pending\.position", mark)


def test_play_and_seek_check_a_stale_main_before_a_pending_load():
    src = PAGE.read_text()
    play = _between(src, 'case "play":', 'case "pause":')
    assert play.index("mainStale !== null") < play.index("pending !== null")
    seek = _between(src, 'case "seek":', 'case "blank":')
    assert seek.index("mainStale !== null") < seek.index("pending !== null")


def test_row_4_takes_the_duration_from_the_view():
    """The library's duration is known before the page loads the file."""
    src = PAGE.read_text()
    line = _between(src, "function playerLine(", "function videoLine(")
    assert "v.paused_duration" in line


def test_row_4_keeps_the_duration_across_a_reload():
    """Fallback while the library has no duration yet: a reload resets
    main.duration to NaN, and row 4 must not flash --:--."""
    src = PAGE.read_text()
    line = _between(src, "function playerLine(", "function videoLine(")
    assert "knownDuration()" in line


def test_video_rows_right_field_is_8_columns():
    """8 columns fit -1:05:00; the title gives up the column."""
    src = PAGE.read_text()
    line = _between(src, "function videoLine(", "function commandLine(")
    assert "COLS - len(head) - 9" in line
    assert 'padL(r.right || "", 8)' in line


def test_paused_load_is_muted_before_its_pipeline_starts():
    """Muting just before the priming play is too late on the Chromecast: a
    reload after a preview played a fraction of audio (TV check, 2026-11-08)."""
    src = PAGE.read_text()
    load = _between(src, "function load(", "function blank(")
    mute = load.index("if (!msg.autoplay) { main.muted = true; }")
    assert mute < load.index("main.load()")
    start = _between(src, "function startAt(", "function load(")
    assert start.index("main.muted = false") < start.index("main.play()")
