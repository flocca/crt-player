FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY crt ./crt
COPY tui_client ./tui_client

RUN pip install --no-cache-dir -e .

ENV CRT_TEMP_DIR=/data/cache
ENV CRT_STATE_FILE=/data/state/state.json
ENV CRT_YT_CLIENT_SECRETS=/data/secrets/client_secrets.json
ENV CRT_YT_TOKEN_FILE=/data/secrets/oauth_token.json

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8765/status',timeout=5)" || exit 1

ENTRYPOINT ["crt-daemon"]
