import pytest
from pydantic import ValidationError
from starlette.responses import Response
from app.config import Settings
from app.core import security


def test_production_http_requires_opt_in():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", public_origin="http://159.75.2.196:8080", allow_insecure_http=False)


@pytest.mark.parametrize("origin,opt_in,secure,name", [
    ("http://159.75.2.196:8080", True, False, "island-session"),
    ("https://example.com", False, True, "__Host-island-session"),
    ("https://example.com", True, True, "__Host-island-session"),
])
def test_production_cookie_transport(monkeypatch, origin, opt_in, secure, name):
    settings = Settings(_env_file=None, app_env="production", public_origin=origin, allow_insecure_http=opt_in)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    response = Response()
    security.cookie(response, settings.session_cookie, "example", 600)
    header = response.headers["set-cookie"]
    assert settings.session_cookie == name
    assert ("; Secure" in header) is secure
    assert "HttpOnly" in header and "SameSite=lax" in header


def test_opt_in_does_not_allow_other_schemes():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", public_origin="ftp://example.com", allow_insecure_http=True)
