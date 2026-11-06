"""A small polite HTTP client: one User-Agent, redirects, timeouts and retries."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import USER_AGENT

log = logging.getLogger(__name__)


class Client:
    def __init__(self, *, retries: int = 3, backoff: float = 1.5, pause: float = 0.1) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=httpx.Timeout(60.0),
        )
        self.retries = retries
        self.backoff = backoff
        self.pause = pause

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with retries on 5xx, 429 and transport errors. Other statuses are returned as-is."""
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise httpx.HTTPStatusError(f"{resp.status_code} from {url}", request=resp.request, response=resp)
                if self.pause:
                    time.sleep(self.pause)
                return resp
            except httpx.HTTPError as exc:
                last = exc
                wait = self.backoff**attempt
                log.warning("GET %s failed (%s); retrying in %.1fs", url, exc, wait)
                time.sleep(wait)
        raise RuntimeError(f"GET {url} failed after {self.retries} attempts") from last

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_bytes(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        resp = self.get(url, params=params)
        resp.raise_for_status()
        return resp.content
