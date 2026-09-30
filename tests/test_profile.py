import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import select
from app.models import User
from app.config import get_settings

pytestmark = pytest.mark.integration


def picture():
    stream = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(stream, "PNG")
    return stream.getvalue()


async def test_rename_login_and_validation(client, session):
    before = (await client.get("/api/v1/auth/me")).json()
    response = await client.patch("/api/v1/auth/me", json={"username": "  New-Name  "})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "new-name"
    assert (
        response.json()["id"] == before["id"]
        and response.json()["permissions"] == before["permissions"]
    )
    assert (await client.get("/api/v1/auth/me")).json()["username"] == "new-name"
    session.add(User(username="taken", password_hash="unused"))
    await session.commit()
    assert (
        await client.patch("/api/v1/auth/me", json={"username": "Taken"})
    ).status_code == 409
    for value in [" ", "a" * 101, "bad\x00name"]:
        assert (
            await client.patch("/api/v1/auth/me", json={"username": value})
        ).status_code == 422
    assert (
        await client.patch(
            "/api/v1/auth/me", json={"username": "ok", "id": str(uuid.uuid4())}
        )
    ).status_code == 422
    assert (
        await client.patch(
            "/api/v1/auth/me", json={"username": "ok"}, headers={"X-CSRF-Token": "bad"}
        )
    ).status_code == 403
    await client.post("/api/v1/auth/logout")
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "test-admin", "password": "isolated-test-password"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "new-name", "password": "isolated-test-password"},
        )
    ).status_code == 200


async def test_avatar_validation_persistence_and_cleanup(
    client, session, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "import_storage_root", str(tmp_path))

    async def upload(data=None, mime="image/png", crop=None):
        return await client.put(
            "/api/v1/auth/me/avatar",
            files={
                "file": ("photo.png", data if data is not None else picture(), mime)
            },
            data=crop or {"x": "100", "y": "0", "size": "600"},
        )

    response = await upload()
    assert response.status_code == 200, response.text
    url = response.json()["avatar_url"]
    assert (await client.get("/api/v1/auth/me")).json()["avatar_url"] == url
    image = await client.get(url)
    assert image.status_code == 200
    decoded = Image.open(io.BytesIO(image.content))
    assert (
        decoded.format == "WEBP"
        and decoded.size == (512, 512)
        and not decoded.getexif()
    )
    for data, mime, crop in [
        (b"bad", "image/png", None),
        (picture(), "image/jpeg", None),
        (b"x" * (5 * 1024 * 1024 + 1), "image/png", None),
        (picture(), "image/png", {"x": -1, "y": 0, "size": 600}),
    ]:
        assert (await upload(data, mime, crop)).status_code in (413, 422)
        assert (await client.get("/api/v1/auth/me")).json()["avatar_url"] == url
    from app.core.profile import avatar_path

    old_path = avatar_path(url.rsplit("/", 1)[1])
    with patch.object(Path, "mkdir", side_effect=OSError("disk")):
        assert (await upload()).status_code == 503
    assert old_path.exists()
    from sqlalchemy.exc import SQLAlchemyError
    from unittest.mock import AsyncMock
    with patch.object(session, 'commit', new=AsyncMock(side_effect=SQLAlchemyError('write failed'))):
        assert (await upload()).status_code == 503
    assert old_path.exists() and len(list((tmp_path/'avatars').glob('*.webp'))) == 1
    replacement = await upload()
    assert replacement.status_code == 200 and replacement.json()["avatar_url"] != url
    assert not old_path.exists() and (await client.get(url)).status_code == 404
    assert (await client.delete("/api/v1/auth/me/avatar")).json()["avatar_url"] is None
    assert not list((tmp_path / "avatars").glob("*.webp"))


def test_rotated_image_and_animation_validation():
    from app.core.profile import render_avatar
    from fastapi import HTTPException
    image=Image.new('RGB',(100,200),'red')
    exif=Image.Exif();exif[274]=6
    buf=io.BytesIO();image.save(buf,'JPEG',exif=exif)
    # After orientation correction width is 200 and height is 100.
    result=render_avatar(buf.getvalue(),'image/jpeg',100,0,100)
    assert Image.open(io.BytesIO(result)).size==(512,512)
    buf=io.BytesIO();image.save(buf,'PNG',save_all=True,append_images=[Image.new('RGB',(100,200),'blue')],duration=100,loop=0)
    with pytest.raises(HTTPException):render_avatar(buf.getvalue(),'image/png',0,0,100)


async def test_avatar_is_private(client,session,tmp_path,monkeypatch):
    from app.core.profile import avatar_path
    monkeypatch.setattr(get_settings(),'import_storage_root',str(tmp_path))
    key=str(uuid.uuid4())
    session.add(User(username='other-avatar',password_hash='unused',avatar_key=key))
    await session.commit()
    path=avatar_path(key);path.parent.mkdir();path.write_bytes(picture())
    assert (await client.get('/api/v1/auth/me/avatar/'+key)).status_code==404
