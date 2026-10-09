"""Polite GET-only transport with bounded retries, cache and robots checks."""

import email.utils
import ipaddress
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from . import __version__
from .util import ExportError, digest, write_json


class FetchError(ExportError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _Redirects(HTTPRedirectHandler):
    def __init__(self, client):
        self.client = client

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.client.assert_url(newurl)
        if not self.client.loading_robots:
            self.client.check_robots(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    def __init__(self, cache_dir, *, delay=0.6, timeout=30, retries=3,
                 cache_hours=24, max_bytes=16 * 1024 * 1024, allow_private=False):
        self.cache_dir = Path(cache_dir)
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.retries = retries
        self.cache_seconds = cache_hours * 3600
        self.max_bytes = max_bytes
        self.allow_private = allow_private
        self.user_agent = f"StoreCatalogExporter/{__version__}"
        self.robot_agent = "StoreCatalogExporter"
        self.robots = {}
        self.loading_robots = False
        self.last_request = {}
        self.requests = 0
        self.cache_hits = 0
        self.response_metadata = {}
        self.opener = build_opener(ProxyHandler({}), _Redirects(self))

    def assert_url(self, url):
        p = urlsplit(url)
        if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
            raise FetchError("Only public HTTP(S) URLs without credentials are accepted.")
        try:
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        except (OSError, ValueError) as exc:
            raise FetchError(f"Cannot resolve host {p.hostname}.") from exc
        if not self.allow_private:
            for item in addresses:
                address = ipaddress.ip_address(item[4][0].split("%")[0])
                if not address.is_global:
                    raise FetchError("Private, loopback and reserved network addresses are blocked.")
        return p

    @staticmethod
    def origin(url):
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def check_robots(self, url):
        origin = self.origin(url)
        if origin not in self.robots:
            parser = RobotFileParser(origin + "/robots.txt")
            self.loading_robots = True
            try:
                try:
                    body, _ = self._request(origin + "/robots.txt")
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
                except FetchError as exc:
                    if exc.status in (404, 410):
                        parser.parse([])
                    else:
                        raise FetchError("robots.txt unavailable or denied; export stopped.") from exc
            finally:
                self.loading_robots = False
            self.robots[origin] = parser
        parser = self.robots[origin]
        if not parser.can_fetch(self.robot_agent, url):
            raise FetchError("robots.txt disallows this request.")
        return parser

    def _pause(self, url):
        origin = self.origin(url)
        parser = self.robots.get(origin)
        robot_delay = parser.crawl_delay(self.robot_agent) if parser else None
        request_rate = parser.request_rate(self.robot_agent) if parser else None
        rate_delay = request_rate.seconds / request_rate.requests if request_rate else 0
        interval = max(self.delay, robot_delay or 0, rate_delay)
        elapsed = time.monotonic() - self.last_request.get(origin, 0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self.last_request[origin] = time.monotonic()

    @staticmethod
    def _retry_seconds(value, attempt):
        if value:
            try:
                return min(120.0, max(0.0, float(value)))
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(value)
                    return min(120.0, max(0.0, (when - datetime.now(timezone.utc)).total_seconds()))
                except (ValueError, TypeError):
                    pass
        return min(30.0, 2 ** attempt)

    def _request(self, url, max_bytes=None):
        self.assert_url(url)
        cap = self.max_bytes if max_bytes is None else max_bytes
        for attempt in range(self.retries + 1):
            self._pause(url)
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
            self.requests += 1
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read(cap + 1)
                    if len(body) > cap:
                        raise FetchError("Response exceeds configured size limit.")
                    return body, response.headers.get("Content-Type", "").split(";")[0]
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After")
                exc.close()
                if status not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise FetchError(f"HTTP {status} from public endpoint.", status) from exc
                time.sleep(self._retry_seconds(retry_after, attempt))
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise FetchError("Network request failed after bounded retries.") from exc
                time.sleep(self._retry_seconds(None, attempt))
        raise FetchError("Request failed.")

    def text(self, url, *, refresh=False):
        self.assert_url(url)
        self.check_robots(url)
        path = self.cache_dir / (digest(url) + ".json")
        if not refresh and self.cache_seconds > 0 and path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached["url"] == url and time.time() - cached["timestamp"] < self.cache_seconds:
                    self.cache_hits += 1
                    self.response_metadata[url] = {"url": url,
                                                   "fetched_at": datetime.fromtimestamp(cached["timestamp"], timezone.utc).isoformat(),
                                                   "cache_hit": True}
                    return cached["text"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        body, _ = self._request(url)
        text = body.decode("utf-8-sig", errors="replace")
        timestamp = time.time()
        self.response_metadata[url] = {"url": url, "fetched_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                                       "cache_hit": False}
        write_json(path, {"url": url, "timestamp": timestamp, "text": text})
        return text

    def json(self, url):
        try:
            return json.loads(self.text(url))
        except ValueError as exc:
            cache_path = self.cache_dir / (digest(url) + ".json")
            if cache_path.exists():
                cache_path.unlink()  # A malformed response must not poison the next resume.
            raise FetchError("Expected JSON; endpoint returned an unsupported page or malformed JSON.") from exc

    def download(self, url, path, max_bytes):
        self.assert_url(url)
        self.check_robots(url)
        body, content_type = self._request(url, max_bytes)
        if not content_type.startswith(("image/", "video/")):
            raise FetchError("Media URL did not return an image or video Content-Type.")
        # Reuse the atomic writer pattern without decoding binary media.
        import os
        import tempfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".media-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return len(body)
