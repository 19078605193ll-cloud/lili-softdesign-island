"""Authenticated, same-origin gateway to the isolated lili voice service."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from starlette.responses import JSONResponse, Response
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.core.errors import fail
from app.core.security import (
    check_origin,
    current_session,
    current_user,
    digest,
    redis_client,
)

router = APIRouter(prefix="/api/v2/voice", tags=["voice"])
TOKEN_TTL = 900  # Covers 10-minute recordings plus finalization / HTTP fallback.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_BYTES = 256 * 1024


def service_client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def service_url(path: str) -> str:
    settings = get_settings()
    if not settings.voice_enabled:
        raise fail(503, "VOICE_DISABLED", "语音输入暂未启用")
    if not settings.writes_enabled:
        raise fail(503, "MAINTENANCE", "系统正在维护，请稍后重试")
    return settings.voice_service_url.rstrip("/") + path


def upstream_headers() -> dict[str, str]:
    return {"Origin": get_settings().public_origin}


def voice_failure(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "type": "error",
            "code": code,
            "message": message,
            "recoverable": status in {429, 502, 503, 504},
        },
        status_code=status,
    )


def upstream_response(response: httpx.Response) -> Response:
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower()
        in {"content-type", "retry-after", "x-retry-after-ms", "x-error-code"}
    }
    return Response(response.content, status_code=response.status_code, headers=headers)


@router.post("/session")
async def create_voice_session(request: Request):
    user = await current_user(request)
    ready_url = service_url("/health/ready")
    try:
        async with redis_client() as redis:
            allowed = await redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1,
                "voice:starts:" + str(user.id),
            )
            if allowed > 10:
                raise fail(429, "RATE_LIMITED", "语音启动过于频繁，请稍后重试")
        async with service_client(8) as client:
            ready = await client.get(ready_url)
            if ready.status_code != 200:
                raise fail(
                    503,
                    "VOICE_NOT_READY",
                    "语音服务尚未就绪，请配置 ASR 和文本整理 API Key 后重建语音容器",
                )
            issued = await client.post(
                service_url("/v1/anonymous-tokens"),
                headers=upstream_headers(),
                json={"client_id": str(user.id)},
            )
            if issued.status_code != 200:
                return upstream_response(issued)
            token = issued.json().get("token")
            if not isinstance(token, str) or not token:
                raise fail(502, "VOICE_TOKEN_ERROR", "语音服务未返回有效令牌")
        async with redis_client() as redis:
            await redis.set("voice:token:" + digest(token), str(user.id), ex=TOKEN_TTL)
        return JSONResponse(
            {"token": token, "expires_in": TOKEN_TTL},
            headers={"Cache-Control": "no-store"},
        )
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc
    except httpx.TimeoutException as exc:
        raise fail(504, "VOICE_TIMEOUT", "连接语音服务超时，请稍后重试") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise fail(
            503, "VOICE_UNAVAILABLE", "语音服务暂不可用，请检查语音容器"
        ) from exc


async def validate_token(token: str, user_id: str) -> None:
    if not token or len(token) > 4096:
        raise fail(401, "VOICE_TOKEN_INVALID", "语音会话已过期，请重新录音")
    try:
        async with redis_client() as redis:
            owner = await redis.get("voice:token:" + digest(token))
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc
    if owner != user_id:
        raise fail(401, "VOICE_TOKEN_INVALID", "语音会话已过期，请重新录音")


@router.post("/transcriptions")
async def fallback_transcription(request: Request):
    # SDK uploads authenticate with a CSRF-initialized short-lived bearer token,
    # a current website session, and an exact Origin; ordinary HTTP CSRF stays intact.
    check_origin(request)
    user = await current_session(request)
    authorization = request.headers.get("authorization", "")
    token = (
        authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    )
    await validate_token(token, str(user.id))
    url = service_url("/v1/transcriptions")
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        return voice_failure(400, "INVALID_REQUEST", "需要上传录音文件")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            return voice_failure(413, "UPLOAD_TOO_LARGE", "录音文件过大，请缩短录音")
    headers = {
        **upstream_headers(),
        "Authorization": "Bearer " + token,
        "Content-Type": content_type,
        "X-Voice-Fallback": "1",
    }
    try:
        async with service_client(httpx.Timeout(125, connect=8)) as client:
            upload = asyncio.create_task(
                client.post(url, headers=headers, content=bytes(body))
            )

            async def disconnected():
                while True:
                    if (await request.receive())["type"] == "http.disconnect":
                        return

            monitor = asyncio.create_task(disconnected())
            try:
                done, _ = await asyncio.wait(
                    {upload, monitor}, return_when=asyncio.FIRST_COMPLETED
                )
                if upload in done:
                    return upstream_response(await upload)
                return Response(status_code=499)
            finally:
                for task in (upload, monitor):
                    task.cancel()
                await asyncio.gather(upload, monitor, return_exceptions=True)
    except httpx.TimeoutException:
        return voice_failure(504, "ASR_TIMEOUT", "语音转写超时，请稍后重试")
    except httpx.HTTPError:
        return voice_failure(503, "VOICE_UNAVAILABLE", "语音服务暂不可用")


@router.websocket("/transcriptions/stream")
async def stream_transcription(socket: WebSocket):
    tasks: list[asyncio.Task] = []
    accepted = False
    try:
        check_origin(socket)
        user = await current_session(socket)
        parsed = urlsplit(service_url("/v1/transcriptions/stream"))
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path,
                "",
                "",
            )
        )
        await socket.accept()
        accepted = True
        start_raw = await asyncio.wait_for(socket.receive_text(), timeout=8)
        if len(start_raw) > 8192:
            raise fail(400, "INVALID_REQUEST", "无效的语音初始化消息")
        start = json.loads(start_raw)
        if not isinstance(start, dict) or start.get("type") != "start":
            raise fail(400, "INVALID_REQUEST", "无效的语音初始化消息")
        await validate_token(str(start.get("auth_token") or ""), str(user.id))
        async with asyncio.timeout(800):
            async with connect(
                url,
                origin=get_settings().public_origin,
                open_timeout=8,
                max_size=MAX_MESSAGE_BYTES,
                proxy=None,
            ) as upstream:
                await upstream.send(start_raw)

                async def send_audio():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        payload = (
                            message.get("bytes")
                            if message.get("bytes") is not None
                            else message.get("text", "")
                        )
                        if len(payload) > MAX_MESSAGE_BYTES:
                            raise fail(413, "UPLOAD_TOO_LARGE", "录音数据块过大")
                        await upstream.send(payload)

                async def receive_results():
                    async for message in upstream:
                        if isinstance(message, str):
                            await socket.send_text(message)
                        else:
                            await socket.send_bytes(message)
                    await socket.close(code=upstream.close_code or 1000)

                tasks = [
                    asyncio.create_task(send_audio()),
                    asyncio.create_task(receive_results()),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
    except HTTPException as exc:
        if accepted:
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.send_json(
                    {
                        "type": "error",
                        "code": (exc.headers or {}).get("X-Error-Code", "VOICE_ERROR"),
                        "message": str(exc.detail),
                        "recoverable": False,
                    }
                )
                await socket.close(code=1008)
        else:
            await socket.close(code=1008)
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    except (OSError, TimeoutError, ValueError):
        if accepted:
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.send_json(
                    {
                        "type": "error",
                        "code": "VOICE_UNAVAILABLE",
                        "message": "语音连接中断，请稍后重试",
                        "recoverable": True,
                    }
                )
                await socket.close(code=1013)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
