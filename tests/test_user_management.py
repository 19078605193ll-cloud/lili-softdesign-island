import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.security import passwords
from app.core.user_management import ensure_can_disable
from app.models import AuditEvent, Role, User, UserRole
from tests.test_h5 import answer, setup_questions
from tests.test_markdown_integration import storage as storage  # noqa: PLC0414
from tests.test_registration import PASSWORD, anonymous, login, payload

pytestmark = pytest.mark.integration


async def test_list_search_pagination_and_empty_learning(client, session):
    session.add_all(
        [
            User(
                username="foo_bar",
                email="special@example.com",
                password_hash="unused",
                active=False,
            ),
            User(username="fooxbar", password_hash="unused"),
        ]
    )
    await session.commit()
    result = (await client.get("/api/v1/admin/users?q=foo_bar")).json()
    assert result["total"] == 1 and result["items"][0]["username"] == "foo_bar"
    row = result["items"][0]
    assert (
        row["attempt_count"] == 0
        and row["accuracy"] is None
        and row["last_attempt_at"] is None
    )
    assert "password_hash" not in row and "auth_version" not in row
    assert (
        await client.get("/api/v1/admin/users?q=SPECIAL@EXAMPLE.COM&active=false")
    ).json()["total"] == 1
    assert (await client.get("/api/v1/admin/users?q=special&active=true")).json()[
        "total"
    ] == 0
    first = (await client.get("/api/v1/admin/users?limit=1&page=1")).json()
    second = (await client.get("/api/v1/admin/users?limit=1&page=2")).json()
    assert first["total"] == 3 and first["items"][0]["id"] != second["items"][0]["id"]
    assert (await client.get("/api/v1/admin/users?page=0")).status_code == 422
    assert (await client.get("/api/v1/admin/users?limit=101")).status_code == 422


async def test_statistics_include_repeated_formal_parts(client, session, storage):
    _, _, units = await setup_questions(client, session)
    await answer(client, units[0], ["A", "C"])
    await answer(client, units[0], ["A", "B"])
    data = (await client.get("/api/v1/admin/users?q=test-admin")).json()["items"][0]
    assert data["attempt_count"] == 2 and data["accuracy"] == 75.0
    assert data["last_attempt_at"] and data["last_login_at"]


async def test_status_revokes_all_sessions_and_records_audit(client, session):
    async with anonymous() as browser, anonymous() as second:
        result = await browser.post("/api/v1/auth/register", json=payload())
        browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        assert (await login(second, "learner@example.com")).status_code == 200
        profile = (await browser.get("/api/v1/auth/me")).json()
        uid = profile["id"]
        url = f"/api/v1/admin/users/{uid}/status"
        assert (await client.patch(url, json={"active": False})).status_code == 200
        user = await session.get(User, uuid.UUID(uid))
        assert user.active is False and user.auth_version == 2
        assert (await client.patch(url, json={"active": False})).status_code == 200
        await session.refresh(user)
        assert user.auth_version == 2
        assert (await browser.get("/api/v1/auth/me")).status_code == 401
        assert (await second.get("/api/v1/auth/me")).status_code == 401
        # Expired cookies cannot satisfy pre-login CSRF; refresh retrieves a new token.
        assert (await login(browser, "learner@example.com")).status_code == 401
        assert (await client.patch(url, json={"active": True})).status_code == 200
        assert (await second.get("/api/v1/auth/me")).status_code == 401
        assert (await login(browser, "new-learner")).status_code == 200
        audit = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "PATCH:user_status")
            )
        )
        assert len(audit) == 2
        assert all(
            a.summary["user_id"] == uid and PASSWORD not in str(a.summary)
            for a in audit
        )


async def test_self_last_admin_and_invalid_status(client, session):
    profile = (await client.get("/api/v1/auth/me")).json()
    url = f"/api/v1/admin/users/{profile['id']}/status"
    assert (await client.patch(url, json={"active": True})).status_code == 200
    result = await client.patch(url, json={"active": False})
    assert (
        result.status_code == 409
        and result.headers["X-Error-Code"] == "CANNOT_DISABLE_SELF"
    )
    admin = await session.get(User, uuid.UUID(profile["id"]))
    with pytest.raises(HTTPException) as error:
        await ensure_can_disable(session, admin)
    assert error.value.headers["X-Error-Code"] == "LAST_ADMINISTRATOR"
    assert (await client.patch(url, json={"active": "false"})).status_code == 422
    assert (
        await client.patch(url, json={"active": True, "roles": ["administrator"]})
    ).status_code == 422
    assert (
        await client.patch(url, json={"active": False}, headers={"X-CSRF-Token": "bad"})
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/admin/users/{uuid.uuid4()}/status", json={"active": False}
        )
    ).status_code == 404


@pytest.mark.parametrize("role", ["editor", "reviewer", "publisher"])
async def test_other_roles_cannot_manage_users(client, session, role):
    user = User(username="restricted", password_hash=passwords.hash(PASSWORD))
    session.add_all([user, Role(name=role)])
    await session.flush()
    session.add(UserRole(user_id=user.id, role=role))
    await session.commit()
    async with anonymous() as browser:
        assert (await login(browser, "restricted")).status_code == 200
        assert (await browser.get("/api/v1/admin/users")).status_code == 403
        assert (await browser.get("/admin/users")).status_code == 403
        assert (
            await browser.patch(
                f"/api/v1/admin/users/{user.id}/status", json={"active": False}
            )
        ).status_code == 403
