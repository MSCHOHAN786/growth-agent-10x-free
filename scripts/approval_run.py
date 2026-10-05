#!/usr/bin/env python3
"""Queue ready drafts for human approval (runs after research, before publish).

This script closes the loop between content generation and publishing:

    draft  →  pending_approval (+ approval_queue row + Telegram request)
             →  approved → scheduled → published   (via dashboard / Telegram)
             →  rejected → draft                   (rework)

For every video with ``status='draft'`` that has a title and script:
  1. Skip it when an undecided ``approval_queue`` row already exists
     (idempotent — safe to re-run).
  2. Set ``videos.status='pending_approval'``.
  3. Insert an ``approval_queue`` row (``decision`` NULL).
  4. Send a Telegram approval request with inline buttons
     (Goedkeuren / Afkeuren / Aanpassen).

Intended schedule: daily at ~06:30 PKT, right after ``research_run.py``,
so the admin has a few hours to approve before ``publish_run.py``
(10:00 PKT) picks up ``scheduled`` videos.

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
    from core import SupabaseClient, TelegramClient
except ImportError as exc:
    print(f"CONFIG ERROR: core modules missing: {exc}", file=sys.stderr)
    sys.exit(2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("approval_run")


def queue_drafts(db: SupabaseClient, tg: TelegramClient | None, channel: str | None = None) -> dict:
    """Move ready drafts to pending_approval and request Telegram approval."""
    query = (
        db.table("videos")
        .select("id,title,channel_id,script")
        .eq("status", "draft")
        .order("created_at")
        .limit(50)
    )
    if channel:
        query = query.eq("channel_id", channel)
    drafts = list((query.execute().data or []))
    logger.info("Found %d draft video(s)%s", len(drafts), f" for {channel}" if channel else "")

    queued, skipped, failed = 0, 0, 0
    for video in drafts:
        video_id = video.get("id")
        if not video.get("title") or not video.get("script"):
            logger.info("Skipping %s: no title/script yet", video_id)
            skipped += 1
            continue
        try:
            existing = (
                db.table("approval_queue")
                .select("id")
                .eq("video_id", video_id)
                .is_("decision", "null")
                .limit(1)
                .execute()
                .data
            )
            if existing:
                logger.info("Skipping %s: approval already pending", video_id)
                skipped += 1
                continue
            db.table("videos").update({"status": "pending_approval"}).eq("id", video_id).execute()
            db.table("approval_queue").insert({"video_id": video_id}).execute()
            if tg is not None:
                tg.send_approval_request(video)
            queued += 1
            logger.info("Queued for approval: %s (%s)", video.get("title"), video_id)
        except Exception as exc:  # noqa: BLE001 - per-video continue
            logger.error("Failed to queue %s: %r", video_id, exc)
            failed += 1
    return {"queued": queued, "skipped": skipped, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue draft videos for human approval.")
    parser.add_argument("--channel", help="Only queue drafts for this channel id")
    parser.add_argument("--dry-run", action="store_true", help="Report only, change nothing")
    args = parser.parse_args()

    try:
        db = SupabaseClient()
    except RuntimeError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    tg: TelegramClient | None
    try:
        tg = TelegramClient()
    except Exception as exc:  # noqa: BLE001 - Telegram is optional
        logger.warning("Telegram unavailable, continuing without notifications: %r", exc)
        tg = None

    if args.dry_run:
        logger.info("DRY-RUN: no changes will be made")
        # Still list what would be queued.
        query = db.table("videos").select("id,title,channel_id").eq("status", "draft").limit(50)
        if args.channel:
            query = query.eq("channel_id", args.channel)
        for v in query.execute().data or []:
            logger.info("Would queue: %s (%s)", v.get("title"), v.get("id"))
        return 0

    try:
        result = queue_drafts(db, tg, args.channel)
    except Exception as exc:  # noqa: BLE001
        logger.error("approval_run failed: %r", exc)
        return 1

    summary = (
        f"Goedkeuring aangevraagd: {result['queued']} video's in de wachtrij, "
        f"{result['skipped']} overgeslagen, {result['failed']} mislukt."
    )
    logger.info(summary)
    if tg is not None:
        tg.send_message(None, f"📋 <b>Approval-ronde</b>\n{summary}")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
