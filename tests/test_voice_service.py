"""Real container protocol and pipeline, mock providers, isolated website sessions."""

import asyncio
import io
import json
import math
import os
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app.core.security import Principal
from app.h5 import voice
from app.main import app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VOICE_TEST_URL"), reason="Run scripts/verify_voice.py"
    ),
]


def audio():
    pcm = b"".join(
        struct.pack("<h", round(math.sin(index * 2 * math.pi * 440 / 16000) * 10000))
        for index in range(16000)
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return pcm, output.getvalue()


@pytest.fixture
def real_voice(monkeypatch):
    settings = voice.get_settings().model_copy(
        update={"voice_service_url": os.environ["VOICE_TEST_URL"]}
    )
    monkeypatch.setattr(voice, "get_settings", lambda: settings)


async def test_real_http_asr_then_polish(client, real_voice):
    session = await client.post("/api/v2/voice/session", json={})
    assert session.status_code == 200, session.text
    _, wav = audio()
    response = await client.post(
        "/api/v2/voice/transcriptions",
        headers={"Authorization": "Bearer " + session.json()["token"]},
        files={"file": ("synthetic.wav", wav, "audio/wav")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["type"] == "final" and result["text"].strip()
    assert result["polish_status"] == "applied" and result["polished"] is True
    assert result["degraded"] is False


async def test_real_websocket_audio_and_polished_final(client, real_voice, monkeypatch):
    session = await client.post("/api/v2/voice/session", json={})
    token = session.json()["token"]
    from uuid import UUID

    user = (await client.get("/api/v1/auth/me")).json()

    async def authenticated(connection):
        return Principal(UUID(user["id"]), user["username"], frozenset())

    # HTTP bootstrap above exercises real login. Avoid sharing a pooled DB
    # connection between pytest's event loop and TestClient's separate loop.
    monkeypatch.setattr(voice, "current_session", authenticated)
    pcm, _ = audio()

    def browser():
        with (
            TestClient(app) as browser_client,
            browser_client.websocket_connect(
                "/api/v2/voice/transcriptions/stream", headers={"Origin": "http://test"}
            ) as socket,
        ):
            socket.send_json(
                {
                    "type": "start",
                    "protocol_version": "1",
                    "format": "pcm16",
                    "sample_rate": 16000,
                    "language": "zh",
                    "auth_token": token,
                }
            )
            while socket.receive_json()["type"] != "ready":
                pass
            for offset in range(0, len(pcm), 3200):
                socket.send_bytes(pcm[offset : offset + 3200])
            socket.send_json({"type": "commit"})
            result = socket.receive_json()
            assert result["type"] == "final", json.dumps(result, ensure_ascii=False)
            assert result["text"].strip() and result["polish_status"] == "applied"
            assert result["degraded"] is False

    await asyncio.to_thread(browser)
