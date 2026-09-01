"""Interactive, operator-controlled login helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wixq.storage import atomic_json


LOGIN_URL = "https://wx.zsxq.com/"


def save_session(path: Path, cookies: list[dict[str, Any]]) -> None:
    """Persist browser cookies locally. Callers must never print their values."""
    atomic_json(
        path,
        {
            "schema_version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cookies": cookies,
        },
    )


def login_interactively(session_file: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Browser login needs `pip install -e .[browser]`.") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        input("Complete login in the browser, then press Enter here to save the session. ")
        save_session(session_file, context.cookies())
        browser.close()


def login_from_cdp(session_file: Path, endpoint: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("CDP login needs `pip install -e .[browser]`.") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("the Chrome debugging endpoint has no browser context")
        save_session(session_file, contexts[0].cookies([LOGIN_URL]))
        browser.close()
