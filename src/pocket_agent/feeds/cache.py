"""Polite HTTP cache: 6 hour TTL, conditional requests, one second between hits per host."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

USER_AGENT = "pocket-agent/0.1 (local recruiting assistant; educational; +https://github.com/SimplifyJobs/New-Grad-Positions)"
DEFAULT_TTL_SECONDS = 6 * 60 * 60


class CachedHttp:
    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        min_interval: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.min_interval = min_interval
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
        )
        self._owns_client = client is None
        self._last_by_host: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get(self, url: str) -> tuple[str, bool]:
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.body"
        meta_path = self.cache_dir / f"{key}.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        age = time.time() - float(meta.get("fetched_at") or 0)
        if body_path.exists() and age < self.ttl_seconds:
            return body_path.read_text(encoding="utf-8"), True
        self._wait(url)
        headers = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        response = self._client.get(url, headers=headers)
        if response.status_code == 304 and body_path.exists():
            meta["fetched_at"] = time.time()
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            return body_path.read_text(encoding="utf-8"), True
        response.raise_for_status()
        text = response.text
        body_path.write_text(text, encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "fetched_at": time.time(),
                    "etag": response.headers.get("etag"),
                    "status": response.status_code,
                }
            ),
            encoding="utf-8",
        )
        return text, False

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_by_host.get(host, 0.0)
        delay = self.min_interval - (time.time() - last)
        if delay > 0:
            time.sleep(delay)
        self._last_by_host[host] = time.time()
