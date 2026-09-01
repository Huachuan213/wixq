from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from wixq.client import AuthenticationError, ZsxqClient
from wixq.config import Settings


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload


class SequenceSession:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.headers: dict[str, str] = {}
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_base="https://api.example",
        topics_path="/v2/groups/{group_id}/topics",
        page_size=20,
        cursor_param="end_time",
        session_file=tmp_path / "session.json",
        retry_attempts=3,
        retry_backoff_seconds=0.25,
    )


def success() -> FakeResponse:
    return FakeResponse(200, {"succeeded": True, "resp_data": {"topics": []}})


def test_client_retries_connection_reset_then_succeeds(tmp_path: Path) -> None:
    sleep_calls: list[float] = []
    session = SequenceSession([requests.ConnectionError("reset"), success()])
    client = ZsxqClient(settings(tmp_path), session=session, sleep=sleep_calls.append)

    status, payload, request = client.fetch_topics("123", "cursor-a")
    assert status == 200 and payload["succeeded"]
    assert request["cursor_value"] == "cursor-a"
    assert session.calls == 2
    assert sleep_calls == [0.25]


@pytest.mark.parametrize("status", [429, 503])
def test_client_retries_transient_http_statuses(tmp_path: Path, status: int) -> None:
    sleep_calls: list[float] = []
    headers = {"Retry-After": "1"} if status == 429 else {}
    session = SequenceSession([FakeResponse(status, {"succeeded": False}, headers), success()])
    client = ZsxqClient(settings(tmp_path), session=session, sleep=sleep_calls.append)

    assert client.fetch_topics("123", None)[0] == 200
    assert session.calls == 2
    assert sleep_calls == ([1.0] if status == 429 else [0.25])


def test_client_does_not_retry_expired_session(tmp_path: Path) -> None:
    sleep_calls: list[float] = []
    session = SequenceSession([FakeResponse(401, {"succeeded": False})])
    client = ZsxqClient(settings(tmp_path), session=session, sleep=sleep_calls.append)

    with pytest.raises(AuthenticationError):
        client.fetch_topics("123", None)
    assert session.calls == 1
    assert sleep_calls == []


def test_client_retries_known_transient_api_failure(tmp_path: Path) -> None:
    sleep_calls: list[float] = []
    session = SequenceSession([
        FakeResponse(200, {"succeeded": False, "msg": "内部错误"}),
        success(),
    ])
    client = ZsxqClient(settings(tmp_path), session=session, sleep=sleep_calls.append)
    assert client.fetch_topics("123", None)[0] == 200
    assert session.calls == 2
    assert sleep_calls == [0.25]


def test_session_keeps_non_secret_api_header_hints(tmp_path: Path) -> None:
    config = settings(tmp_path)
    config.session_file.write_text(
        json.dumps(
            {
                "cookies": [{"name": "zsxq_access_token", "value": "fixture", "domain": ".zsxq.com"}],
                "headers": {"X-AdUid": "fixture-device", "X-Version": "2.38.0"},
            }
        ),
        encoding="utf-8",
    )
    client = ZsxqClient.from_session_file(config)
    assert client.session.headers["X-Aduid"] == "fixture-device"
    assert client.session.headers["X-Version"] == "2.38.0"
