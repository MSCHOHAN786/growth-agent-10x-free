#!/usr/bin/env python3
"""
Fetch channel analytics — GROUP D script.

For each active channel: AnalyticsAgent.run(channel_id), with per-channel
try/except (one failing channel must not abort the rest). Prints a JSON
summary to stdout for downstream jobs/workflows to consume.

Real interfaces (Groups B+C):
    from core import SupabaseClient, LLMRouter, NicheEngine, TelegramClient
    db.get_channels(status="active") -> list of dicts
    db.get_channel(channel_id) -> dict | None
    AnalyticsAgent(llm, db, niche, telegram).run(channel_id) -> dict
        (JSON-serializable summary)

Exit codes: 0 = success, 1 = failure, 2 = config error.
"""
from __future__ import annotations

import argparse
import json
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
log = logging.getLogger("analytics_run")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch channel analytics")
    parser.add_argument("--channel", help="Fetch analytics for a single channel id")
    parser.add_argument("--out", help="Write JSON summary to this file (in addition to stdout)")
    args = parser.parse_args()

    log.info("Starting analytics_run%s", f" for {args.channel}" if args.channel else "")

    AnalyticsAgent = import_agent("AnalyticsAgent", "analytics_agent")

    try:
        db = SupabaseClient()
    except Exception as exc:
        log.error("Could not initialize SupabaseClient: %s", exc)
        return 2

    try:
        if args.channel:
            ch = db.get_channel(args.channel)
            if ch is None:
                log.error("Channel %s not found", args.channel)
                return 1
            if str(ch.get("status", "active")).lower() != "active":
                log.error("Channel %s is not active", args.channel)
                return 1
            channels = [ch]
        else:
            channels = db.get_channels(status="active")
    except Exception as exc:
        log.error("Could not load channels: %s", exc)
        return 1

    tg = TelegramClient()
    summary: dict = {"channels": {}, "errors": {}}
    for ch in channels:
        cid = ch.get("id")
        try:
            agent = AnalyticsAgent(LLMRouter(), db, NicheEngine(), tg)
            result = agent.run(cid)
            summary["channels"][str(cid)] = (
                result if isinstance(result, dict) else {"result": result})
            log.info("Analytics OK for channel %s", cid)
        except Exception as exc:  # per-channel continue-on-error
            summary["errors"][str(cid)] = str(exc)
            log.exception("Analytics failed for channel %s: %s", cid, exc)

    payload = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        log.info("Wrote JSON summary to %s", args.out)

    log.info("Finished analytics_run: %d ok, %d failed",
             len(summary["channels"]), len(summary["errors"]))
    return 1 if summary["errors"] and not summary["channels"] else 0


if __name__ == "__main__":
    sys.exit(main())
