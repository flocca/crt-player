import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from crt.media_server import create_media_app


@pytest.fixture
def media_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def app(media_dir):
    return create_media_app(media_dir)


@pytest.mark.asyncio
async def test_serve_existing_file(app, media_dir):
    filepath = os.path.join(media_dir, "test_video.mp4")
    with open(filepath, "wb") as f:
        f.write(b"\x00" * 1024)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/test_video.mp4")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert len(resp.content) == 1024


@pytest.mark.asyncio
async def test_serve_nonexistent_file(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/nope.mp4")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_path_traversal_blocked(app, media_dir):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/media/../../../etc/passwd")
    assert resp.status_code == 404
