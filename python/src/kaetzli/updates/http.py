"""What tools/lib.sh's fetch, gh_api and the tarball check did with curl: a GET that
follows redirects, retries a 5xx, 429 or network error four times (backing off 1, 2, 4,
8 s) and fails on an HTTP error; the same against GitHub's API with a token when there is
one; and a HEAD that says whether a URL is there.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from functools import cache

TIMEOUT = 60
USER_AGENT = "kaetzli-check-updates"


class FetchError(OSError):
    pass


class _KeepHead(urllib.request.HTTPRedirectHandler):
    """urllib turns a redirected HEAD into a GET, which would download the tarball."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and req.get_method() == "HEAD":
            new.method = "HEAD"
        return new


_opener = urllib.request.build_opener(_KeepHead)


def _request(url: str, method: str = "GET", headers: dict | None = None) -> bytes:
    last: Exception | None = None
    for attempt in range(5):
        if attempt:
            time.sleep(2 ** (attempt - 1))
        req = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            with _opener.open(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
    raise FetchError(f"{url}: {getattr(last, 'reason', None) or last}")


@cache
def github_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
            token = r.stdout.strip() if r.returncode == 0 else ""
        except FileNotFoundError:
            pass
    return token


class Http:
    def get(self, url: str) -> str:
        return _request(url).decode("utf-8", errors="replace")

    def get_bytes(self, url: str) -> bytes:
        return _request(url)

    def github(self, url: str) -> str:
        """GitHub's API: a token lifts the anonymous limit of 60 requests an hour."""
        headers = {"Accept": "application/vnd.github+json"}
        if token := github_token():
            headers["Authorization"] = f"Bearer {token}"
        return _request(url, headers=headers).decode("utf-8")

    def exists(self, url: str) -> bool:
        try:
            _request(url, method="HEAD")
        except FetchError:
            return False
        return True
