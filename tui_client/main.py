from __future__ import annotations

import os

from tui_client.ui import CRTCastApp


def main() -> None:
    daemon_url = os.environ.get("CRT_DAEMON_URL", "http://localhost:8765")
    app = CRTCastApp(daemon_url)
    app.run()


if __name__ == "__main__":
    main()
