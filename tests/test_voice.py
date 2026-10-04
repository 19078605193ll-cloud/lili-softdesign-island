from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app.config import get_settings
from app.core.security import Principal, digest, redis_client
from app.h5 import voice
from scripts.init_voice import initialize


def test_voice_secret_initialization_is_persistent(tmp_path):
    target = tmp_path / "voice.env"
    assert initialize(target)
    original = target.read_text()
    assert len(original.split("=", 1)[1].strip()) >= 32
    assert not initialize(target)
    assert target.read_text() == original


@pytest.fixture
def upstream(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/anonymous-tokens":
            return httpx.Response(
                200, json={"token": "test-short-token", "expires_in_seconds": 600}
            )
        return httpx.Response(
            200,
            json={
                "type": "final",
                "text": "整理后的语音文字",
                "polish_status": "applied",
            },
        )

    monkeypatch.setattr(
        voice,
        "service_client",
        lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), timeout=timeout
        ),
    )
    return requests


@pytest.mark.integration
async def test_session_requires_login_csrf_and_origin(client, upstream):
    result = await client.post("/api/v2/voice/session", json={})
    assert result.status_code == 200
    assert result.json()["token"] == "test-short-token"
    assert result.headers["cache-control"] == "no-store"
    assert upstream[-1].headers["origin"] == "http://test"
    async with redis_client() as redis:
        assert await redis.ttl("voice:token:" + digest("test-short-token")) > 0
    assert (
        await client.post(
            "/api/v2/voice/session", headers={"X-CSRF-Token": "invalid"}, json={}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v2/voice/session",
            headers={"Origin": "https://other.example"},
            json={},
        )
    ).status_code == 403
    client.cookies.clear()
    assert (await client.post("/api/v2/voice/session", json={})).status_code == 401


@pytest.mark.integration
async def test_upload_needs_owned_short_token_and_keeps_upstream_result(
    client, upstream
):
    await client.post("/api/v2/voice/session", json={})
    client.headers.pop(
        "X-CSRF-Token"
    )  # The SDK sends only its short-lived bearer token.
    headers = {"Authorization": "Bearer test-short-token"}
    result = await client.post(
        "/api/v2/voice/transcriptions",
        headers=headers,
        files={"file": ("audio.wav", b"RIFF-recording", "audio/wav")},
    )
    assert result.status_code == 200 and result.json()["polish_status"] == "applied"
    assert b"RIFF-recording" in upstream[-1].content
    assert upstream[-1].headers["authorization"] == "Bearer test-short-token"
    assert (
        await client.post(
            "/api/v2/voice/transcriptions", files={"file": ("audio.wav", b"x")}
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v2/voice/transcriptions",
            headers={**headers, "Origin": "https://other.example"},
            files={"file": ("a.wav", b"x")},
        )
    ).status_code == 403
    async with redis_client() as redis:
        await redis.set(
            "voice:token:" + digest("test-short-token"), str(uuid.uuid4()), ex=600
        )
    assert (
        await client.post(
            "/api/v2/voice/transcriptions",
            headers=headers,
            files={"file": ("a.wav", b"x")},
        )
    ).status_code == 401


@pytest.mark.integration
async def test_unready_and_timeout_leave_other_endpoints_available(client, monkeypatch):
    def unready(request):
        return httpx.Response(
            503, json={"status": "not_ready", "errors": ["ASR_API_KEY is missing"]}
        )

    monkeypatch.setattr(
        voice,
        "service_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(unready)),
    )
    result = await client.post("/api/v2/voice/session", json={})
    assert result.status_code == 503 and result.json()["code"] == "VOICE_NOT_READY"
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    def timed_out(request):
        raise httpx.ReadTimeout("upstream timed out", request=request)

    monkeypatch.setattr(
        voice,
        "service_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(timed_out)),
    )
    assert (await client.post("/api/v2/voice/session", json={})).status_code == 504


@pytest.mark.integration
async def test_preserves_capacity_error_and_limits_upload(
    client, upstream, monkeypatch
):
    await client.post("/api/v2/voice/session", json={})

    def overloaded(request):
        return httpx.Response(
            429,
            headers={"Retry-After": "3", "X-Retry-After-Ms": "3000"},
            json={
                "type": "error",
                "code": "CAPACITY_REACHED",
                "message": "语音服务繁忙",
                "recoverable": True,
            },
        )

    monkeypatch.setattr(
        voice,
        "service_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(overloaded)),
    )
    headers = {"Authorization": "Bearer test-short-token"}
    result = await client.post(
        "/api/v2/voice/transcriptions", headers=headers, files={"file": ("a.wav", b"x")}
    )
    assert result.status_code == 429 and result.json()["code"] == "CAPACITY_REACHED"
    assert result.headers["x-retry-after-ms"] == "3000"
    monkeypatch.setattr(voice, "MAX_UPLOAD_BYTES", 16)
    result = await client.post(
        "/api/v2/voice/transcriptions",
        headers=headers,
        files={"file": ("a.wav", b"x" * 17)},
    )
    assert result.status_code == 413


async def test_websocket_transfers_audio_and_final_and_cleans_up(monkeypatch):
    principal = Principal(uuid.uuid4(), "voice-test", frozenset())
    observed = []
    closed = asyncio.Event()

    async def authenticated(connection):
        return principal

    async def token_check(token, user_id):
        assert token == "short-token" and user_id == str(principal.id)

    monkeypatch.setattr(voice, "current_session", authenticated)
    monkeypatch.setattr(voice, "validate_token", token_check)

    async def upstream(socket):
        try:
            observed.append(json.loads(await socket.recv()))
            await socket.send(json.dumps({"type": "ready", "protocol_version": "1"}))
            observed.append(await socket.recv())
            observed.append(json.loads(await socket.recv()))
            await socket.send(
                json.dumps(
                    {"type": "final", "text": "最终文字", "polish_status": "applied"}
                )
            )
            await socket.wait_closed()
        finally:
            closed.set()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = get_settings().model_copy(
            update={"voice_service_url": f"http://127.0.0.1:{port}"}
        )
        monkeypatch.setattr(voice, "get_settings", lambda: settings)
        from app.main import app

        def browser():
            with (
                TestClient(app) as client,
                client.websocket_connect(
                    "/api/v2/voice/transcriptions/stream",
                    headers={"Origin": "http://test"},
                ) as socket,
            ):
                socket.send_json({"type": "start", "auth_token": "short-token"})
                assert socket.receive_json()["type"] == "ready"
                socket.send_bytes(b"\x00\x01" * 1600)
                socket.send_json({"type": "commit"})
                assert socket.receive_json()["text"] == "最终文字"

        await asyncio.to_thread(browser)
        await asyncio.wait_for(closed.wait(), timeout=3)
    assert observed[1] == b"\x00\x01" * 1600
    assert observed[2] == {"type": "commit"}


def test_websocket_rejects_other_origin():
    app = FastAPI()
    app.include_router(voice.router)
    with TestClient(app) as client:
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/api/v2/voice/transcriptions/stream",
                headers={"Origin": "https://other.example"},
            ),
        ):
            pass
