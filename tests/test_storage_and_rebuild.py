from __future__ import annotations

import json

import pytest

from wixq.rebuild import rebuild_outputs
from wixq.storage import DataIntegrityError, load_raw_pages, save_post, save_raw_page_once


def test_raw_page_is_immutable(tmp_path) -> None:
    record = {
        "schema_version": 1,
        "page_no": 1,
        "request": {"group_id": "1", "api_path": "/v2/groups/{group_id}/topics", "page_size": 20, "cursor_param": "end_time", "cursor_value": ""},
        "response_status": 200,
        "fetched_at": "2026-09-01T00:00:00+00:00",
        "response": {"succeeded": True, "resp_data": {"topics": []}},
        "next_cursor": None,
    }
    save_raw_page_once(tmp_path, 1, record)
    save_raw_page_once(tmp_path, 1, record)
    changed = dict(record)
    changed["response_status"] = 201
    with pytest.raises(DataIntegrityError, match="raw page conflict"):
        save_raw_page_once(tmp_path, 1, changed)


def test_rebuild_uses_posts_as_derived_source(tmp_path) -> None:
    post = {
        "schema_version": 1,
        "topic_id": "42",
        "group_id": "1",
        "created_at": "2026-09-01T01:00:00.000+0000",
        "author": "tester",
        "text": "hello",
        "image_urls": ["https://images.example/42.jpg"],
        "source_url": "https://wx.zsxq.com/group/1/topic/42",
        "topic_type": "talk",
        "sticky": False,
        "digested": False,
    }
    assert save_post(tmp_path, post)
    assert not save_post(tmp_path, post)
    assert rebuild_outputs(tmp_path, "1") == 1
    line = (tmp_path / "topics.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line)["topic_id"] == "42"
    markdown = (tmp_path / "markdown" / "42.md").read_text(encoding="utf-8")
    assert "hello" in markdown and "https://images.example/42.jpg" in markdown


def test_legacy_raw_page_without_schema_or_page_field_is_readable(tmp_path) -> None:
    raw = tmp_path / "raw_pages"
    raw.mkdir()
    legacy = {
        "request": {
            "group_id": "1",
            "api_path": "/v2/groups/{group_id}/topics",
            "page_size": 20,
            "cursor_param": "end_time",
            "cursor_value": "",
        },
        "response_status": 200,
        "response": {"succeeded": True, "resp_data": {"topics": []}},
        "next_cursor": None,
    }
    (raw / "page_000001.json").write_text(json.dumps(legacy), encoding="utf-8")
    pages = load_raw_pages(tmp_path, "1")
    assert len(pages) == 1 and pages[0].page_no == 1
