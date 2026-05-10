from __future__ import annotations

import httpx


class DaemonClient:
    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def fetch_library(self) -> dict:
        r = self._client.get("/library/items")
        r.raise_for_status()
        return r.json()

    def fetch_status(self) -> dict:
        r = self._client.get("/status")
        r.raise_for_status()
        return r.json()

    def next(self) -> dict:
        r = self._client.post("/control/next")
        r.raise_for_status()
        return r.json()

    def prev(self) -> dict:
        r = self._client.post("/control/prev")
        r.raise_for_status()
        return r.json()

    def toggle(self) -> dict:
        r = self._client.post("/control/toggle")
        r.raise_for_status()
        return r.json()

    def stop(self) -> dict:
        r = self._client.post("/control/stop")
        r.raise_for_status()
        return r.json()

    def play(self, video_id: str) -> dict:
        r = self._client.post(f"/control/play/{video_id}")
        r.raise_for_status()
        return r.json()

    def loop_toggle(self) -> dict:
        r = self._client.post("/control/loop/toggle")
        r.raise_for_status()
        return r.json()

    def trigger_sync(self) -> dict:
        r = self._client.post("/control/sync")
        r.raise_for_status()
        return r.json()

    def calibrate(self) -> dict:
        r = self._client.post("/control/calibrate")
        r.raise_for_status()
        return r.json()
