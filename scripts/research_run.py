#!/usr/bin/env python3
"""
Daily trend research (06:00 PKT) — GROUP D script.

For each active channel: ResearchAgent.run(channel_id) -> 5 new video ideas.
Idempotent: the agent caches per channel per day in niche_patterns
(pattern_key "research:<YYYY-MM-DD>"); this script only orchestrates,
summarizes and notifies via Telegram.

Real interfaces (Groups B+C):
    from core import SupabaseClient, LLMRouter, NicheEngine, TelegramClient
    from agents.research_agent import ResearchAgent   (agents/__init__ may
        not re-export yet, so a submodule fallback is used)
    db.get_channels(status="active") -> list of dicts (id, name, status, ...)
    ResearchAgent(llm, db, niche, telegram).run(channel_id) -> list[dict]
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
log = logging.getLogger("research_run")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily trend research per channel")
    parser.add_argument("--channel", help="Run for a single channel id (e.g. psy_nl_01)")
    args = parser.parse_args()

    log.info("Starting research_run%s", f" for {args.channel}" if args.channel else "")

    ResearchAgent = import_agent("ResearchAgent", "research_agent")

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
                log.error("Channel %s is not active (status=%s)",
                          args.channel, ch.get("status"))
                return 1
            channels = [ch]
        else:
            channels = db.get_channels(status="active")
    except Exception as exc:
        log.error("Could not load channels: %s", exc)
        return 1

    if not channels:
        log.warning("No active channels found; nothing to do")

    tg = TelegramClient()
    total_ideas = 0
    ok_channels = 0
    failed_channels: list[str] = []

    for ch in channels:
        cid = ch.get("id")
        name = ch.get("name", cid)
        try:
            agent = ResearchAgent(LLMRouter(), db, NicheEngine(), tg)
            ideas = agent.run(cid)
            n = len(ideas) if isinstance(ideas, (list, tuple)) else 0
            total_ideas += n
            ok_channels += 1
            log.info("Channel %s (%s): %d ideas", cid, name, n)
        except Exception as exc:  # per-channel continue-on-error
            failed_channels.append(str(cid))
            log.exception("Research failed for channel %s: %s", cid, exc)

    summary = f"Onderzoek voltooid: {total_ideas} ideeën voor {ok_channels} kanalen"
    if failed_channels:
        summary += f" (mislukt: {', '.join(failed_channels)})"

    try:
        tg.send_message(None, summary)
        log.info("Telegram report sent: %s", summary)
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)
        return 1

    log.info("Finished research_run: %d ideas, %d/%d channels OK",
             total_ideas, ok_channels, len(channels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
