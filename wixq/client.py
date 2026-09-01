"""Authenticated HTTP client for the documented group-topics endpoint."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import requests

from wixq.config import Settings


class AuthenticationError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _session_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AuthenticationError(
            f"session file not found: {path}. Run `wixq login` and sign in yourself first."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthenticationError(f"session file is unreadable: {path}") from exc
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"cookies": payload}
    raise AuthenticationError("session file does not contain a cookie list")


def _session_cookies(path: Path) -> list[dict[str, Any]]:
    payload = _session_payload(path)
    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        raise AuthenticationError("session file does not contain a cookie list")
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


class ZsxqClient:
    retry_statuses = {429, 500, 502, 503, 504}

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.sleep = sleep or time.sleep
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://wx.zsxq.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36",
                "X-Version": settings.api_version,
            }
        )

    @classmethod
    def from_session_file(cls, settings: Settings) -> "ZsxqClient":
        session = requests.Session()
        payload = _session_payload(settings.session_file)
        cookies = payload.get("cookies")
        if not isinstance(cookies, list):
            raise AuthenticationError("session file does not contain a cookie list")
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name, value = cookie.get("name"), cookie.get("value")
            if not name or value is None:
                continue
            domain = str(cookie.get("domain") or ".zsxq.com")
            path = str(cookie.get("path") or "/")
            session.cookies.set(str(name), str(value), domain=domain, path=path)
        client = cls(settings, session)
        saved_headers = payload.get("headers")
        if isinstance(saved_headers, dict):
            for source, value in saved_headers.items():
                lowered = str(source).lower()
                if lowered == "x-aduid" and value:
                    client.session.headers["X-Aduid"] = str(value)
                if lowered == "x-version" and value:
                    client.session.headers["X-Version"] = str(value)
        return client

    def _retry_delay(self, response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            try:
                if raw is not None:
                    return min(float(raw), 30.0)
            except ValueError:
                pass
        return self.settings.retry_backoff_seconds * (2**attempt)

    @staticmethod
    def _is_retryable_api_failure(payload: Any) -> bool:
        if not isinstance(payload, dict) or payload.get("succeeded") is not False:
            return False
        message = " ".join(
            str(payload.get(key) or "") for key in ("msg", "message", "error")
        ).lower()
        return any(
            marker in message
            for marker in ("内部错误", "internal error", "server error", "系统繁忙", "too many")
        )

    def fetch_topics(
        self, group_id: str, cursor_value: str | None
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        params: dict[str, Any] = {"count": self.settings.page_size}
        if cursor_value:
            params[self.settings.cursor_param] = cursor_value
        url = self.settings.topics_url(group_id)
        response: requests.Response | None = None
        request_error: requests.RequestException | None = None
        payload: dict[str, Any] | None = None
        for attempt in range(self.settings.retry_attempts):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.settings.request_timeout_seconds,
                    headers={"X-Timestamp": str(int(time.time()))},
                )
                request_error = None
            except requests.RequestException as exc:
                response = None
                request_error = exc

            retryable = request_error is not None or (
                response is not None and response.status_code in self.retry_statuses
            )
            if not retryable and response is not None and response.status_code < 400:
                try:
                    decoded = response.json()
                except ValueError:
                    decoded = None
                if self._is_retryable_api_failure(decoded):
                    retryable = True
                elif isinstance(decoded, dict):
                    payload = decoded
            if not retryable or attempt == self.settings.retry_attempts - 1:
                break
            self.sleep(self._retry_delay(response, attempt))

        if request_error is not None:
            raise ApiError(f"request failed for group {group_id}") from request_error
        if response is None:
            raise ApiError(f"request did not produce a response for group {group_id}")
        if payload is None:
            try:
                payload = response.json()
            except ValueError as exc:
                raise ApiError(f"API returned non-JSON with HTTP {response.status_code}") from exc

        if response.status_code in (401, 403):
            raise AuthenticationError("ZSXQ rejected the session. Run `wixq login` again.")
        if response.status_code >= 400:
            raise ApiError(f"API returned HTTP {response.status_code}")
        if not isinstance(payload, dict):
            raise ApiError("API response root is not an object")
        if payload.get("succeeded") is False:
            raw_code = payload.get("code")
            try:
                code = int(raw_code) if raw_code is not None else None
            except (TypeError, ValueError):
                code = None
            raise ApiError(
                str(payload.get("msg") or payload.get("error") or "API request failed"), code=code
            )

        request = {
            "url": url,
            "api_path": self.settings.topics_path,
            "group_id": str(group_id),
            "page_size": self.settings.page_size,
            "cursor_param": self.settings.cursor_param,
            "cursor_value": cursor_value or "",
            "params": params,
        }
        return response.status_code, payload, request
