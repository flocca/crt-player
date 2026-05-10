import pytest

from crt import bootstrap


def test_extract_code_from_callback_url():
    url = "http://localhost/?code=4/0AX_xyz&scope=youtube.readonly"
    code = bootstrap.extract_code_from_url(url)
    assert code == "4/0AX_xyz"


def test_extract_code_from_url_missing_code_raises():
    with pytest.raises(ValueError, match="missing 'code'"):
        bootstrap.extract_code_from_url("http://localhost/?error=access_denied")


def test_extract_code_handles_extra_params():
    url = "http://localhost/?state=xxx&code=ABC123&scope=foo"
    code = bootstrap.extract_code_from_url(url)
    assert code == "ABC123"
