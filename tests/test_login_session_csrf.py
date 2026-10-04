"""Login and registration must accept the CSRF token returned for a live session."""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.security import digest, passwords, redis_client
from app.models import User
from tests.test_registration import anonymous, payload

pytestmark = pytest.mark.integration
ADMIN_LOGIN = {"username": "test-admin", "password": "isolated-test-password"}


@pytest.mark.parametrize("initial_account", ["test-admin", "learner"])
async def test_login_with_existing_session_rotates_identity(
    client, session, initial_account
):
    if initial_account == "learner":
        session.add(
            User(
                username="learner",
                password_hash=passwords.hash(ADMIN_LOGIN["password"]),
            )
        )
        await session.commit()
    async with anonymous() as browser:
        first = await browser.post(
            "/api/v1/auth/login", json={**ADMIN_LOGIN, "username": initial_account}
        )
        assert first.status_code == 200, first.text
        old_cookie = browser.cookies[get_settings().session_cookie]
        token = (await browser.get("/api/v1/auth/csrf")).json()["csrf_token"]
        browser.headers["X-CSRF-Token"] = token
        result = await browser.post("/api/v1/auth/login", json=ADMIN_LOGIN)
        assert result.status_code == 200, result.text
        assert browser.cookies[get_settings().session_cookie] != old_cookie
        assert result.json()["csrf_token"] != token
        async with redis_client() as redis:
            assert await redis.get("session:" + digest(old_cookie)) is None
        profile = (await browser.get("/api/v1/auth/me")).json()
        assert profile["username"] == "test-admin"
        assert "imports:read" in profile["permissions"]
        assert (await browser.get("/admin/imports")).status_code == 200


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"X-CSRF-Token": "wrong"}, "CSRF_TOKEN"),
        ({"X-CSRF-Token": ""}, "CSRF_TOKEN"),
        ({"Origin": "https://attacker.invalid"}, "CSRF_ORIGIN"),
    ],
)
async def test_session_login_still_requires_csrf_and_origin(client, headers, code):
    result = await client.post("/api/v1/auth/login", json=ADMIN_LOGIN, headers=headers)
    assert result.status_code == 403 and result.headers["X-Error-Code"] == code


async def test_session_login_still_checks_password(client):
    result = await client.post(
        "/api/v1/auth/login", json={**ADMIN_LOGIN, "password": "incorrect-password"}
    )
    assert result.status_code == 401
    assert result.headers["X-Error-Code"] == "INVALID_CREDENTIALS"
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_revoked_session_cannot_authorize_login(client, session):
    user = await session.scalar(select(User).where(User.username == "test-admin"))
    user.auth_version += 1
    await session.commit()
    result = await client.post("/api/v1/auth/login", json=ADMIN_LOGIN)
    assert result.status_code in {401, 403}
    client.headers["X-CSRF-Token"] = (await client.get("/api/v1/auth/csrf")).json()[
        "csrf_token"
    ]
    assert (
        await client.post("/api/v1/auth/login", json=ADMIN_LOGIN)
    ).status_code == 200


async def test_registration_with_session_csrf_remains_learner_only(client):
    client.headers["X-CSRF-Token"] = (await client.get("/api/v1/auth/csrf")).json()[
        "csrf_token"
    ]
    result = await client.post("/api/v1/auth/register", json=payload())
    assert result.status_code == 201, result.text
    profile = (await client.get("/api/v1/auth/me")).json()
    assert profile["username"] == "new-learner" and profile["permissions"] == []
