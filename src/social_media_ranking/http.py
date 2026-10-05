"""Thin HTTP helpers shared by the scrapers, with retry and redirect resolution."""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

USER_AGENT = "social-media-ranking/0.1 (+https://github.com/OpenPecha)"

# Status codes worth a second attempt; everything else fails fast.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# requests' default HTTPAdapter pool (10) is smaller than SocialCrawl's own
# thread pool can run concurrently (up to 50 -- config.SOCIALCRAWL_MAX_CONCURRENCY_CEILING).
# Confirmed live 2026-10-05: the default pool size produced a stream of
# "Connection pool is full, discarding connection" warnings under real
# concurrent load. Not fatal (requests falls back to opening unpooled
# connections), just wasteful -- sized generously here since this client is
# shared by every extractor, not just SocialCrawl's.
DEFAULT_POOL_SIZE = 50


class HttpClient:
    def __init__(self, timeout: int = 30, max_retries: int = 3, session: Optional[requests.Session] = None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        if session is None:
            adapter = requests.adapters.HTTPAdapter(pool_connections=DEFAULT_POOL_SIZE, pool_maxsize=DEFAULT_POOL_SIZE)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def _sleep(self, attempt: int, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 60))
                return
            except ValueError:
                pass
        # Exponential backoff with full jitter, so a pool of workers retrying
        # together doesn't re-collide on the same instant (section 4).
        time.sleep(random.uniform(0, min(2 ** attempt, 30)))

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries - 1:
                    self._sleep(attempt, response.headers.get("Retry-After"))
                    continue
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    self._sleep(attempt)
        raise requests.RequestException(f"request to {url} failed: {last_error}")

    def get_text(self, url: str) -> str:
        response = self.request("GET", url)
        if response.status_code >= 400:
            log.warning("GET %s -> %s", url, response.status_code)
            return ""
        return response.text

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        response = self.request("GET", url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def post_json(self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        response = self.request("POST", url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def resolve_redirect(self, url: str) -> str:
        """Follow a shortened URL to its destination (test case 4)."""
        try:
            response = self.request("HEAD", url, allow_redirects=True)
            if response.status_code >= 400:
                response = self.request("GET", url, allow_redirects=True, stream=True)
            return response.url or url
        except requests.RequestException as exc:
            log.warning("could not resolve %s: %s", url, exc)
            return url
