#!/usr/bin/env python3
"""
Draft comment replies (every 3h) — GROUP D script.

Runs EngagementAgent.run(), which DRAFTS replies and enqueues them for human
approval (never auto-posts). This script counts the enqueued drafts and sends
a Telegram summary.

Real interfaces (Groups B+C):
    from core import SupabaseClient, LLMRouter, NicheEngine, TelegramClient
    EngagementAgent(llm, db, niche, telegram).run(limit_channels=10)
        -> list[dict] drafts: {channel_id, video_id, comment_id,
           comment_author, comment_text, draft_reply}.
        Drafts are only enqueued for human approval — nothing is auto-posted.
    TelegramClient().send_message(chat_id, text)  (chat_id=None -> admin chat)

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
log = logging.getLogger("comments_run")


def _count_drafts(result) -> int:
    """Normalize the agent result to a draft count."""
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    if isinstance(result, (list, tuple)):
        return len(result)
    if isinstance(result, dict):
        drafts = result.get("drafts")
        if isinstance(drafts, (list, tuple)):
            return len(drafts)
        for key in ("count", "enqueued", "total"):
            if isinstance(result.get(key), int):
                return result[key]
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft comment replies (never auto-posts)")
    parser.add_argument("--channel", help="Limit drafting to one channel id")
    args = parser.parse_args()

    log.info("Starting comments_run%s", f" for {args.channel}" if args.channel else "")

    EngagementAgent = import_agent("EngagementAgent", "engagement_agent")

    try:
        db = SupabaseClient()
    except Exception as exc:
        log.error("Could not initialize SupabaseClient: %s", exc)
        return 2

    tg = TelegramClient()
    try:
        agent = EngagementAgent(LLMRouter(), db, NicheEngine(), tg)
        drafts = agent.run(limit_channels=10)
        if args.channel:
            drafts = [d for d in drafts
                      if isinstance(d, dict) and d.get("channel_id") == args.channel]
    except Exception as exc:
        log.exception("EngagementAgent run failed: %s", exc)
        return 1

    n = _count_drafts(drafts)
    log.info("Drafts enqueued for approval: %d (nothing auto-posted)", n)

    summary = f"{n} conceptreacties wachten op goedkeuring"
    try:
        tg.send_message(None, summary)
        log.info("Telegram report sent: %s", summary)
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)
        return 1

    log.info("Finished comments_run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
