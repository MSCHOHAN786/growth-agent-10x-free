#!/usr/bin/env python3
"""
Prevent Supabase/Streamlit sleep (every 3 days) — GROUP D script.

1. Supabase ping: db.get_channels(status="active") and measure latency in ms.
2. Dashboard ping: GET $DASHBOARD_URL + "/?keepalive=1" with a 20s timeout.

Exit 1 if either fails. No secrets are ever printed.

Real interfaces (Group B):
    from core import SupabaseClient
    SupabaseClient() reads SUPABASE_URL / SUPABASE_SERVICE_KEY from env
    db.get_channels(status="active") -> list (any successful return = ping OK)

Exit codes: 0 = both healthy, 1 = failure, 2 = config error.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import urllib.request
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
    from core import SupabaseClient
except ImportError as exc:
    print(f"CONFIG ERROR: core modules missing: {exc}", file=sys.stderr)
    sys.exit(2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("keepalive")

TIMEOUT_S = 20


def ping_supabase() -> tuple[bool, str]:
    """Return (ok, detail)."""
    try:
        db = SupabaseClient()
    except Exception as exc:
        return False, f"SupabaseClient init failed: {exc}"
    start = time.perf_counter()
    try:
        channels = db.get_channels(status="active")
        latency_ms = (time.perf_counter() - start) * 1000
        n = len(channels) if isinstance(channels, list) else "?"
        return True, f"Supabase OK — {n} actieve kanalen, latency {latency_ms:.0f} ms"
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return False, f"Supabase ping failed after {latency_ms:.0f} ms: {exc}"


def ping_dashboard() -> tuple[bool, str]:
    """Return (ok, detail). Never logs the URL itself (may carry secrets)."""
    base = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    if not base:
        return False, "DASHBOARD_URL is not set"
    url = base + "/?keepalive=1"
    req = urllib.request.Request(url, headers={"User-Agent": "growth-agent-keepalive/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status = resp.status
            resp.read(1024)  # complete the request
        ok = 200 <= status < 400
        return ok, f"Dashboard ping -> HTTP {status} ({'OK' if ok else 'unexpected'})"
    except Exception as exc:
        return False, f"Dashboard ping failed: {type(exc).__name__}"


def main() -> int:
    log.info("Starting keepalive")

    supabase_ok, supabase_detail = ping_supabase()
    (log.info if supabase_ok else log.error)("%s", supabase_detail)

    dashboard_ok, dashboard_detail = ping_dashboard()
    (log.info if dashboard_ok else log.error)("%s", dashboard_detail)

    if supabase_ok and dashboard_ok:
        log.info("Finished keepalive: all healthy")
        return 0
    log.error("Finished keepalive: UNHEALTHY (supabase=%s dashboard=%s)",
              supabase_ok, dashboard_ok)
    return 1


if __name__ == "__main__":
    sys.exit(main())
