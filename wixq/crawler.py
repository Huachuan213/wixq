"""Single-platform, resumable ZSXQ group crawler."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from wixq.client import ApiError, ZsxqClient
from wixq.config import Settings
from wixq.models import CrawlResult, CrawlWindow, RawPage
from wixq.rebuild import rebuild_outputs
from wixq.storage import (
    DataIntegrityError,
    load_raw_pages,
    load_state,
    post_path,
    read_json,
    recompute_stats,
    save_failures,
    save_post,
    save_raw_page_once,
    save_state,
    utc_now,
    valid_post,
)


GROUP_PATH = re.compile(r"^/group/(\d+)(?:/|$)")


def group_id_from_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith("zsxq.com"):
        raise ValueError("expected a https://wx.zsxq.com/group/<group_id> URL")
    match = GROUP_PATH.match(parsed.path)
    if not match:
        raise ValueError("the URL does not contain a ZSXQ group id")
    return match.group(1)


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone offset")
    return result


def _topic_time(topic: dict[str, Any]) -> tuple[datetime, str] | None:
    value = topic.get("create_time")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_datetime(value), value
    except ValueError:
        return None


def _api_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _topics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("resp_data", {})
    candidate = data.get("topics", []) if isinstance(data, dict) else []
    if not isinstance(candidate, list):
        raise DataIntegrityError("API response has a non-list topics value")
    return [topic for topic in candidate if isinstance(topic, dict)]


def _next_cursor(topics: Iterable[dict[str, Any]]) -> str | None:
    """Use the oldest ordinary topic, avoiding a stale pinned item as a cursor."""
    ordinary = [item for item in topics if not item.get("sticky") and _topic_time(item)]
    candidates = ordinary or [item for item in topics if _topic_time(item)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: _topic_time(item)[0])["create_time"]


def _image_urls(talk: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    images = talk.get("images", [])
    if not isinstance(images, list):
        return urls
    for image in images:
        if not isinstance(image, dict):
            continue
        selected: str | None = None
        for size in ("original", "large", "thumbnail"):
            candidate = image.get(size)
            if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                selected = candidate["url"].strip()
                break
        if selected and selected not in urls:
            urls.append(selected)
    return urls


def _content_from_topic(topic: dict[str, Any]) -> tuple[str, list[str], str]:
    """Collect text/images from the normal talk and question-answer shapes."""
    text_parts: list[str] = []
    images: list[str] = []
    author = ""
    for key in ("talk", "question", "answer"):
        container = topic.get(key)
        if not isinstance(container, dict):
            continue
        text = container.get("text")
        if not isinstance(text, str) or not text.strip():
            text = container.get("title") if isinstance(container.get("title"), str) else ""
        if text and text.strip() and text.strip() not in text_parts:
            text_parts.append(text.strip())
        for image_url in _image_urls(container):
            if image_url not in images:
                images.append(image_url)
        owner = container.get("owner")
        if not author and isinstance(owner, dict):
            author = str(
                owner.get("name")
                or owner.get("nickname")
                or owner.get("user_id")
                or owner.get("id")
                or ""
            )
    return "\n\n".join(text_parts), images, author


def topic_to_post(topic: dict[str, Any], group_id: str) -> dict[str, Any] | None:
    """Create a small derived view without retaining attachments or comments."""
    topic_id = str(topic.get("topic_id") or "").strip()
    if not topic_id:
        return None
    text, images, author = _content_from_topic(topic)
    if not text and not images:
        return None
    return {
        "schema_version": 1,
        "topic_id": topic_id,
        "group_id": str(group_id),
        "created_at": str(topic.get("create_time") or ""),
        "author": author,
        "text": text,
        "image_urls": images,
        "source_url": f"https://wx.zsxq.com/group/{group_id}/topic/{topic_id}",
        "topic_type": str(topic.get("type") or ""),
        "sticky": bool(topic.get("sticky")),
        "digested": bool(topic.get("digested")),
    }


def _in_window(topic: dict[str, Any], window: CrawlWindow) -> bool:
    parsed = _topic_time(topic)
    if parsed is None:
        return False
    value = parsed[0]
    return (window.after is None or value >= window.after) and (
        window.before is None or value < window.before
    )


def _all_ordinary_topics_before(topics: list[dict[str, Any]], after: datetime | None) -> bool:
    if after is None:
        return False
    ordinary_times = [_topic_time(item)[0] for item in topics if not item.get("sticky") and _topic_time(item)]
    return bool(ordinary_times) and all(value < after for value in ordinary_times)


def _record_is_terminal(page: RawPage, after: datetime | None) -> str | None:
    topics = _topics(page.response)
    if not topics:
        return "empty_page"
    if not page.next_cursor:
        return "missing_next_cursor"
    if page.next_cursor == str(page.request.get("cursor_value") or ""):
        return "cursor_stalled"
    if _all_ordinary_topics_before(topics, after):
        return "after_boundary"
    return None


def _load_failures(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / "failed_topics.json"
    if not path.exists():
        return []
    value = read_json(path)
    if not isinstance(value, list):
        raise DataIntegrityError(f"failed topic list is invalid: {path}")
    return [entry for entry in value if isinstance(entry, dict)]


class GroupCrawler:
    def __init__(self, settings: Settings, client: ZsxqClient) -> None:
        self.settings = settings
        self.client = client

    def _materialize_page(
        self,
        output_dir: Path,
        page: RawPage,
        group_id: str,
        window: CrawlWindow,
        failures: list[dict[str, Any]],
    ) -> None:
        for topic in _topics(page.response):
            topic_id = str(topic.get("topic_id") or "")
            try:
                if not _in_window(topic, window):
                    continue
                post = topic_to_post(topic, group_id)
                if post is not None:
                    save_post(output_dir, post)
            except Exception as exc:  # one malformed topic must not discard its page
                failures.append(
                    {
                        "topic_id": topic_id,
                        "raw_page_no": page.page_no,
                        "error": type(exc).__name__,
                    }
                )

    def _validate_state_window(self, output_dir: Path, group_id: str, window: CrawlWindow) -> None:
        state = load_state(output_dir)
        if not state:
            return
        if str(state.get("group_id") or group_id) != str(group_id):
            raise DataIntegrityError("state belongs to another group")
        saved_window = state.get("window", {})
        expected = {"after": _api_timestamp(window.after), "before": _api_timestamp(window.before)}
        if saved_window and saved_window != expected:
            raise DataIntegrityError("output directory belongs to a different time window")

    def _state_payload(
        self,
        group_id: str,
        window: CrawlWindow,
        pages: list[RawPage],
        termination: str | None,
        output_dir: Path,
    ) -> dict[str, Any]:
        return {
            "group_id": str(group_id),
            "window": {"after": _api_timestamp(window.after), "before": _api_timestamp(window.before)},
            "next_cursor": pages[-1].next_cursor if pages else _api_timestamp(window.before),
            "last_completed_page": pages[-1].page_no if pages else 0,
            "termination_reason": termination,
            "stats": recompute_stats(output_dir, group_id),
        }

    def crawl(self, group_url: str, output_dir: str | Path, window: CrawlWindow) -> CrawlResult:
        group_id = group_id_from_url(group_url)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        pages = load_raw_pages(target, group_id)
        self._validate_state_window(target, group_id, window)
        failures = _load_failures(target)

        for page in pages:
            self._materialize_page(target, page, group_id, window, failures)
        save_failures(target, failures)

        termination = _record_is_terminal(pages[-1], window.after) if pages else None
        cursor = pages[-1].next_cursor if pages else _api_timestamp(window.before)
        page_no = pages[-1].page_no + 1 if pages else 1
        used_initial_cursor_fallback = False

        while termination is None:
            try:
                status, payload, request = self.client.fetch_topics(group_id, cursor)
            except ApiError as exc:
                # The service can reject an end_time later than the newest available
                # topic with business code 1059. Start at the newest page instead.
                if page_no == 1 and cursor and exc.code == 1059 and not used_initial_cursor_fallback:
                    cursor = None
                    used_initial_cursor_fallback = True
                    continue
                raise
            topics = _topics(payload)
            next_cursor = _next_cursor(topics)
            record = {
                "schema_version": 1,
                "page_no": page_no,
                "request": request,
                "response_status": status,
                "fetched_at": utc_now(),
                "response": payload,
                "next_cursor": next_cursor,
            }
            path = save_raw_page_once(target, page_no, record)
            current = RawPage(page_no, path, record)
            pages.append(current)
            self._materialize_page(target, current, group_id, window, failures)
            save_failures(target, failures)
            termination = _record_is_terminal(current, window.after)
            save_state(target, self._state_payload(group_id, window, pages, termination, target))
            if termination is None:
                cursor = next_cursor
                page_no += 1

        rebuild_outputs(target, group_id)
        stats = recompute_stats(target, group_id)
        save_state(target, self._state_payload(group_id, window, pages, termination, target))
        return CrawlResult(target, len(pages), termination, stats)
