"""Interactive OAuth bootstrap subcommand.

Usage:
    crt-bootstrap     # or: python -m crt.bootstrap

Logs the consent URL, waits for the user to paste the callback URL from their
browser (which will show 'connection refused' on http://localhost/), extracts
the code, exchanges it for tokens, writes the token file.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow

from crt import config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
REDIRECT_URI = "http://localhost/"


def extract_code_from_url(url: str) -> str:
    """Extract the OAuth ?code= parameter from a callback URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "code" not in qs:
        raise ValueError(
            "URL missing 'code' parameter; check that you copied the FULL URL "
            "from your browser after consent."
        )
    return qs["code"][0]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    flow = Flow.from_client_secrets_file(
        config.YT_CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    print("Open this URL in your browser, sign in, and consent:")
    print()
    print(auth_url)
    print()
    print("After consenting, your browser will redirect to a URL like:")
    print("    http://localhost/?code=4/0AX_...&scope=...")
    print("It will show 'connection refused' (no listener on localhost). That's expected.")
    print("Copy the FULL URL from the browser address bar and paste it below.")
    print()

    callback_url = input("Paste the URL: ").strip()
    code = extract_code_from_url(callback_url)

    flow.fetch_token(code=code)
    creds = flow.credentials

    os.makedirs(os.path.dirname(config.YT_TOKEN_FILE), exist_ok=True)
    with open(config.YT_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to {config.YT_TOKEN_FILE}")
    print("Bootstrap complete. You can now run `crt-daemon`.")


if __name__ == "__main__":
    main()
