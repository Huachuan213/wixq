"""Command line interface for Wixq."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from wixq.auth import login_from_cdp, login_interactively
from wixq.client import ZsxqClient
from wixq.config import Settings
from wixq.crawler import GroupCrawler, parse_datetime
from wixq.models import CrawlWindow
from wixq.rebuild import rebuild_outputs
from wixq.storage import DataIntegrityError, recompute_stats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wixq", description="Read text and image URLs from authorised ZSXQ groups.")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="save a locally authenticated ZSXQ browser session")
    login.add_argument("--session", type=Path, help="local session file path")
    login.add_argument("--cdp", metavar="ENDPOINT", help="reuse a Chrome remote-debugging endpoint")

    crawl = commands.add_parser("crawl", help="read one group time window")
    crawl.add_argument("group_url")
    crawl.add_argument("--after", help="inclusive ISO-8601 timestamp with offset")
    crawl.add_argument("--before", help="exclusive ISO-8601 timestamp with offset")
    crawl.add_argument("--output", type=Path, required=True)
    crawl.add_argument("--session", type=Path, help="local session file path")

    rebuild = commands.add_parser("rebuild-output", help="rebuild JSONL and Markdown from posts")
    rebuild.add_argument("output_dir", type=Path)
    rebuild.add_argument("--group-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_environment(getattr(args, "session", None))
    try:
        if args.command == "login":
            if args.cdp:
                login_from_cdp(settings.session_file, args.cdp)
            else:
                login_interactively(settings.session_file)
            print(f"Session saved locally: {settings.session_file}")
            return 0

        if args.command == "crawl":
            after = parse_datetime(args.after) if args.after else None
            before = parse_datetime(args.before) if args.before else None
            if after and before and after >= before:
                raise ValueError("--after must be earlier than --before")
            result = GroupCrawler(settings, ZsxqClient.from_session_file(settings)).crawl(
                args.group_url, args.output, CrawlWindow(after, before)
            )
            print(f"Finished: {result.termination_reason}; pages={result.pages_fetched}; posts={result.stats['posts_written']}")
            return 0

        count = rebuild_outputs(args.output_dir, args.group_id)
        stats = recompute_stats(args.output_dir, args.group_id)
        print(f"Rebuilt {count} posts; raw_pages={stats['raw_pages']}")
        return 0
    except (ValueError, DataIntegrityError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 2
