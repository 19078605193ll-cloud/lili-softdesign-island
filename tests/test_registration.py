import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.security import passwords
from app.main import app
from app.models import User, UserRole

pytestmark = pytest.mark.integration
PASSWORD = "registration-password-123"


@asynccontextmanager
async def anonymous():
    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as browser:
        csrf = (await browser.get("/api/v1/auth/csrf")).json()["csrf_token"]
        browser.headers["X-CSRF-Token"] = csrf
        yield browser


def payload(**changes):
    return dict(
        username="new-learner",
        email="learner@example.com",
        password=PASSWORD,
        confirm_password=PASSWORD,
        **changes,
    )


async def login(browser, identifier):
    browser.headers["X-CSRF-Token"] = (await browser.get("/api/v1/auth/csrf")).json()[
        "csrf_token"
    ]
    result = await browser.post(
        "/api/v1/auth/login", json={"username": identifier, "password": PASSWORD}
    )
    if result.status_code == 200:
        browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result


async def test_registration_auto_login_email_username_and_legacy(client, session):
    async with anonymous() as browser:
        data = payload()
        data.update(username="  NEW-Learner  ", email="  LEARNER@EXAMPLE.COM  ")
        result = await browser.post("/api/v1/auth/register", json=data)
        assert result.status_code == 201, result.text
        browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        profile = (await browser.get("/api/v1/auth/me")).json()
        assert (
            profile["username"] == "new-learner"
            and profile["email"] == "learner@example.com"
        )
        assert profile["permissions"] == []
        user = await session.get(User, uuid.UUID(profile["id"]))
        assert passwords.verify(PASSWORD, user.password_hash) and user.last_login_at
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UserRole)
                .where(UserRole.user_id == user.id)
            )
            == 0
        )
        for identifier in (" LEARNER@EXAMPLE.COM ", " NEW-LEARNER "):
            assert (await browser.post("/api/v1/auth/logout")).status_code == 200
            result = await login(browser, identifier)
            assert result.status_code == 200, result.text
        assert (await browser.get("/api/v1/admin/users")).status_code == 403
        assert (await browser.get("/admin/users")).status_code == 403
        prefs = (await browser.get("/api/v2/learning/preferences")).json()
        assert prefs["motto"] == "千里之行，始于足下。"
    assert (await client.get("/api/v1/auth/me")).json()["email"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"username": "  "},
        {"username": "bad\x00name"},
        {"password": "short", "confirm_password": "short"},
        {"password": "x" * 129, "confirm_password": "x" * 129},
        {"confirm_password": "different-password-123"},
        {"roles": ["administrator"]},
        {"active": False},
    ],
)
async def test_registration_validation_does_not_create_or_leak(
    client, session, changes
):
    before = await session.scalar(select(func.count()).select_from(User))
    async with anonymous() as browser:
        data = payload()
        data.update(changes)
        response = await browser.post("/api/v1/auth/register", json=data)
        assert response.status_code == 422
        assert (
            PASSWORD not in response.text
            and "different-password-123" not in response.text
        )
    assert await session.scalar(select(func.count()).select_from(User)) == before


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"username": "TEST-ADMIN"}, "USERNAME_TAKEN"),
        ({"username": "other", "email": "LEARNER@EXAMPLE.COM"}, "EMAIL_TAKEN"),
        (
            {"username": "learner@example.com", "email": "other@example.com"},
            "USERNAME_TAKEN",
        ),
    ],
)
async def test_duplicate_identifiers(client, session, changes, code):
    session.add(
        User(
            username="existing",
            email="learner@example.com",
            password_hash=passwords.hash(PASSWORD),
        )
    )
    await session.commit()
    async with anonymous() as browser:
        data = payload()
        data.update(changes)
        response = await browser.post("/api/v1/auth/register", json=data)
        assert response.status_code == 409 and response.headers["X-Error-Code"] == code


async def test_email_cannot_shadow_legacy_username_and_rename(client, session):
    session.add(
        User(username="legacy@example.com", password_hash=passwords.hash(PASSWORD))
    )
    session.add(
        User(
            username="existing",
            email="taken@example.com",
            password_hash=passwords.hash(PASSWORD),
        )
    )
    await session.commit()
    async with anonymous() as browser:
        data = payload()
        data["email"] = "legacy@example.com"
        result = await browser.post("/api/v1/auth/register", json=data)
        assert (
            result.status_code == 409
            and result.headers["X-Error-Code"] == "EMAIL_TAKEN"
        )
        assert (await login(browser, "legacy@example.com")).status_code == 200
    result = await client.patch(
        "/api/v1/auth/me", json={"username": "TAKEN@EXAMPLE.COM"}
    )
    assert result.status_code == 409


async def test_concurrent_username_email_and_cross_collisions(client, session):
    async def attempt(data):
        async with anonymous() as browser:
            return await browser.post("/api/v1/auth/register", json=data)

    first = payload()
    second = payload()
    second.update(username="different", email="learner@example.com")
    results = await asyncio.gather(attempt(first), attempt(second))
    assert sorted(r.status_code for r in results) == [201, 409]
    first = payload()
    first.update(username="cross@example.com", email="first@example.com")
    second = payload()
    second.update(username="second", email="cross@example.com")
    results = await asyncio.gather(attempt(first), attempt(second))
    assert sorted(r.status_code for r in results) == [201, 409]
    first = payload()
    first.update(username="same-name", email="third@example.com")
    second = payload()
    second.update(username="SAME-NAME", email="fourth@example.com")
    results = await asyncio.gather(attempt(first), attempt(second))
    assert sorted(r.status_code for r in results) == [201, 409]


async def test_registration_csrf_origin_rate_limit_and_maintenance(
    client, session, monkeypatch
):
    from app.config import get_settings

    async with anonymous() as browser:
        assert (
            await browser.post(
                "/api/v1/auth/register",
                json=payload(),
                headers={"X-CSRF-Token": "wrong"},
            )
        ).status_code == 403
        assert (
            await browser.post(
                "/api/v1/auth/register",
                json=payload(),
                headers={"Origin": "https://other.invalid"},
            )
        ).status_code == 403
        monkeypatch.setattr(get_settings(), "writes_enabled", False)
        assert (
            await browser.post("/api/v1/auth/register", json=payload())
        ).status_code == 503
        monkeypatch.setattr(get_settings(), "writes_enabled", True)
        data = payload()
        data["username"] = "test-admin"
        for _ in range(10):
            assert (
                await browser.post("/api/v1/auth/register", json=data)
            ).status_code == 409
        assert (
            await browser.post("/api/v1/auth/register", json=data)
        ).status_code == 429


async def test_registration_retains_account_if_auto_login_fails(
    client, session, monkeypatch
):
    from redis.exceptions import ConnectionError

    monkeypatch.setattr(
        "app.core.registration.start_session", AsyncMock(side_effect=ConnectionError())
    )
    async with anonymous() as browser:
        response = await browser.post("/api/v1/auth/register", json=payload())
        assert response.status_code == 201 and response.json()["authenticated"] is False
        assert (await browser.get("/api/v1/auth/me")).status_code == 401
        assert (await login(browser, "learner@example.com")).status_code == 200


async def test_invalid_registration_inputs_are_also_rate_limited(client, session):
    async with anonymous() as browser:
        data = payload()
        data["email"] = "invalid"
        for _ in range(10):
            assert (
                await browser.post("/api/v1/auth/register", json=data)
            ).status_code == 422
        assert (
            await browser.post("/api/v1/auth/register", json=data)
        ).status_code == 429
