"""Supabase data-access layer shared by every worker in the system.

Conventions (table/column names match the Group A schema):
- ``channels``: ``id`` (psy_nl_01 … psy_nl_10), ``status`` …
- ``videos``: ``id``, ``channel_id``, ``status`` …
- ``job_queue``: ``id``, ``job_type``, ``status``, ``attempts``, ``payload`` …
- ``approval_queue``: ``id``, ``kind`` ('video'), ``item_id`` (video id),
  ``decision`` (null = pending), ``note``, ``decided_at`` …
- ``engagement_queue``: ``id``, ``video_id``, ``status`` ('pending_approval',
  'approved', 'rejected'), ``note`` …
- ``llm_usage``: ``provider``, ``day``, ``requests``, ``tokens``, ``quota_hit``
- ``analytics``: ``id``, ``video_id``, ``metrics``, ``collected_at``
- ``api_usage``: ``service``, ``day``, ``units``

Error contract: query methods (``get_*``) log failures and return a safe
default (``[]``, ``None`` or ``0``); mutation methods log and re-raise the
last error after retries, so callers never silently believe a write happened.
Every network call goes through :func:`_with_retry` (3 attempts, exponential
backoff starting at 2s).
"""

from __future__ import annotations

import functools
import logging
import os
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _with_retry(func: F) -> F:
    """Retry a Supabase call up to 3 times with exponential backoff.

    Attempts wait 2s, 4s, 8s. Each failure is logged; after the final attempt
    the exception is re-raised.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - retry covers all call errors
                last_exc = exc
                if attempt < 3:
                    wait = 2**attempt  # 2s, 4s, 8s
                    logger.warning(
                        "%s attempt %d/3 failed (%s); retrying in %ds",
                        func.__name__,
                        attempt,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
        logger.error("%s failed after 3 attempts: %s", func.__name__, last_exc)
        assert last_exc is not None
        raise last_exc

    return wrapper  # type: ignore[return-value]


class SupabaseClient:
    """Typed wrapper around the Supabase Python client.

    Credentials come from the environment only (``SUPABASE_URL`` and
    ``SUPABASE_SERVICE_KEY``); nothing secret is ever stored in code.

    Args:
        url: Optional explicit Supabase URL (overrides env).
        service_key: Optional explicit service key (overrides env).

    Raises:
        RuntimeError: If credentials are missing, or the ``supabase``
            package is not installed.
    """

    def __init__(
        self, url: Optional[str] = None, service_key: Optional[str] = None
    ) -> None:
        self.url = url or os.environ.get("SUPABASE_URL", "").strip()
        self.service_key = service_key or os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not self.url or not self.service_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables "
                "are required. Set them before constructing SupabaseClient."
            )
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                "The 'supabase' package is required. Install it with: "
                "pip install supabase"
            ) from exc
        self.client = create_client(self.url, self.service_key)
        logger.info("SupabaseClient initialised")

    def table(self, name: str) -> Any:
        """Direct access to a supabase-py table builder (for agent helpers).

        Args:
            name: Table name, e.g. ``"videos"``.

        Returns:
            The supabase-py table object supporting ``.select()``,
            ``.insert()``, ``.update()``, ``.upsert()`` etc.
        """
        return self.client.table(name)

    # ------------------------------------------------------------------ #
    # Channels                                                            #
    # ------------------------------------------------------------------ #
    @_with_retry
    def get_channels(self, status: str = "active") -> list[dict]:
        """Return all channels with the given status (default 'active').

        Args:
            status: Channel status filter.

        Returns:
            List of channel rows, ``[]`` on error.
        """
        try:
            resp = (
                self.client.table("channels")
                .select("*")
                .eq("status", status)
                .order("id")
                .execute()
            )
            return list(resp.data or [])
        except Exception as exc:  # noqa: BLE001
            logger.error("get_channels failed: %s", exc)
            return []

    @_with_retry
    def get_channel(self, channel_id: str) -> Optional[dict]:
        """Return a single channel row by id.

        Args:
            channel_id: e.g. ``psy_nl_01``.

        Returns:
            The channel row, or ``None`` if not found / on error.
        """
        try:
            resp = (
                self.client.table("channels")
                .select("*")
                .eq("id", channel_id)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return dict(rows[0]) if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.error("get_channel(%s) failed: %s", channel_id, exc)
            return None

    # ------------------------------------------------------------------ #
    # Jobs                                                                #
    # ------------------------------------------------------------------ #
    @_with_retry
    def get_pending_jobs(self, job_type: str, limit: int = 20) -> list[dict]:
        """Fetch pending jobs of a type, oldest first.

        Args:
            job_type: e.g. 'research', 'script', 'produce', 'publish'.
            limit: Maximum rows to return.

        Returns:
            List of job rows, ``[]`` on error.
        """
        try:
            resp = (
                self.client.table("job_queue")
                .select("*")
                .eq("job_type", job_type)
                .eq("status", "pending")
                .order("created_at")
                .limit(limit)
                .execute()
            )
            return list(resp.data or [])
        except Exception as exc:  # noqa: BLE001
            logger.error("get_pending_jobs(%s) failed: %s", job_type, exc)
            return []

    @_with_retry
    def update_job_status(
        self, job_id: str, status: str, increment_attempts: bool = False
    ) -> None:
        """Set a job's status (optionally bumping its attempt counter).

        Args:
            job_id: Row id in ``job_queue``.
            status: New status, e.g. 'running', 'done', 'failed'.
            increment_attempts: Also increment ``attempts`` by 1.

        Raises:
            Exception: The last Supabase error after retries.
        """
        try:
            if increment_attempts:
                job = (
                    self.client.table("job_queue")
                    .select("attempts")
                    .eq("id", job_id)
                    .limit(1)
                    .execute()
                )
                rows = job.data or []
                attempts = int((rows[0] or {}).get("attempts") or 0) + 1
                self.client.table("job_queue").update(
                    {"status": status, "attempts": attempts}
                ).eq("id", job_id).execute()
            else:
                self.client.table("job_queue").update({"status": status}).eq(
                    "id", job_id
                ).execute()
            logger.info("Job %s -> %s", job_id, status)
        except Exception as exc:  # noqa: BLE001
            logger.error("update_job_status(%s, %s) failed: %s", job_id, status, exc)
            raise

    # ------------------------------------------------------------------ #
    # Videos                                                              #
    # ------------------------------------------------------------------ #
    @_with_retry
    def insert_video(self, video_data: dict) -> str:
        """Insert a video row.

        Args:
            video_data: Row payload. If it lacks ``id``, a UUID is generated.

        Returns:
            The video id.

        Raises:
            ValueError: If video_data is empty.
            Exception: The last Supabase error after retries.
        """
        if not video_data:
            raise ValueError("video_data must not be empty")
        payload = dict(video_data)
        payload.setdefault("id", str(uuid.uuid4()))
        try:
            self.client.table("videos").insert(payload).execute()
            logger.info("Inserted video %s", payload["id"])
            return str(payload["id"])
        except Exception as exc:  # noqa: BLE001
            logger.error("insert_video failed: %s", exc)
            raise

    @_with_retry
    def update_video_status(
        self, video_id: str, status: str, extra: Optional[dict] = None
    ) -> None:
        """Set a video's status plus any extra fields.

        Args:
            video_id: Row id in ``videos``.
            status: New status, e.g. 'approved', 'published'.
            extra: Additional columns to update alongside ``status``.

        Raises:
            Exception: The last Supabase error after retries.
        """
        payload = {"status": status}
        if extra:
            payload.update(extra)
        try:
            self.client.table("videos").update(payload).eq("id", video_id).execute()
            logger.info("Video %s -> %s", video_id, status)
        except Exception as exc:  # noqa: BLE001
            logger.error("update_video_status(%s, %s) failed: %s", video_id, status, exc)
            raise

    # ------------------------------------------------------------------ #
    # Approvals (video) / engagement (comment)                             #
    # ------------------------------------------------------------------ #
    @_with_retry
    def get_pending_approvals(self, kind: str = "video") -> list[dict]:
        """Fetch items waiting for human approval.

        Args:
            kind: 'video' → rows in ``approval_queue`` where ``decision`` is
                null; 'comment' → rows in ``engagement_queue`` where
                ``status`` is 'pending_approval'.

        Returns:
            List of pending rows, ``[]`` on error.

        Raises:
            ValueError: For an unknown kind.
        """
        if kind not in ("video", "comment"):
            raise ValueError("kind must be 'video' or 'comment'")
        try:
            if kind == "video":
                resp = (
                    self.client.table("approval_queue")
                    .select("*")
                    .is_("decision", "null")
                    .order("requested_at")
                    .execute()
                )
            else:
                resp = (
                    self.client.table("engagement_queue")
                    .select("*")
                    .eq("status", "pending_approval")
                    .order("created_at")
                    .execute()
                )
            return list(resp.data or [])
        except Exception as exc:  # noqa: BLE001
            logger.error("get_pending_approvals(%s) failed: %s", kind, exc)
            return []

    @_with_retry
    def decide_approval(
        self, queue_id: str, kind: str, decision: str, note: str = ""
    ) -> None:
        """Record a human decision on an approval/comment item.

        Decision values match the DB CHECK constraints and every other
        writer (Cloudflare worker, Streamlit dashboard): ``'approved'``,
        ``'rejected'`` or ``'needs_edit'``. For convenience ``'approve'``
        and ``'reject'`` are accepted as aliases.

        Args:
            queue_id: Row id in the approval/engagement queue.
            kind: 'video' (approval_queue) or 'comment' (engagement_queue).
            decision: 'approved'/'rejected'/'needs_edit' (aliases 'approve'/'reject' ok).
            note: Optional reviewer note (stored in ``reviewer_note`` for videos).

        Raises:
            ValueError: For an unknown kind or decision.
            Exception: The last Supabase error after retries.
        """
        if kind not in ("video", "comment"):
            raise ValueError("kind must be 'video' or 'comment'")
        aliases = {"approve": "approved", "reject": "rejected", "edit": "needs_edit"}
        decision = aliases.get(decision, decision)
        if decision not in ("approved", "rejected", "needs_edit"):
            raise ValueError(
                "decision must be 'approved', 'rejected' or 'needs_edit'"
            )
        decided_at = datetime.now(timezone.utc).isoformat()
        try:
            if kind == "video":
                self.client.table("approval_queue").update(
                    {
                        "decision": decision,
                        "reviewer_note": note,
                        "decided_at": decided_at,
                    }
                ).eq("id", queue_id).execute()
            else:
                self.client.table("engagement_queue").update(
                    {"status": decision, "decided_at": decided_at}
                ).eq("id", queue_id).execute()
            logger.info("Decided %s %s: %s", kind, queue_id, decision)
        except Exception as exc:  # noqa: BLE001
            logger.error("decide_approval(%s, %s) failed: %s", kind, queue_id, exc)
            raise

    @_with_retry
    def enqueue_engagement(self, item: dict) -> str:
        """Add an item (comment/reply) to the engagement queue.

        Args:
            item: Row payload; ``id`` is generated when absent and
                ``status`` defaults to 'pending_approval'.

        Returns:
            The queue item id.

        Raises:
            ValueError: If item is empty.
            Exception: The last Supabase error after retries.
        """
        if not item:
            raise ValueError("item must not be empty")
        payload = dict(item)
        payload.setdefault("id", str(uuid.uuid4()))
        payload.setdefault("status", "pending_approval")
        try:
            self.client.table("engagement_queue").insert(payload).execute()
            logger.info("Enqueued engagement item %s", payload["id"])
            return str(payload["id"])
        except Exception as exc:  # noqa: BLE001
            logger.error("enqueue_engagement failed: %s", exc)
            raise

    # ------------------------------------------------------------------ #
    # LLM usage counters                                                  #
    # ------------------------------------------------------------------ #
    @_with_retry
    def log_llm_usage(self, provider: str, tokens: int) -> None:
        """Increment today's request+token counters for a provider.

        Creates the ``llm_usage`` row for (provider, today) when absent.

        Args:
            provider: 'groq', 'gemini' or 'cohere'.
            tokens: Estimated token count for this call.

        Raises:
            Exception: The last Supabase error after retries.
        """
        today = date.today().isoformat()
        try:
            existing = self.get_llm_usage(provider, date.today())
            if existing:
                self.client.table("llm_usage").update(
                    {
                        "requests": int(existing.get("requests") or 0) + 1,
                        "tokens": int(existing.get("tokens") or 0) + max(tokens, 0),
                    }
                ).eq("provider", provider).eq("day", today).execute()
            else:
                self.client.table("llm_usage").insert(
                    {
                        "provider": provider,
                        "day": today,
                        "requests": 1,
                        "tokens": max(tokens, 0),
                        "quota_hit": False,
                    }
                ).execute()
            logger.debug("Logged LLM usage %s: +%d tokens", provider, tokens)
        except Exception as exc:  # noqa: BLE001
            logger.error("log_llm_usage(%s) failed: %s", provider, exc)
            raise

    @_with_retry
    def get_llm_usage(self, provider: str, day: date) -> Optional[dict]:
        """Return the usage row for (provider, day), or None when absent.

        Args:
            provider: Provider name.
            day: The day to look up.

        Returns:
            The usage row, or ``None`` on error / when absent.
        """
        try:
            resp = (
                self.client.table("llm_usage")
                .select("*")
                .eq("provider", provider)
                .eq("day", day.isoformat())
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return dict(rows[0]) if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.error("get_llm_usage(%s, %s) failed: %s", provider, day, exc)
            return None

    @_with_retry
    def mark_quota_hit(self, provider: str) -> None:
        """Flag today's ``llm_usage`` row as quota-hit (creates it if absent).

        Args:
            provider: Provider name.

        Raises:
            Exception: The last Supabase error after retries.
        """
        today = date.today().isoformat()
        try:
            existing = self.get_llm_usage(provider, date.today())
            if existing:
                self.client.table("llm_usage").update({"quota_hit": True}).eq(
                    "provider", provider
                ).eq("day", today).execute()
            else:
                self.client.table("llm_usage").insert(
                    {
                        "provider": provider,
                        "day": today,
                        "requests": 0,
                        "tokens": 0,
                        "quota_hit": True,
                    }
                ).execute()
            logger.warning("Quota hit recorded for provider %s", provider)
        except Exception as exc:  # noqa: BLE001
            logger.error("mark_quota_hit(%s) failed: %s", provider, exc)
            raise

    # ------------------------------------------------------------------ #
    # Analytics                                                           #
    # ------------------------------------------------------------------ #
    @_with_retry
    def insert_analytics(self, video_id: str, metrics: dict) -> None:
        """Store a metrics snapshot for a video.

        Args:
            video_id: The video row id.
            metrics: e.g. {'views': 123, 'likes': 4, 'comments': 1}.

        Raises:
            ValueError: If metrics is empty.
            Exception: The last Supabase error after retries.
        """
        if not metrics:
            raise ValueError("metrics must not be empty")
        try:
            self.client.table("analytics").insert(
                {"id": str(uuid.uuid4()), "video_id": video_id, "metrics": metrics}
            ).execute()
            logger.info("Inserted analytics for video %s", video_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("insert_analytics(%s) failed: %s", video_id, exc)
            raise

    # ------------------------------------------------------------------ #
    # API usage counters (YouTube quota etc.)                             #
    # ------------------------------------------------------------------ #
    @_with_retry
    def get_api_usage(self, service: str, day: date) -> int:
        """Return today's consumed units for a service, 0 when absent/error.

        Args:
            service: e.g. 'youtube'.
            day: The day to look up.

        Returns:
            Units consumed (int).
        """
        try:
            resp = (
                self.client.table("api_usage")
                .select("units")
                .eq("service", service)
                .eq("day", day.isoformat())
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return int((rows[0] or {}).get("units") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("get_api_usage(%s, %s) failed: %s", service, day, exc)
            return 0

    @_with_retry
    def add_api_usage(self, service: str, units: int) -> None:
        """Add units to today's ``api_usage`` counter (upsert).

        Args:
            service: e.g. 'youtube'.
            units: Units to add (must be >= 0).

        Raises:
            ValueError: If units is negative.
            Exception: The last Supabase error after retries.
        """
        if units < 0:
            raise ValueError("units must be >= 0")
        today = date.today().isoformat()
        try:
            current = self.get_api_usage(service, date.today())
            self.client.table("api_usage").upsert(
                {"service": service, "day": today, "units": current + units},
                on_conflict="service,day",
            ).execute()
            logger.debug("API usage %s: +%d units", service, units)
        except Exception as exc:  # noqa: BLE001
            logger.error("add_api_usage(%s, %d) failed: %s", service, units, exc)
            raise
