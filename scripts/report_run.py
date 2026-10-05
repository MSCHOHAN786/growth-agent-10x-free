#!/usr/bin/env python3
"""
Weekly report (Sunday 21:00 PKT) — GROUP D script.

Gathers:
  - per-channel analytics summaries (AnalyticsAgent.run when available,
    else cached rows from the niche_patterns table, else "Nog geen data")
  - llm_usage totals per provider for the last 7 days
    (db.get_llm_usage(provider, day) -> {requests, tokens, quota_hit})
  - api_usage YouTube quota units for the last 7 days
    (db.get_api_usage("youtube", day) -> int)

Composes a Dutch report, sends it via telegram.send_report("Weekrapport",
lines) and prints it to stdout. Empty data is handled gracefully
("Nog geen data").

Real interfaces (Groups B+C):
    from core import SupabaseClient, TelegramClient
    from core.llm_router import PROVIDERS  (names: groq, gemini, cohere)
    db.get_channels(status="active") -> list of dicts
    db.client.table("niche_patterns")... -> cached analytics fallback
    TelegramClient().send_report(title, lines) -> bool

Exit codes: 0 = success, 1 = failure, 2 = config error.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
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

try:
    from core.llm_router import PROVIDERS
    PROVIDER_NAMES = [p["name"] for p in PROVIDERS]
except (ImportError, KeyError, TypeError):
    PROVIDER_NAMES = ["groq", "gemini", "cohere"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("report_run")

NO_DATA = "Nog geen data"
DAYS = 7


def _import_analytics_agent():
    """Return the AnalyticsAgent class, or None when Group C hasn't delivered it."""
    try:
        pkg = __import__("agents", fromlist=["AnalyticsAgent"])
        cls = getattr(pkg, "AnalyticsAgent", None)
        if cls is not None:
            return cls
    except ImportError:
        pass
    try:
        mod = __import__("agents.analytics_agent", fromlist=["AnalyticsAgent"])
        return getattr(mod, "AnalyticsAgent", None)
    except ImportError:
        return None


def _last_n_days(n: int = DAYS) -> list[date]:
    today = date.today()
    return [today - timedelta(days=i) for i in range(n)]


def _llm_usage_7d(db) -> dict[str, dict]:
    """Aggregate {requests, tokens, quota_hits} per provider over 7 days."""
    totals = {p: {"requests": 0, "tokens": 0, "quota_hits": 0} for p in PROVIDER_NAMES}
    for provider in PROVIDER_NAMES:
        for day in _last_n_days():
            try:
                row = db.get_llm_usage(provider, day)
            except Exception as exc:
                log.warning("get_llm_usage(%s, %s) failed: %s", provider, day, exc)
                continue
            if not row:
                continue
            totals[provider]["requests"] += int(row.get("requests", 0) or 0)
            totals[provider]["tokens"] += int(row.get("tokens", 0) or 0)
            if row.get("quota_hit"):
                totals[provider]["quota_hits"] += 1
    return totals


def _youtube_units_7d(db) -> int:
    """Total YouTube API quota units consumed over the last 7 days."""
    total = 0
    for day in _last_n_days():
        try:
            total += int(db.get_api_usage("youtube", day) or 0)
        except Exception as exc:
            log.warning("get_api_usage(youtube, %s) failed: %s", day, exc)
    return total


def _cached_patterns(db, channel_id: str) -> list[dict]:
    """Latest cached niche_patterns rows for a channel (may be empty)."""
    client = getattr(db, "client", None)
    if client is None:
        return []
    resp = (
        client.table("niche_patterns")
        .select("pattern_key,pattern_value,updated_at")
        .eq("channel_id", channel_id)
        .order("updated_at", desc=True)
        .limit(5)
        .execute()
    )
    return list(resp.data or [])


def _channel_summary(db, tg, channel: dict) -> tuple[str, bool]:
    """Return (one-line summary, used_cache)."""
    cid = channel.get("id")
    # 1. Live analytics via the agent (when Group C has delivered it).
    AnalyticsAgent = _import_analytics_agent()
    if AnalyticsAgent is not None:
        try:
            agent = AnalyticsAgent(LLMRouter(), db, NicheEngine(), tg)
            result = agent.run(cid)
            if isinstance(result, dict) and result:
                return _format_analytics(result), False
        except Exception as exc:
            log.warning("Live analytics failed for %s: %s", cid, exc)
    # 2. Cached niche_patterns rows.
    try:
        rows = _cached_patterns(db, cid)
        if rows:
            keys = ", ".join(r.get("pattern_key", "?") for r in rows)
            return f"cached patronen: {keys}", True
    except Exception as exc:
        log.warning("Cached niche_patterns lookup failed for %s: %s", cid, exc)
    # 3. Nothing available.
    return NO_DATA, False


def _format_analytics(result: dict) -> str:
    views = result.get("views_7d", result.get("views"))
    subs = result.get("subscribers", result.get("subs"))
    videos = result.get("videos_published", result.get("published"))
    if views is None and subs is None and videos is None:
        return NO_DATA
    parts = []
    if views is not None:
        parts.append(f"views: {_fmt_int(views)}")
    if subs is not None:
        parts.append(f"subs: {_fmt_int(subs)}")
    if videos is not None:
        parts.append(f"video's: {videos}")
    return ", ".join(parts)


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def build_report(db, tg) -> list[str]:
    days = _last_n_days()
    lines = [
        f"Periode: {days[-1].isoformat()} t/m {days[0].isoformat()}",
        "",
        "Kanalen",
    ]

    try:
        channels = db.get_channels(status="active")
    except Exception as exc:
        log.error("Could not load channels: %s", exc)
        channels = []

    if not channels:
        lines.append(NO_DATA)
    for ch in channels:
        cid = ch.get("id")
        name = ch.get("name", cid)
        detail, cached = _channel_summary(db, tg, ch)
        suffix = " (cache)" if cached else ""
        lines.append(f"{name} ({cid}){suffix}: {detail}")

    lines += ["", "LLM-gebruik (7 dagen)"]
    usage = _llm_usage_7d(db)
    total_tokens = sum(v["tokens"] for v in usage.values())
    total_requests = sum(v["requests"] for v in usage.values())
    if total_requests == 0 and total_tokens == 0:
        lines.append(NO_DATA)
    else:
        for provider in PROVIDER_NAMES:
            v = usage[provider]
            line = (f"{provider}: {_fmt_int(v['tokens'])} tokens, "
                    f"{v['requests']} requests")
            if v["quota_hits"]:
                line += f" ({v['quota_hits']}x quota bereikt)"
            lines.append(line)
        lines.append(f"Totaal: {_fmt_int(total_tokens)} tokens, "
                     f"{total_requests} requests")

    lines += ["", "YouTube API-quota (7 dagen)"]
    units = _youtube_units_7d(db)
    lines.append(f"Verbruikte quota-eenheden: {_fmt_int(units)}")

    return lines


def main() -> int:
    log.info("Starting report_run (weekly)")

    try:
        db = SupabaseClient()
    except Exception as exc:
        log.error("Could not initialize SupabaseClient: %s", exc)
        return 2

    tg = TelegramClient()
    try:
        lines = build_report(db, tg)
    except Exception as exc:
        log.exception("Failed to build report: %s", exc)
        return 1

    print("\n".join(lines))

    try:
        ok = tg.send_report("Weekrapport", lines)
        if not ok:
            log.error("Telegram send_report returned False")
            return 1
        log.info("Telegram weekrapport sent (%d lines)", len(lines))
    except Exception as exc:
        log.error("Telegram notification failed: %s", exc)
        return 1

    log.info("Finished report_run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
