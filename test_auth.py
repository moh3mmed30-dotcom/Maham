import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
import pytest
from fastapi import HTTPException
from app.auth import validate_init_data
from app.config import settings


def make_init_data(token: str, user: dict) -> str:
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_telegram_init_data(monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123:test-token")
    monkeypatch.setattr(settings, "init_data_max_age_seconds", 600)
    user = {"id": 7391716280, "first_name": "Admin"}
    assert validate_init_data(make_init_data(settings.bot_token, user)) == user


def test_invalid_telegram_init_data_rejected(monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "123:test-token")
    monkeypatch.setattr(settings, "init_data_max_age_seconds", 600)
    with pytest.raises(HTTPException) as exc:
        validate_init_data("auth_date=1&user=%7B%7D&hash=bad")
    assert exc.value.status_code == 401
