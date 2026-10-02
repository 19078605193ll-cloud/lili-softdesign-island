"""Anonymous registration creates learners only, using the existing browser sessions."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.errors import fail
from app.core.identifiers import (
    identifiers_available,
    normalize_email,
    normalize_username,
)
from app.core.security import (
    check_prelogin,
    digest,
    limit_attempts,
    passwords,
    redis_client,
    start_session,
)
from app.db import SessionFactory
from app.models import AuditEvent, User

router = APIRouter(tags=["identity"])


class Register(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    email: str
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value):
        return normalize_username(value)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        return normalize_email(value)

    @model_validator(mode="after")
    def matching(self):
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


async def registration_guard(request: Request):
    try:
        async with redis_client() as redis:
            await check_prelogin(request, redis)
            ip = request.client.host if request.client else "unknown"
            await limit_attempts(
                redis,
                "register:ip:" + digest(ip),
                10,
                "REGISTER_RATE_LIMIT",
                "注册尝试过多，请15分钟后再试",
            )
    except RedisError as exc:
        raise fail(503, "IDENTITY_UNAVAILABLE", "注册服务暂不可用，请稍后重试") from exc


@router.post(
    "/api/v1/auth/register", status_code=201, dependencies=[Depends(registration_guard)]
)
async def register(payload: Register, request: Request):
    password_hash = await asyncio.to_thread(passwords.hash, payload.password)
    user_id = uuid.uuid4()
    try:
        async with SessionFactory.begin() as session:
            await identifiers_available(session, payload.username, payload.email)
            session.add(
                User(
                    id=user_id,
                    username=payload.username,
                    email=payload.email,
                    password_hash=password_hash,
                )
            )
            session.add(
                AuditEvent(
                    user_id=None,
                    action="user:register",
                    object_id=str(user_id),
                    request_id=request.state.request_id,
                    summary={"result": "committed"},
                )
            )
    except IntegrityError as exc:
        # Serialize normal writers; constraints still protect against independent writers.
        name = (
            getattr(
                getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", ""
            )
            or ""
        )
        code = "EMAIL_TAKEN" if "email" in name else "USERNAME_TAKEN"
        raise fail(409, code, "邮箱或用户名已被使用，请换一个或直接登录") from exc
    try:
        async with redis_client() as redis:
            return await start_session(request, user_id, redis, status_code=201)
    except (RedisError, SQLAlchemyError):
        return JSONResponse(
            {
                "authenticated": False,
                "message": "注册成功，自动登录暂不可用，请使用新账号登录",
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )
