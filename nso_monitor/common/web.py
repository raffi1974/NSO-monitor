"""A polite HTTP client used by both agents.

* waits `request_delay_seconds` between two requests to the same server
* retries with growing pauses on timeouts, HTTP 429 (too many requests) and 5xx errors
* respects robots.txt
* if a site's TLS certificate is broken (common on NSO sites), retries once without certificate checks
  and remembers it in `insecure_hosts` so reports can mention it
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 NSO-Monitor/2.0")
RETRY_STATUS = {429, 500, 502, 503, 504}


class Blocked(Exception):
    """robots.txt disallows the URL."""


class PoliteClient:
    def __init__(self, delay: float = 0.5, timeout: float = 40, retries: int = 3, concurrency: int = 2,
                 respect_robots: bool = True):
        self.delay, self.retries, self.concurrency = float(delay), int(retries), max(1, int(concurrency))
        self.respect_robots = respect_robots
        common = dict(headers={"User-Agent": USER_AGENT, "Accept-Language": "en,ar;q=0.8,fr;q=0.7"},
                      timeout=httpx.Timeout(timeout, connect=20), follow_redirects=True,
                      limits=httpx.Limits(max_connections=self.concurrency * 2 + 2))
        self._client = httpx.Client(**common)
        self._insecure = httpx.Client(verify=False, **common)
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.insecure_hosts: set[str] = set()
        self.stats = {"requests": 0, "retries": 0}

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        self._insecure.close()

    # -- politeness ---------------------------------------------------------------------------
    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + self.delay
        if slot > now:
            time.sleep(slot - now)

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            cached = base in self._robots
            parser = self._robots.get(base)
        if not cached:
            parser = None
            try:
                r = self._send("GET", base + "/robots.txt", timeout=15)
                if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(r.text.splitlines())
            except httpx.HTTPError:
                parser = None
            with self._lock:
                self._robots[base] = parser
        return parser is None or parser.can_fetch("NSO-Monitor", url)

    # -- requests -----------------------------------------------------------------------------
    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        host = urlsplit(url).netloc
        client = self._insecure if host in self.insecure_hosts else self._client
        try:
            return client.request(method, url, **kw)
        except httpx.ConnectError as exc:
            if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
                log.info("broken TLS certificate on %s - retrying without certificate check", host)
                self.insecure_hosts.add(host)
                return self._insecure.request(method, url, **kw)
            raise

    def request(self, method: str, url: str, *, check_robots: bool = True, retries: int | None = None,
                **kw) -> httpx.Response:
        """`retries` overrides the client default for this call: 0 for quick probes where "no answer" is itself
        the finding, more for data downloads that must eventually succeed."""
        if check_robots and not self.allowed(url):
            raise Blocked(url)
        host = urlsplit(url).netloc
        tries = self.retries if retries is None else retries
        for attempt in range(tries + 1):
            self._wait(host)
            try:
                r = self._send(method, url, **kw)
            except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError) as exc:
                if attempt >= tries:
                    raise
                self._pause(attempt, None, f"{type(exc).__name__} on {url}")
                continue
            with self._lock:
                self.stats["requests"] += 1
            if r.status_code in RETRY_STATUS and attempt < tries:
                self._pause(attempt, r.headers.get("retry-after"), f"HTTP {r.status_code} on {url}")
                continue
            return r
        raise RuntimeError("unreachable")  # pragma: no cover

    def _pause(self, attempt: int, retry_after: str | None, why: str) -> None:
        with self._lock:
            self.stats["retries"] += 1
        try:
            wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            wait = 0.0
        wait = min(max(wait, 2.0 * 2 ** attempt), 60.0)
        log.debug("waiting %.0fs (%s)", wait, why)
        time.sleep(wait)

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw) -> Any:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def head(self, url: str, **kw) -> httpx.Response:
        r = self.request("HEAD", url, **kw)
        if r.status_code in (403, 405, 501):  # some servers refuse HEAD: ask for one byte instead
            r = self.get(url, headers={"Range": "bytes=0-0"}, **kw)
        return r

    # -- concurrency --------------------------------------------------------------------------
    def map(self, fn: Callable[[Any], Any], items: Iterable[Any]) -> list[tuple[Any, Any, Exception | None]]:
        """(item, result, error) for every item, in order; one failure never stops the others."""
        items = list(items)

        def safe(item):
            try:
                return item, fn(item), None
            except Exception as exc:  # noqa: BLE001 - the caller decides what a failure means
                return item, None, exc

        if self.concurrency == 1 or len(items) < 2:
            return [safe(i) for i in items]
        with ThreadPoolExecutor(self.concurrency) as pool:
            return list(pool.map(safe, items))
