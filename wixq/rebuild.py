"""Build convenient views from the durable per-topic JSON records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wixq.storage import atomic_text, list_posts


def _ordered_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(posts, key=lambda item: (str(item.get("created_at") or ""), str(item["topic_id"])), reverse=True)


def _markdown(post: dict[str, Any]) -> str:
    lines = [f"# {post['topic_id']}", ""]
    for label, key in (("发布时间", "created_at"), ("作者", "author"), ("原帖", "source_url")):
        value = post.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if post.get("text"):
        lines.extend(["", str(post["text"])])
    for image_url in post.get("image_urls", []):
        lines.extend(["", f"![]({image_url})"])
    return "\n".join(lines).rstrip() + "\n"


def rebuild_outputs(output_dir: str | Path, group_id: str | None = None) -> int:
    target = Path(output_dir)
    posts = _ordered_posts(list_posts(target, group_id))
    jsonl = "".join(json.dumps(post, ensure_ascii=False, sort_keys=True) + "\n" for post in posts)
    atomic_text(target / "topics.jsonl", jsonl)
    markdown_dir = target / "markdown"
    for post in posts:
        atomic_text(markdown_dir / f"{post['topic_id']}.md", _markdown(post))
    return len(posts)
