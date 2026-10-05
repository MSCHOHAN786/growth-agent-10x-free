#!/usr/bin/env python3
"""
Publish approved videos (10:00 PKT) — GROUP D script.

Runs PublishAgent.run(), which respects human-in-the-loop: only videos with
an approved status are uploaded to YouTube. This script summarizes
published / skipped / failed counts and sends a Telegram report with video
titles + YouTube IDs.

Real interfaces (Groups B+C):
    from core import SupabaseClient, LLMRouter, NicheEngine, TelegramClient
    PublishAgent(llm, db, niche, telegram).run() -> list[dict] with per-video
        results: {video_id, status, youtube_video_id|error}; status is one of
        'published' / 'skipped' / 'quota_exhausted' / 'error'.
        Only approved + scheduled videos are uploaded (human-in-the-loop).
    TelegramClient().send_report(title, lines) -> bool (admin chat)

Exit codes: 0 = success, 1 = failure, 2 = config error.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # tolerate missing dotenv
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

load_dotenv(REPO_ROOT / ".env")

try:
    from core import SupabaseClient, LLMRouter, NicheEngine, TelegramClient
except ImportError as exc:
    print(f"CONFIG ERROR: core modules missing: {exc}", file=sys.stderr)
    sys.exit(2)


def import_agent(class_name: str, module_name: str):
    """Import an agent class, trying the agents package first, then its submodule."""
    try:
        pkg = __import__("agents", fromlist=[class_name])
        return getattr(pkg, class_name)
    except (ImportError, AttributeError):
        pass
    try:
        mod = __import__(f"agents.{module_name}", fromlist=[class_name])
        return getattr(mod, class_name)
    except (ImportError, AttributeError) as exc:
        print(f"CONFIG ERROR: {class_name} not available: {exc}",
              file=sys.stderr)
        sys.exit(2)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("publish_run")


def _summarize(results: list) -> tuple[list, list, list]:
    """Split per-video results into (published, skipped, failed)."""
    published, skipped, failed = [], [], []
    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).lower()
        if status == "published":
            published.append(item)
        elif status in ("skipped", "quota_exhausted"):
            skipped.append(item)
        else:
            failed.append(item)
    return published, skipped, failed


def _label(item: dict) -> str:
    title = item.get("title")
    vid = item.get("video_id", "?")
    return f"{title} ({vid})" if title else str(vid)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish approved videos")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report what would be published, without uploading")
    args = parser.parse_args()

    log.info("Starting publish_run%s", " (dry run)" if args.dry_run else "")

    PublishAgent = import_agent("PublishAgent", "publish_agent")

    try:
        db = SupabaseClient()
    except Exception as exc:
        log.error("Could not initialize SupabaseClient: %s", exc)
        return 2

    tg = TelegramClient()
    try:
        agent = PublishAgent(LLMRouter(), db, NicheEngine(), tg)
        if args.dry_run:
            if hasattr(agent, "dry_run"):
                result = agent.dry_run()
            else:
                log.warning("--dry-run: agent has no dry_run(); "
                            "nothing published, exiting without action")
                return 0
        else:
            result = agent.run()
    except Exception as exc:
        log.exception("PublishAgent run failed: %s", exc)
        return 1

    if not isinstance(result, (list, tuple)):
        log.error("PublishAgent returned unexpected type: %r", type(result))
        return 1

    published, skipped, failed = _summarize(result)

    log.info("Published=%d skipped=%d failed=%d",
             len(published), len(skipped), len(failed))

    lines = [
        f"Gepubliceerd: {len(published)}",
        f"Overgeslagen: {len(skipped)}",
        f"Mislukt: {len(failed)}",
    ]
    for item in published:
        lines.append(f"{_label(item)} — YouTube: {item.get('youtube_video_id', '?')}")
    for item in failed:
        reason = item.get("error", "")
        lines.append(f"MISLUKT: {_label(item)}" + (f" ({reason})" if reason else ""))

    try:
        tg.send_report("Publicatieronde voltooid", lines)
        log.info("Telegram report sent")
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)
        return 1

    log.info("Finished publish_run")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
