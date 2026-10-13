import pytest

from scripts.verify_deployment_origin import allowed_origins, check_origins


ORIGIN = "https://app.liuli2026.top"


def test_stale_voice_environment_is_rejected_even_with_updated_compose():
    with pytest.raises(ValueError, match="recreate voice-input"):
        check_origins(ORIGIN, {"PUBLIC_ORIGIN": ORIGIN}, {"ALLOWED_ORIGINS": "http://159.75.2.196"})


def test_stale_api_environment_is_rejected():
    with pytest.raises(ValueError, match="recreate the API"):
        check_origins(ORIGIN, {"PUBLIC_ORIGIN": "http://159.75.2.196"}, {"ALLOWED_ORIGINS": ORIGIN})


@pytest.mark.parametrize("value", [ORIGIN, '["https://app.liuli2026.top"]'])
def test_exact_https_origin_is_accepted(value):
    check_origins(ORIGIN, {"PUBLIC_ORIGIN": ORIGIN, "ALLOW_INSECURE_HTTP": "false"}, {"ALLOWED_ORIGINS": value})


def test_wildcard_is_rejected():
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        check_origins(ORIGIN, {"PUBLIC_ORIGIN": ORIGIN}, {"ALLOWED_ORIGINS": "*"})


def test_https_cannot_enable_insecure_http():
    with pytest.raises(ValueError, match="ALLOW_INSECURE_HTTP"):
        check_origins(ORIGIN, {"PUBLIC_ORIGIN": ORIGIN, "ALLOW_INSECURE_HTTP": "true"}, {"ALLOWED_ORIGINS": ORIGIN})


def test_comma_separated_origins_are_parsed():
    assert allowed_origins("https://a.example, https://b.example") == ["https://a.example", "https://b.example"]
