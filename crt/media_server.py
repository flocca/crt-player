import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response


def create_media_app(media_dir: str) -> FastAPI:
    app = FastAPI()

    @app.get("/media/{filename}")
    async def serve_media(filename: str) -> Response:
        if "/" in filename or "\\" in filename or ".." in filename:
            return Response(status_code=404)
        filepath = os.path.join(media_dir, filename)
        if not os.path.isfile(filepath):
            return Response(status_code=404)
        return FileResponse(filepath, media_type="video/mp4")

    return app
