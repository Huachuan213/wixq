"""Runtime configuration for the single ZSXQ collection path."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_API_BASE = "https://api.zsxq.com"
DEFAULT_TOPICS_PATH = "/v2/groups/{group_id}/topics"


def _positive_int(value: str, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _positive_float(value: str, *, default: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(parsed, maximum))


@dataclass(frozen=True)
class Settings:
    api_base: str
    topics_path: str
    page_size: int
    cursor_param: str
    session_file: Path
    api_version: str = "2.37.0"
    request_timeout_seconds: int = 25
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.5

    @property
    def normalized_api_base(self) -> str:
        return self.api_base.rstrip("/")

    def topics_url(self, group_id: str) -> str:
        return self.normalized_api_base + self.topics_path.format(group_id=group_id)

    @classmethod
    def from_environment(cls, session_file: str | Path | None = None) -> "Settings":
        selected_session = session_file or os.getenv("WIXQ_SESSION_FILE", "sessions/zsxq.json")
        return cls(
            api_base=os.getenv("WIXQ_API_BASE", DEFAULT_API_BASE),
            topics_path=os.getenv("WIXQ_TOPICS_API_PATH", DEFAULT_TOPICS_PATH),
            page_size=_positive_int(
                os.getenv("WIXQ_TOPICS_PAGE_SIZE", "20"), default=20, maximum=20
            ),
            cursor_param=os.getenv("WIXQ_TOPICS_CURSOR_PARAM", "end_time"),
            session_file=Path(selected_session),
            api_version=os.getenv("WIXQ_API_VERSION", "2.37.0").strip() or "2.37.0",
            retry_attempts=_positive_int(
                os.getenv("WIXQ_RETRY_ATTEMPTS", "3"), default=3, maximum=6
            ),
            retry_backoff_seconds=_positive_float(
                os.getenv("WIXQ_RETRY_BACKOFF_SECONDS", "0.5"), default=0.5, maximum=10.0
            ),
        )
