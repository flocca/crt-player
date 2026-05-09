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

ENTRYPOINT ["crt-daemon"]
