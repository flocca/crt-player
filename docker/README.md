# crt-player Docker deployment

Run the headless sync daemon in a container on any Linux host on the same LAN as your Chromecast.

## Prerequisites

- Docker and Docker Compose installed
- `ffmpeg` is bundled inside the image (no host install needed)
- YouTube OAuth credentials (`client_secrets.json` + `oauth_token.json`) placed in `docker/data/secrets/`

## Quick start

```bash
cd docker

# Copy the example env file and fill in your values
cp .env.example .env
# Edit .env: set CRT_YT_PLAYLIST_ID and CRT_CHROMECAST_NAME

docker compose up -d
docker compose logs -f
```

The daemon listens on port **8765** (host network mode, so it can discover Chromecasts via mDNS).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CRT_YT_PLAYLIST_ID` | *(required)* | YouTube playlist ID to sync from |
| `CRT_CHROMECAST_NAME` | `Living Room TV` | Chromecast friendly name |
| `CRT_SCALE_MODE` | `crop` | `crop` or `pad` |
| `CRT_AUTO_CROP` | `1` | Auto-detect and remove baked-in letterbox bars |
| `CRT_SYNC_INTERVAL_S` | `300` | Seconds between YouTube playlist polls |
| `CRT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CRT_MARGIN_TOP` | `0` | CRT overscan compensation (pixels) |
| `CRT_MARGIN_BOTTOM` | `0` | CRT overscan compensation (pixels) |
| `CRT_MARGIN_LEFT` | `0` | CRT overscan compensation (pixels) |
| `CRT_MARGIN_RIGHT` | `0` | CRT overscan compensation (pixels) |

## Volumes

| Container path | Purpose |
|---|---|
| `/data/cache` | Encoded MP4 files (can be large — use a fast disk) |
| `/data/state` | `state.json` — queue and playback position persistence |
| `/data/secrets` | OAuth credentials (mounted read-only) |

## OAuth credentials

1. Create a project in Google Cloud Console and enable the YouTube Data API v3.
2. Download the OAuth client secrets file and save it as `docker/data/secrets/client_secrets.json`.
3. Run the auth flow once on any machine:
   ```bash
   python -m crt.youtube_client auth
   ```
   This writes `oauth_token.json` to the path configured by `CRT_YT_TOKEN_FILE`.
4. Copy the resulting token file to `docker/data/secrets/oauth_token.json`.

## Updating

```bash
docker compose pull   # or rebuild from source:
docker compose build
docker compose up -d
```

## Interacting with the daemon

The HTTP API is available at `http://<host>:8765`:

```bash
# Library status
curl http://localhost:8765/library/items

# Full daemon status
curl http://localhost:8765/status

# Trigger a manual sync
curl -X POST http://localhost:8765/control/sync

# Play a specific video
curl -X POST http://localhost:8765/control/play/<video_id>

# Stop playback
curl -X POST http://localhost:8765/control/stop
```

Or use the TUI client from any machine on the same network:
```bash
CRT_DAEMON_URL=http://<host>:8765 python -m tui_client
```
