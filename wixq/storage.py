"""Filesystem rules for immutable raw pages and recoverable derived data."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from wixq.models import RawPage


RAW_PAGE_NAME = re.compile(r"^page_(\d{6})\.json$")
STATE_SCHEMA_VERSION = 1


class DataIntegrityError(RuntimeError):
    """Raised when durable collection records disagree or cannot be read."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataIntegrityError(f"invalid JSON file: {path}") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: Path, value: Any) -> None:
    """Write a replaceable derived record without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def raw_directory(output_dir: Path) -> Path:
    return output_dir / "raw_pages"


def post_directory(output_dir: Path) -> Path:
    return output_dir / "posts"


def raw_page_path(output_dir: Path, page_no: int) -> Path:
    return raw_directory(output_dir) / f"page_{page_no:06d}.json"


def save_raw_page_once(output_dir: Path, page_no: int, record: dict[str, Any]) -> Path:
    """Create an immutable page. Existing valid content must be identical."""
    path = raw_page_path(output_dir, page_no)
    if path.exists():
        existing = read_json(path)
        if _canonical_json(existing) != _canonical_json(record):
            raise DataIntegrityError(f"raw page conflict: {path}")
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        # The target did not exist when checked. Do not overwrite if another run won.
        if path.exists():
            existing = read_json(path)
            if _canonical_json(existing) != _canonical_json(record):
                raise DataIntegrityError(f"raw page conflict: {path}")
            return path
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return path


def _required_raw_fields(data: dict[str, Any], path: Path) -> None:
    request = data.get("request")
    response = data.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise DataIntegrityError(f"raw page lacks request or response object: {path}")
    required_request = {"group_id", "api_path", "page_size", "cursor_param", "cursor_value"}
    missing = required_request.difference(request)
    if missing:
        raise DataIntegrityError(f"raw page lacks request fields {sorted(missing)}: {path}")


def load_raw_pages(output_dir: Path, group_id: str | None = None) -> list[RawPage]:
    directory = raw_directory(output_dir)
    if not directory.exists():
        return []

    pages: list[RawPage] = []
    for path in sorted(directory.glob("page_*.json")):
        match = RAW_PAGE_NAME.match(path.name)
        if not match:
            raise DataIntegrityError(f"unexpected raw page filename: {path}")
        data = read_json(path)
        if not isinstance(data, dict):
            raise DataIntegrityError(f"raw page must be an object: {path}")
        _required_raw_fields(data, path)
        page_no = int(match.group(1))
        if data.get("page_no") not in (None, page_no):
            raise DataIntegrityError(f"raw page number mismatch: {path}")
        if group_id and str(data["request"]["group_id"]) != str(group_id):
            raise DataIntegrityError(f"raw page belongs to another group: {path}")
        pages.append(RawPage(page_no, path, data))

    for expected, page in enumerate(pages, start=1):
        if page.page_no != expected:
            raise DataIntegrityError(f"raw pages are not contiguous at page {expected}")

    if pages:
        first = pages[0].request
        invariant = (first["group_id"], first["api_path"], first["page_size"], first["cursor_param"])
        for previous, current in zip(pages, pages[1:]):
            current_invariant = (
                current.request["group_id"],
                current.request["api_path"],
                current.request["page_size"],
                current.request["cursor_param"],
            )
            if current_invariant != invariant:
                raise DataIntegrityError(f"raw page settings conflict: {current.path}")
            if previous.next_cursor != str(current.request.get("cursor_value") or ""):
                raise DataIntegrityError(
                    f"cursor chain conflict between {previous.path.name} and {current.path.name}"
                )
    return pages


def post_path(output_dir: Path, topic_id: str) -> Path:
    return post_directory(output_dir) / f"{topic_id}.json"


def valid_post(path: Path, group_id: str | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = read_json(path)
    if not isinstance(data, dict):
        raise DataIntegrityError(f"post must be an object: {path}")
    topic_id = str(data.get("topic_id") or "")
    if not topic_id or path.stem != topic_id:
        raise DataIntegrityError(f"post id mismatch: {path}")
    if group_id and str(data.get("group_id") or "") != str(group_id):
        raise DataIntegrityError(f"post belongs to another group: {path}")
    if not isinstance(data.get("text", ""), str) or not isinstance(data.get("image_urls", []), list):
        raise DataIntegrityError(f"post fields are invalid: {path}")
    return data


def save_post(output_dir: Path, post: dict[str, Any]) -> bool:
    topic_id = str(post["topic_id"])
    path = post_path(output_dir, topic_id)
    try:
        existing = valid_post(path, str(post["group_id"]))
    except DataIntegrityError:
        # posts are a rebuildable view; raw_pages remain the immutable authority.
        atomic_json(path, post)
        return True
    if existing is not None:
        if _canonical_json(existing) != _canonical_json(post):
            raise DataIntegrityError(f"post conflict: {path}")
        return False
    atomic_json(path, post)
    return True


def list_posts(output_dir: Path, group_id: str | None = None) -> list[dict[str, Any]]:
    directory = post_directory(output_dir)
    if not directory.exists():
        return []
    posts = [valid_post(path, group_id) for path in sorted(directory.glob("*.json"))]
    return [item for item in posts if item is not None]


def load_state(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "state.json"
    return read_json(path) if path.exists() else None


def save_state(output_dir: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = utc_now()
    atomic_json(output_dir / "state.json", state)


def save_failures(output_dir: Path, failures: Iterable[dict[str, Any]]) -> None:
    atomic_json(output_dir / "failed_topics.json", list(failures))


def recompute_stats(output_dir: Path, group_id: str | None = None) -> dict[str, int]:
    pages = load_raw_pages(output_dir, group_id)
    received = 0
    identifiers: list[str] = []
    missing_identifier = 0
    image_urls = 0
    image_posts = 0

    for page in pages:
        payload = page.response.get("resp_data", {})
        topics = payload.get("topics", []) if isinstance(payload, dict) else []
        if not isinstance(topics, list):
            raise DataIntegrityError(f"topics must be a list: {page.path}")
        received += len(topics)
        for topic in topics:
            if not isinstance(topic, dict) or not topic.get("topic_id"):
                missing_identifier += 1
            else:
                identifiers.append(str(topic["topic_id"]))

    posts = list_posts(output_dir, group_id)
    for post in posts:
        count = len(post.get("image_urls", []))
        image_urls += count
        image_posts += int(count > 0)

    failures = output_dir / "failed_topics.json"
    failed_count = len(read_json(failures)) if failures.exists() else 0
    counts = Counter(identifiers)
    return {
        "raw_pages": len(pages),
        "topics_received": received,
        "topics_with_valid_id": len(identifiers),
        "unique_topic_ids": len(counts),
        "duplicate_topic_occurrences": sum(value - 1 for value in counts.values()),
        "topics_without_topic_id": missing_identifier,
        "posts_written": len(posts),
        "image_posts": image_posts,
        "image_urls": image_urls,
        "failed_topics": failed_count,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
