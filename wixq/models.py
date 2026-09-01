"""Small value objects shared by the crawler and persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CrawlWindow:
    after: datetime | None
    before: datetime | None


@dataclass(frozen=True)
class RawPage:
    page_no: int
    path: Path
    data: dict[str, Any]

    @property
    def request(self) -> dict[str, Any]:
        return self.data["request"]

    @property
    def response(self) -> dict[str, Any]:
        return self.data["response"]

    @property
    def next_cursor(self) -> str | None:
        value = self.data.get("next_cursor")
        return str(value) if value else None


@dataclass(frozen=True)
class CrawlResult:
    output_dir: Path
    pages_fetched: int
    termination_reason: str
    stats: dict[str, int]
