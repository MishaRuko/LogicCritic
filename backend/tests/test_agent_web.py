import httpx
import pytest

from app.agent.web import FetchError, fetch_public, html_to_text, is_public_address

PUBLIC = "93.184.216.34"


async def resolves_to(*addresses: str):
    async def resolver(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolver


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "0.0.0.0",
        "100.64.0.1", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:10.0.0.1", "not-an-ip",
    ],
)  # fmt: skip
def test_non_public_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "http://user:pass@example.com/",
        "http:///nohost",
        "javascript:alert(1)",
    ],
)
async def test_unsafe_urls_are_refused_before_any_request(url: str) -> None:
    def handler(request):  # pragma: no cover - must never run
        raise AssertionError("a request was made")

    with pytest.raises(FetchError):
        await fetch_public(url, resolver=await resolves_to(PUBLIC), client=client_for(handler))


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["10.0.0.5"], ["169.254.169.254"], [PUBLIC, "10.0.0.5"], []],
)
async def test_hosts_resolving_to_internal_addresses_are_refused(addresses) -> None:
    def handler(request):  # pragma: no cover
        raise AssertionError("a request was made")

    with pytest.raises(FetchError, match="not a public internet address"):
        await fetch_public(
            "http://internal.example/",
            resolver=await resolves_to(*addresses),
            client=client_for(handler),
        )


async def test_the_request_connects_to_the_vetted_ip_with_the_real_host_and_sni() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_connected"] = request.url.host
        seen["host_header"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        seen["path"] = request.url.raw_path.decode()
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text="<p>hi</p>"
        )

    page = await fetch_public(
        "https://example.org/a/b?q=1",
        resolver=await resolves_to(PUBLIC),
        client=client_for(handler),
    )

    assert seen == {
        "host_connected": PUBLIC,
        "host_header": "example.org",
        "sni": "example.org",
        "path": "/a/b?q=1",
    }
    assert page.final_url == "https://example.org/a/b?q=1" and page.content_type == "text/html"


async def test_a_redirect_to_an_internal_host_is_refused() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.7"] if host == "internal.local" else [PUBLIC]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.local/admin"})

    with pytest.raises(FetchError, match="not a public internet address"):
        await fetch_public("https://example.org/", resolver=resolver, client=client_for(handler))


async def test_redirects_are_followed_and_relative_locations_resolved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.org" and request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="done")

    page = await fetch_public(
        "https://example.org/start", resolver=await resolves_to(PUBLIC), client=client_for(handler)
    )
    assert page.final_url == "https://example.org/final" and page.content == b"done"


async def test_a_redirect_loop_stops() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.org/again"})

    with pytest.raises(FetchError, match="Too many redirects"):
        await fetch_public(
            "https://example.org/", resolver=await resolves_to(PUBLIC), client=client_for(handler)
        )


async def test_unreadable_content_types_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")

    with pytest.raises(FetchError, match="Cannot read content of type application/zip"):
        await fetch_public(
            "http://example.org/f", resolver=await resolves_to(PUBLIC), client=client_for(handler)
        )


async def test_oversized_pages_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 5000)

    with pytest.raises(FetchError, match="larger than"):
        await fetch_public(
            "http://example.org/f",
            resolver=await resolves_to(PUBLIC),
            client=client_for(handler),
            max_bytes=1024,
        )


async def test_http_errors_are_reported_not_raised_raw() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    with pytest.raises(FetchError, match="answered 404"):
        await fetch_public(
            "http://example.org/missing",
            resolver=await resolves_to(PUBLIC),
            client=client_for(handler),
        )


async def test_timeouts_become_fetch_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(FetchError, match="too long"):
        await fetch_public(
            "http://example.org/", resolver=await resolves_to(PUBLIC), client=client_for(handler)
        )


def test_html_becomes_titled_markdown_like_paragraphs_without_boilerplate() -> None:
    html = """<html><head><title> Guideline  Update </title><style>p{}</style></head><body>
      <nav>Home | About</nav><h1>Recommendations</h1>
      <p>Use of drug X is <b>not</b> recommended.</p><script>track()</script>
      <ul><li>First point</li><li>Second point</li></ul><footer>(c) 2020</footer></body></html>"""
    title, text = html_to_text(html)
    assert title == "Guideline Update"
    assert text.split("\n\n") == [
        "# Recommendations",
        "Use of drug X is not recommended.",
        "First point",
        "Second point",
    ]


async def test_a_host_with_no_ipv6_route_is_reached_over_ipv4() -> None:
    v6, v4 = "2606:4700::6810:84e5", PUBLIC
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    page = await fetch_public(
        "https://example.org/", resolver=await resolves_to(v6, v4), client=client_for(handler)
    )
    assert tried == [v4] and page.content == b"ok"  # IPv4 is tried first


async def test_the_next_address_is_tried_when_one_cannot_be_connected_to() -> None:
    first, second = PUBLIC, "93.184.216.35"
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if request.url.host == first:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    page = await fetch_public(
        "https://example.org/",
        resolver=await resolves_to(first, second),
        client=client_for(handler),
    )
    assert tried == [first, second] and page.content == b"ok"


async def test_an_http_error_is_not_retried_on_another_address() -> None:
    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        return httpx.Response(403)

    with pytest.raises(FetchError, match="403"):
        await fetch_public(
            "https://example.org/",
            resolver=await resolves_to(PUBLIC, "93.184.216.35"),
            client=client_for(handler),
        )
    assert tried == [PUBLIC]


async def test_when_no_address_connects_the_failure_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    with pytest.raises(FetchError, match="ConnectError"):
        await fetch_public(
            "https://example.org/",
            resolver=await resolves_to(PUBLIC, "93.184.216.35"),
            client=client_for(handler),
        )
