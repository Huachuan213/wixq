from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from wixq.config import Settings
from wixq.client import ApiError
from wixq.crawler import GroupCrawler, group_id_from_url, parse_datetime, topic_to_post
from wixq.models import CrawlWindow
from wixq.storage import DataIntegrityError, load_raw_pages, read_json, sha256


def topic(
    topic_id: str,
    created_at: str,
    text: str = "",
    *,
    images: list[dict[str, Any]] | None = None,
    sticky: bool = False,
) -> dict[str, Any]:
    return {
        "topic_id": topic_id,
        "type": "talk",
        "sticky": sticky,
        "digested": False,
        "create_time": created_at,
        "talk": {"owner": {"name": "tester"}, "text": text, "images": images or []},
        "file": {"name": "ignored.pdf"},
    }


class FakeClient:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = list(pages)
        self.calls: list[str | None] = []

    def fetch_topics(self, group_id: str, cursor_value: str | None):
        self.calls.append(cursor_value)
        if not self.pages:
            raise AssertionError("a completed crawl must not fetch another page")
        topics = self.pages.pop(0)
        return (
            200,
            {"succeeded": True, "resp_data": {"topics": topics}},
            {
                "url": f"https://api.example/v2/groups/{group_id}/topics",
                "api_path": "/v2/groups/{group_id}/topics",
                "group_id": group_id,
                "page_size": 20,
                "cursor_param": "end_time",
                "cursor_value": cursor_value or "",
                "params": {"count": 20, **({"end_time": cursor_value} if cursor_value else {})},
            },
        )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_base="https://api.example",
        topics_path="/v2/groups/{group_id}/topics",
        page_size=20,
        cursor_param="end_time",
        session_file=tmp_path / "sessions" / "zsxq.json",
    )


def test_group_url_and_time_validation() -> None:
    assert group_id_from_url("https://wx.zsxq.com/group/123?x=1") == "123"
    assert parse_datetime("2026-09-01T10:00:00+08:00").utcoffset().total_seconds() == 28800
    with pytest.raises(ValueError):
        group_id_from_url("https://example.com/group/123")
    with pytest.raises(ValueError):
        parse_datetime("2026-09-01 10:00:00")


def test_crawl_persists_raw_pages_handles_sticky_boundary_and_is_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "result"
    image = {"large": {"url": "https://images.example/1.jpg"}}
    first = [
        topic("new", "2026-09-01T01:30:00.000+0000", "inside", images=[image]),
        topic("attachment-only", "2026-09-01T01:20:00.000+0000"),
    ]
    second = [
        topic("new", "2026-09-01T01:30:00.000+0000", "inside", images=[image]),
        topic("old-sticky", "2025-01-01T00:00:00.000+0000", "old", sticky=True),
        topic("still-inside", "2026-09-01T00:30:00.000+0000", "keep"),
    ]
    third = [topic("outside", "2026-08-31T23:00:00.000+0000", "too old")]
    client = FakeClient([first, second, third])
    window = CrawlWindow(
        datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc),
    )

    result = GroupCrawler(settings(tmp_path), client).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )

    assert result.termination_reason == "after_boundary"
    assert result.pages_fetched == 3
    assert result.stats["topics_received"] == 6
    assert result.stats["unique_topic_ids"] == 5
    assert result.stats["duplicate_topic_occurrences"] == 1
    assert result.stats["posts_written"] == 2
    assert result.stats["image_urls"] == 1
    assert (output / "posts" / "new.json").exists()
    assert (output / "posts" / "still-inside.json").exists()
    assert not (output / "posts" / "attachment-only.json").exists()
    assert not (output / "posts" / "old-sticky.json").exists()
    # Every fixture topic includes a file field. Text still wins over the attachment.
    assert read_json(output / "posts" / "new.json")["text"] == "inside"
    assert "ignored.pdf" not in (output / "topics.jsonl").read_text(encoding="utf-8")

    raw = sorted((output / "raw_pages").glob("*.json"))
    before = {path.name: (sha256(path), path.stat().st_mtime_ns) for path in raw}
    resumed = GroupCrawler(settings(tmp_path), FakeClient([])).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )
    after = {path.name: (sha256(path), path.stat().st_mtime_ns) for path in raw}
    assert resumed.stats == result.stats
    assert before == after


def test_raw_pages_rebuild_checkpoint_after_state_loss(tmp_path: Path) -> None:
    output = tmp_path / "result"
    client = FakeClient([[],])
    window = CrawlWindow(None, datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc))
    GroupCrawler(settings(tmp_path), client).crawl("https://wx.zsxq.com/group/123", output, window)
    (output / "state.json").unlink()

    result = GroupCrawler(settings(tmp_path), FakeClient([])).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )
    assert result.termination_reason == "empty_page"
    assert read_json(output / "state.json")["last_completed_page"] == 1


def test_raw_pages_rebuild_missing_or_corrupt_derived_posts(tmp_path: Path) -> None:
    output = tmp_path / "result"
    window = CrawlWindow(None, datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc))
    GroupCrawler(settings(tmp_path), FakeClient([
        [topic("rebuild-me", "2026-09-01T01:00:00.000+0000", "body")],
        [],
    ])).crawl("https://wx.zsxq.com/group/123", output, window)

    post = output / "posts" / "rebuild-me.json"
    post.unlink()
    GroupCrawler(settings(tmp_path), FakeClient([])).crawl("https://wx.zsxq.com/group/123", output, window)
    assert read_json(post)["text"] == "body"

    post.write_text("{not-json", encoding="utf-8")
    GroupCrawler(settings(tmp_path), FakeClient([])).crawl("https://wx.zsxq.com/group/123", output, window)
    assert read_json(post)["topic_id"] == "rebuild-me"


def test_raw_pages_win_when_state_claims_unwritten_pages(tmp_path: Path) -> None:
    output = tmp_path / "result"
    window = CrawlWindow(None, datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc))
    GroupCrawler(settings(tmp_path), FakeClient([[]])).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )
    state = read_json(output / "state.json")
    state["last_completed_page"] = 99
    state["next_cursor"] = "imaginary-page"
    (output / "state.json").write_text(__import__("json").dumps(state), encoding="utf-8")

    result = GroupCrawler(settings(tmp_path), FakeClient([])).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )
    assert result.pages_fetched == 1
    assert read_json(output / "state.json")["last_completed_page"] == 1


def test_question_answer_and_attachment_text_are_content_not_attachments() -> None:
    qa = {
        "topic_id": "qa-1",
        "type": "question",
        "create_time": "2026-09-01T01:00:00.000+0000",
        "file": {"name": "ignored.mp3"},
        "question": {"owner": {"name": "asker"}, "text": "question text", "images": []},
        "answer": {
            "text": "answer text",
            "images": [{"original": {"url": "https://images.example/answer.jpg"}}],
        },
    }
    post = topic_to_post(qa, "123")
    assert post is not None
    assert "question text" in post["text"] and "answer text" in post["text"]
    assert post["image_urls"] == ["https://images.example/answer.jpg"]


def test_first_page_invalid_time_cursor_falls_back_to_latest_page(tmp_path: Path) -> None:
    class CursorFallbackClient(FakeClient):
        def fetch_topics(self, group_id: str, cursor_value: str | None):
            self.calls.append(cursor_value)
            if len(self.calls) == 1:
                raise ApiError("invalid initial cursor", code=1059)
            return super().fetch_topics(group_id, cursor_value)

    output = tmp_path / "result"
    window = CrawlWindow(None, datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc))
    client = CursorFallbackClient([[]])
    result = GroupCrawler(settings(tmp_path), client).crawl(
        "https://wx.zsxq.com/group/123", output, window
    )
    assert result.termination_reason == "empty_page"
    assert client.calls[0] == "2026-09-01T02:00:00.000+0000"
    assert client.calls[1] is None


def test_cursor_chain_conflict_stops_recovery(tmp_path: Path) -> None:
    output = tmp_path / "result"
    raw = output / "raw_pages"
    raw.mkdir(parents=True)
    base = {
        "schema_version": 1,
        "response_status": 200,
        "fetched_at": "2026-09-01T00:00:00+00:00",
        "response": {"succeeded": True, "resp_data": {"topics": []}},
    }
    first = {
        **base,
        "page_no": 1,
        "next_cursor": "cursor-a",
        "request": {"group_id": "123", "api_path": "/v2/groups/{group_id}/topics", "page_size": 20, "cursor_param": "end_time", "cursor_value": ""},
    }
    second = {
        **base,
        "page_no": 2,
        "next_cursor": None,
        "request": {"group_id": "123", "api_path": "/v2/groups/{group_id}/topics", "page_size": 20, "cursor_param": "end_time", "cursor_value": "wrong"},
    }
    import json

    (raw / "page_000001.json").write_text(json.dumps(first), encoding="utf-8")
    (raw / "page_000002.json").write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="cursor chain conflict"):
        load_raw_pages(output, "123")
