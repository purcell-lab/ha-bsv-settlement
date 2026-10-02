"""Asynchronous client for the mock-only service."""
from urllib.parse import urlsplit
import asyncio
import aiohttp


class WalletError(Exception):
    """Safe error containing no auth token or response body."""


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL must not contain credentials, query or fragment")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/"):
        raise ValueError("Use an HTTP(S) origin, without an API path")
    # HTTP is intentionally allowed for isolated mock-only LAN testing.
    return url.rstrip("/")


class WalletAPI:
    def __init__(self, session, url, token):
        self.session = session
        self.url = validate_url(url)
        self.token = token

    async def call(self, method, path, data=None):
        try:
            async with self.session.request(
                method, self.url + path, json=data,
                headers={"Authorization": "Bearer " + self.token},
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False,
            ) as response:
                if response.status >= 400 or response.status < 200 or response.status >= 300:
                    raise WalletError(f"Mock wallet service returned HTTP {response.status}")
                result = await response.json()
                if result.get("mode") != "mock" and not path.startswith("/v1/wallet-bindings/"):
                    raise WalletError("This scaffold refuses non-mock wallet services")
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise WalletError("Mock wallet service connection or response failed") from exc
