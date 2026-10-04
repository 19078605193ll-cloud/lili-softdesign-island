"""Same-origin browser sessions. Redis is revocation state, never authority for roles."""

from __future__ import annotations

import hashlib
import asyncio
import json
import secrets
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Request
from starlette.requests import HTTPConnection
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, func

from app.config import get_settings
from app.db import SessionFactory
from app.models import User, UserRole
from app.core.errors import fail

ROLE_PERMISSIONS = {
    "editor": {"imports:read", "imports:edit"},
    "reviewer": {"imports:read", "imports:review"},
    "publisher": {"imports:read", "imports:publish"},
    "administrator": {
        "imports:read",
        "imports:edit",
        "imports:review",
        "imports:publish",
        "imports:delete",
        "audit:read",
        "users:read",
        "users:manage",
    },
}
passwords = PasswordHash.recommended()
dummy_hash = passwords.hash(secrets.token_urlsafe(32))
router = APIRouter(tags=["identity"])


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    username: str
    permissions: frozenset[str]


def redis_client() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie(response, name, value, ttl):
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=get_settings().secure_cookie,
        samesite="lax",
        path="/",
        max_age=ttl,
    )


def check_origin(request: HTTPConnection):
    if request.headers.get("origin") != get_settings().public_origin:
        raise fail(403, "CSRF_ORIGIN", "请求来源无效")


async def current_session(request: HTTPConnection) -> Principal:
    """Authenticate HTTP or WebSocket cookies without changing HTTP CSRF policy."""
    if getattr(request.state, "principal", None):
        return request.state.principal
    token = request.cookies.get(get_settings().session_cookie)
    if not token:
        raise fail(401, "LOGIN_REQUIRED", "请先登录")
    try:
        async with redis_client() as redis:
            raw = await redis.get("session:" + digest(token))
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc
    if not raw:
        raise fail(401, "SESSION_EXPIRED", "登录已过期")
    data = json.loads(raw)
    async with SessionFactory() as session:
        user = await session.get(User, uuid.UUID(data["user_id"]))
        if not user or not user.active or user.auth_version != data["auth_version"]:
            raise fail(401, "SESSION_REVOKED", "登录已失效")
        roles = await session.scalars(
            select(UserRole.role).where(UserRole.user_id == user.id)
        )
        perms = frozenset(
            p for role in roles for p in ROLE_PERMISSIONS.get(role, set())
        )
        principal = Principal(user.id, user.username, perms)
    request.state.principal = principal
    request.state.session = data
    return principal


async def current_user(request: Request) -> Principal:
    principal = await current_session(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), request.state.session["csrf"]
        ):
            raise fail(403, "CSRF_TOKEN", "请求验证已失效，请刷新页面")
    return principal


async def require(request: Request, permission: str) -> Principal:
    user = await current_user(request)
    if permission not in user.permissions:
        raise fail(403, "PERMISSION_DENIED", "没有执行此操作的权限")
    return user


async def protect(request: HTTPConnection):
    # WebSocket routes authenticate their handshake and protocol explicitly.
    if request.scope["type"] == "websocket":
        return
    path = request.url.path
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and not get_settings().writes_enabled
    ):
        raise fail(503, "MAINTENANCE", "系统正在维护，请稍后重试")
    if path.startswith("/api/v2/learning/"):
        await current_user(request)
    if (
        path.startswith(("/admin/", "/api/v1/admin/", "/api/v2/admin/"))
        and path != "/admin/login"
    ):
        permission = "imports:read"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            permission = "imports:edit"
            if path.endswith("/publish") or path.endswith("/conflict"):
                permission = "imports:publish"
            elif path.endswith(
                ("/review", "/approve-ready", "/approve", "/approve-items", "/reject")
            ):
                permission = "imports:review"
            elif request.method == "DELETE" or path.endswith("/restore"):
                permission = "imports:delete"
            elif path.endswith(("/preview", "/repair-preview")):
                permission = "imports:read"
        if path.startswith("/api/v1/admin/audit"):
            permission = "audit:read"
        if path == "/admin/users" or path == "/api/v1/admin/users" or path.startswith("/api/v1/admin/users/"):
            permission = "users:read" if request.method in {"GET", "HEAD", "OPTIONS"} else "users:manage"
        # Task-specific permissions are checked after loading their trusted DB kind.
        if path.startswith("/api/v2/admin/") and (
            path.endswith("/jobs") or path.endswith("/retry")
        ):
            permission = "imports:read"
        await require(request, permission)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            import re

            retired = (
                path == "/api/v1/admin/import-batches"
                or re.fullmatch(
                    r"/api/v1/admin/import-batches/[^/]+/(suggest-sections|sections|parse|repair|reconcile|classify)",
                    path,
                )
                or re.fullmatch(
                    r"/api/v1/admin/markdown-batches/[^/]+/(classify|assist-pending|images/retry|supplement)",
                    path,
                )
                or re.fullmatch(
                    r"/api/v1/admin/markdown-batches/[^/]+/blocks/[^/]+/assist", path
                )
                or re.fullmatch(r"/api/v1/admin/import-(items|groups)/.*", path)
                or re.fullmatch(
                    r"/api/v1/admin/import-batches/[^/]+/approve-items", path
                )
            )
            if retired:
                raise fail(
                    410,
                    "ENDPOINT_RETIRED",
                    "旧同步处理入口已停用，请使用 /api/v2/admin 的任务接口",
                )


@router.get("/api/v1/auth/csrf")
async def csrf(request: Request):
    token = request.cookies.get(get_settings().session_cookie)
    try:
        async with redis_client() as redis:
            raw = await redis.get("session:" + digest(token)) if token else None
            if raw:
                data = json.loads(raw)
                async with SessionFactory() as session:
                    user = await session.get(User, uuid.UUID(data["user_id"]))
                    valid = user and user.active and user.auth_version == data["auth_version"]
                if valid:
                    return JSONResponse(
                        {"csrf_token": data["csrf"]},
                        headers={"Cache-Control": "no-store"},
                    )
                await redis.delete("session:" + digest(token))
            nonce, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            await redis.set("prelogin:" + digest(nonce), csrf_token, ex=600)
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc
    response = JSONResponse(
        {"csrf_token": csrf_token}, headers={"Cache-Control": "no-store"}
    )
    cookie(response, get_settings().session_cookie + "-prelogin", nonce, 600)
    return response


async def check_prelogin(request, redis):
    check_origin(request)
    # /auth/csrf returns a session token while the browser is already logged in.
    # Accept that token for account switching, with the same live-user, revocation
    # and CSRF checks used for other authenticated writes.
    token = request.cookies.get(get_settings().session_cookie)
    raw = await redis.get("session:" + digest(token)) if token else None
    if raw and secrets.compare_digest(
        json.loads(raw)["csrf"], request.headers.get("x-csrf-token", "")
    ):
        await current_user(request)
        return
    nonce = request.cookies.get(get_settings().session_cookie + "-prelogin", "")
    expected = await redis.get("prelogin:" + digest(nonce))
    if not expected or not secrets.compare_digest(expected, request.headers.get("x-csrf-token", "")):
        raise fail(403, "CSRF_TOKEN", "请刷新登录或注册页面")


async def limit_attempts(redis, key, limit, code, message):
    count = await redis.eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],900) end; return n",
        1, key,
    )
    if count > limit:
        raise fail(429, code, message)


async def start_session(request, user_id, redis, *, status_code=200):
    token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        async with SessionFactory.begin() as session:
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if not user or not user.active:
                raise fail(401, "INVALID_CREDENTIALS", "账号或密码错误")
            await redis.set(
                "session:" + digest(token),
                json.dumps(dict(user_id=str(user.id), auth_version=user.auth_version, csrf=csrf_token)),
                ex=get_settings().session_ttl_seconds,
            )
            user.last_login_at = func.now()
            old = request.cookies.get(get_settings().session_cookie)
            if old:
                await redis.delete("session:" + digest(old))
            nonce = request.cookies.get(get_settings().session_cookie + "-prelogin", "")
            await redis.delete("prelogin:" + digest(nonce))
    except Exception:
        try:
            await redis.delete("session:" + digest(token))
        except RedisError:
            pass
        raise
    response = JSONResponse(
        {"csrf_token": csrf_token, "authenticated": True},
        status_code=status_code, headers={"Cache-Control": "no-store"},
    )
    cookie(response, get_settings().session_cookie, token, get_settings().session_ttl_seconds)
    response.delete_cookie(get_settings().session_cookie + "-prelogin", path="/", secure=get_settings().secure_cookie, httponly=True, samesite="lax")
    return response


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=12, max_length=128)


@router.post("/api/v1/auth/login")
async def login(request: Request, payload: Login):
    check_origin(request)
    ip = request.client.host if request.client else "unknown"
    try:
        async with redis_client() as redis:
            await check_prelogin(request, redis)
            identifier = payload.username.strip().casefold()
            for key, limit in [
                ("login:ip:" + digest(ip), 60),
                ("login:account:" + digest(ip + ":" + identifier), 10),
            ]:
                await limit_attempts(redis, key, limit, "LOGIN_RATE_LIMIT", "登录尝试过多，请稍后重试")
            async with SessionFactory() as session:
                user = await session.scalar(
                    select(User).where(User.username == identifier)
                )
                if user is None:
                    from app.core.identifiers import normalize_email
                    try:
                        email = normalize_email(identifier)
                    except ValueError:
                        email = None
                    if email:
                        user = await session.scalar(select(User).where(User.email == email))

                valid = await asyncio.to_thread(
                    passwords.verify,
                    payload.password,
                    user.password_hash if user else dummy_hash,
                )
                if not valid or not user or not user.active:
                    raise fail(401, "INVALID_CREDENTIALS", "账号或密码错误")
                user_id = user.id
            return await start_session(request, user_id, redis)
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc


@router.post("/api/v1/auth/logout")
async def logout(request: Request):
    await current_user(request)
    try:
        async with redis_client() as redis:
            await redis.delete(
                "session:" + digest(request.cookies[get_settings().session_cookie])
            )
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "登录服务暂不可用") from exc
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(get_settings().session_cookie, path="/", secure=get_settings().secure_cookie, httponly=True, samesite="lax")
    return response


@router.get("/api/v1/auth/me")
async def me(request: Request):
    user = await current_user(request)
    from app.core.profile import profile_read
    async with SessionFactory() as session:
        record = await session.get(User, user.id)
        return profile_read(record, user)


@router.get("/admin/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse("""<!doctype html><html lang="zh-CN"><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"><title>软设岛登录</title>
<style>body{font:16px system-ui;max-width:420px;margin:12vh auto;padding:24px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0}#error{color:#a00}</style>
<h1>软设岛</h1><form><label>账号<input name="username" autocomplete="username" required maxlength="100"></label><label>密码<input name="password" type="password" autocomplete="current-password" required minlength="12" maxlength="128"></label><button>登录</button><p id="error" role="alert"></p></form>
<script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const c=await fetch('/api/v1/auth/csrf');const token=await c.json();if(!c.ok)throw Error(token.detail);const r=await fetch('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrf_token},body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});const d=await r.json();if(!r.ok)throw Error(d.detail);location.href='/admin/imports'}catch(x){document.getElementById('error').textContent=x.message}finally{b.disabled=false}};</script></html>""")
