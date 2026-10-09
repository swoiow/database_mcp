"""Core unit tests: admin API auth + password masking (P0-1)."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gateway_api as api
from gateway_config import ConnectionMeta


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    yield "s3cret"
    # restored by monkeypatch


def test_missing_token_401(admin_token):
    with pytest.raises(HTTPException) as e:
        run(api.require_admin(FakeRequest({})))
    assert e.value.status_code == 401


def test_wrong_token_401(admin_token):
    with pytest.raises(HTTPException):
        run(api.require_admin(FakeRequest({"authorization": "Bearer nope"})))


def test_correct_token_passes(admin_token):
    run(api.require_admin(FakeRequest({"authorization": "Bearer s3cret"})))  # no raise


def test_unset_token_keeps_backcompat(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    run(api.require_admin(FakeRequest({})))  # no raise, warning logged at startup


def test_password_masked_in_responses():
    c = ConnectionMeta(alias="c", db_type="mysql", host="h", user="u",
                       password="real-secret", db_name="d")
    pub = api._public_conn(c)
    assert pub.password == "***"
    assert pub.model_dump()["password"] == "***"
    assert pub.host == "h"  # other fields intact
