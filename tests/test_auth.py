import base64

import pytest

from icloud_mcp import auth
from icloud_mcp.config import config


def test_env_fallback(monkeypatch):
    monkeypatch.setattr(auth, "_request_headers", lambda: {})
    assert auth.get_credentials() == ("tester@icloud.com", "aaaa-bbbb-cccc-dddd")


def test_headers_win_over_env(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_request_headers",
        lambda: {"x-apple-email": "h@icloud.com", "x-apple-app-specific-password": "pppp qqqq"},
    )
    assert auth.get_credentials() == ("h@icloud.com", "ppppqqqq")


def test_basic_auth(monkeypatch):
    token = base64.b64encode(b"b@icloud.com:secret-pass").decode()
    monkeypatch.setattr(auth, "_request_headers", lambda: {"authorization": f"Basic {token}"})
    assert auth.get_credentials() == ("b@icloud.com", "secret-pass")


def test_missing_credentials(monkeypatch):
    monkeypatch.setattr(auth, "_request_headers", lambda: {})
    monkeypatch.setattr(config, "FALLBACK_EMAIL", None)
    monkeypatch.setattr(config, "FALLBACK_PASSWORD", None)
    with pytest.raises(auth.AuthenticationError):
        auth.get_credentials()
