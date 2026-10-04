"""Fetching web pages for an agent, safely.

The backend runs next to Postgres, Redis and (in the cloud) metadata services, and an agent
fetches URLs it found on the open web. A page can point it at an internal address. So every
fetch resolves the host, refuses anything that is not a public address, re-checks every
redirect, and connects to the exact IP it checked, so a DNS answer cannot change afterwards.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 6 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0
USER_AGENT = "LogicCritic-ResearchAgent/1.0 (+evidence-gathering; contact: see deployment)"
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
PDF_TYPE = "application/pdf"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """A fetch that was refused or failed. The message is written for the agent to read."""


def is_public_address(address: str) -> bool:
    """True only for addresses that are routable on the public internet."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False  # carrier-grade NAT, used for internal networks by some providers
    return ip.is_global and not ip.is_multicast


async def resolve_host(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        info = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FetchError(f"Could not resolve {host}.") from error
    return sorted({item[4][0] for item in info})


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    status: int
    content_type: str
    content: bytes


async def fetch_public(
    url: str,
    *,
    resolver: Resolver = resolve_host,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = MAX_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    user_agent: str = USER_AGENT,
) -> FetchedPage:
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        current = url
        for _ in range(max_redirects + 1):
            parts, address = await _vet(current, resolver)
            response, body = await _get_pinned(http, parts, address, max_bytes, user_agent)
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("The server redirected without saying where.")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise FetchError(f"The server answered {response.status_code} for {current}.")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in (*TEXT_TYPES, PDF_TYPE):
                raise FetchError(
                    f"Cannot read content of type {content_type or 'unknown'}; only web pages, "
                    "plain text and PDFs are supported."
                )
            return FetchedPage(url, current, response.status_code, content_type, body)
        raise FetchError(f"Too many redirects (more than {max_redirects}).")
    except httpx.TimeoutException as error:
        raise FetchError("The server took too long to answer.") from error
    except httpx.HTTPError as error:
        raise FetchError(f"The request failed ({type(error).__name__}).") from error
    finally:
        if own_client:
            await http.aclose()


async def _vet(url: str, resolver: Resolver):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs can be fetched.")
    if not parts.hostname:
        raise FetchError("The URL has no host.")
    if parts.username or parts.password:
        raise FetchError("URLs containing credentials are not allowed.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = await resolver(parts.hostname, port)
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise FetchError(
            f"{parts.hostname} is not a public internet address, so it was not fetched."
        )
    return parts, addresses[0]


async def _get_pinned(
    http: httpx.AsyncClient, parts, address: str, max_bytes: int, user_agent: str
):
    """GET connecting to the vetted IP, while still presenting the real host name."""
    host = parts.hostname
    port = f":{parts.port}" if parts.port else ""
    ip = f"[{address}]" if ":" in address else address
    target = f"{parts.scheme}://{ip}{port}{parts.path or '/'}"
    if parts.query:
        target += f"?{parts.query}"
    headers = {
        "Host": parts.netloc,
        "User-Agent": user_agent,
        "Accept": "text/html,text/plain,application/pdf,*/*;q=0.5",
    }
    async with http.stream(
        "GET", target, headers=headers, extensions={"sni_hostname": host}, follow_redirects=False
    ) as response:
        if response.status_code in REDIRECT_STATUSES or response.status_code >= 400:
            return response, b""
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise FetchError(f"The page is larger than {max_bytes // (1024 * 1024)} MB.")
            chunks.append(chunk)
        return response, b"".join(chunks)


# -- HTML to text

SKIP = {
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
    "template",
}
BLOCK = {
    "p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table", "blockquote",
    "pre", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "header",
}  # fmt: skip
HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "####", "h6": "####"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._heading: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK and not self._skip:
            self.parts.append("\n\n")
            if tag in HEADINGS:
                self.parts.append(HEADINGS[tag] + " ")

    def handle_endtag(self, tag):
        if tag in SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK and not self._skip:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) with block elements as paragraphs and headings as Markdown headings."""
    extractor = _TextExtractor()
    extractor.feed(html)
    paragraphs = []
    for block in "".join(extractor.parts).split("\n\n"):
        text = " ".join(block.split())
        if len(text.replace("#", "").strip()) >= 3:
            paragraphs.append(text)
    return " ".join(extractor.title.split()), "\n\n".join(paragraphs)
