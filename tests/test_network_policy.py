from contextlib import asynccontextmanager
import socket
import pytest


async def test_image_connection_uses_checked_ip_and_tls_name(monkeypatch):
    from app.imports.markdown_storage import download_image
    from app.config import get_settings

    seen = {}
    monkeypatch.setattr(get_settings(), "allowed_image_hosts", ["images.example.test"])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )

    class Response:
        is_redirect = False

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b"image"

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            seen.update(url=str(url), **kwargs)
            yield Response()

    monkeypatch.setattr("app.imports.markdown_storage.httpx.AsyncClient", Client)
    assert (
        await download_image("https://images.example.test/image.png", 100) == b"image"
    )
    assert seen["url"] == "https://93.184.216.34/image.png"
    assert seen["headers"]["Host"] == "images.example.test"
    assert seen["extensions"]["sni_hostname"] == "images.example.test"


async def test_image_private_address_and_unknown_host_rejected(monkeypatch):
    from app.imports.markdown_storage import download_image
    from app.imports.storage import ImportStorageError
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "allowed_image_hosts", ["images.example.test"])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ImportStorageError):
        await download_image("https://images.example.test/x", 100)
    with pytest.raises(ImportStorageError):
        await download_image("https://unknown.example.test/x", 100)
