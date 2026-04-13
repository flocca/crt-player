import os

CHROMECAST_NAME = os.environ.get("CRT_CHROMECAST_NAME", "Living Room TV")
MAX_VIDEO_HEIGHT = int(os.environ.get("CRT_MAX_VIDEO_HEIGHT", "576"))
TEMP_DIR = os.environ.get("CRT_TEMP_DIR", "/tmp/crt_cast")
FILE_TTL_HOURS = int(os.environ.get("CRT_FILE_TTL_HOURS", "24"))
SERVER_PORT = int(os.environ.get("CRT_SERVER_PORT", "8765"))
STATE_FILE = os.environ.get(
    "CRT_STATE_FILE",
    os.path.join(os.path.expanduser("~"), ".local", "share", "crt-player", "state.json"),
)
