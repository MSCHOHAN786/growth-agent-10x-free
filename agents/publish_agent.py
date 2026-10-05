"""Publish-agent: uploadt goedgekeurde video's naar YouTube (met quota-bewaking).

Alleen video's met een ``approval_queue``-rij met ``decision='approved'``,
``videos.status='scheduled'``, ``scheduled_at <= nu`` en nog geen
``youtube_video_id`` komen in aanmerking. Per upload worden 1600
quota-units verbruikt; de daglimiet van 10.000 units wordt nooit
overschreden — bij dreigende overschrijding stopt de run.

Upload is idempotent: video's met een ``youtube_video_id`` worden
overgeslagen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base_agent import (
    YOUTUBE_DAILY_QUOTA,
    YOUTUBE_UPLOAD_COST,
    BaseAgent,
)


class PublishAgent(BaseAgent):
    """Publiceert goedgekeurde, ingeplande video's op YouTube."""

    def run(self) -> list[dict]:
        """Upload alle publiceerbare video's.

        Returns:
            Lijst met per-video resultaten: ``video_id``, ``status``
            (``published`` / ``skipped`` / ``quota_exhausted`` / ``error``),
            plus ``youtube_video_id`` of ``error``.
        """
        results: list[dict] = []
        candidates = self._publishable_videos()
        self._log_info(f"{len(candidates)} video('s) klaar voor publicatie")

        for video in candidates:
            video_id = video.get("id")
            if video.get("youtube_video_id"):
                self._log_info(f"Video {video_id} al gepubliceerd; sla over")
                results.append(
                    {
                        "video_id": video_id,
                        "status": "skipped",
                        "youtube_video_id": video.get("youtube_video_id"),
                    }
                )
                continue

            used = int(self.db.get_api_usage("youtube", self._today()) or 0)
            if used + YOUTUBE_UPLOAD_COST > YOUTUBE_DAILY_QUOTA:
                self._log_error(
                    f"YouTube-quota op ({used}/{YOUTUBE_DAILY_QUOTA}); "
                    f"stop publicatie, {len(candidates) - len(results)} resterend"
                )
                results.append({"video_id": video_id, "status": "quota_exhausted"})
                break

            try:
                service = self._get_youtube_service(video["channel_id"])
                video_file = self._resolve_video_file(video)
                youtube_video_id = self._upload_video(service, video, video_file)
                self._db_update(
                    "videos",
                    {
                        "status": "published",
                        "youtube_video_id": youtube_video_id,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                    },
                    id=video_id,
                )
                self.db.add_api_usage("youtube", YOUTUBE_UPLOAD_COST)
                self._log_info(
                    f"Video {video_id} gepubliceerd als {youtube_video_id}"
                )
                results.append(
                    {
                        "video_id": video_id,
                        "status": "published",
                        "youtube_video_id": youtube_video_id,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - per-video foutafhandeling
                self._alert_error(f"publicatie van video {video_id}", exc)
                results.append(
                    {"video_id": video_id, "status": "error", "error": str(exc)}
                )
        return results

    # ------------------------------------------------------------------
    # Selectie
    # ------------------------------------------------------------------
    def _publishable_videos(self) -> list[dict]:
        """Haal video's op die goedgekeurd, ingepland en nog niet live zijn."""
        now = datetime.now(timezone.utc)
        approved_rows = self._db_select("approval_queue", decision="approved")
        approved_ids = {
            row.get("video_id") for row in approved_rows if row.get("video_id")
        }
        scheduled = self._db_select(
            "videos", status="scheduled", order="scheduled_at"
        )
        publishable: list[dict] = []
        for video in scheduled:
            if video.get("id") not in approved_ids:
                continue
            if video.get("youtube_video_id"):
                continue
            scheduled_at = video.get("scheduled_at")
            if scheduled_at:
                try:
                    when = datetime.fromisoformat(str(scheduled_at))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when > now:
                        continue
                except ValueError:
                    self._log_error(
                        f"Video {video.get('id')}: ongeldige scheduled_at "
                        f"'{scheduled_at}'; sla over"
                    )
                    continue
            publishable.append(video)
        return publishable

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def _resolve_video_file(self, video: dict) -> str:
        """Bepaal het pad naar het te uploaden videobestand.

        Bron (in volgorde): ``videos.file_path``, daarna
        ``payload.video_file`` uit de approval-rij, anders
        ``videos.extra.video_file`` (legacy).
        """
        video_file = video.get("file_path")
        if not video_file:
            approvals = self._db_select(
                "approval_queue", video_id=video.get("id"), decision="approved", limit=1
            )
            payload: dict = {}
            if approvals:
                raw_payload = approvals[0].get("payload") or {}
                payload = raw_payload if isinstance(raw_payload, dict) else {}
            extra = video.get("extra") or {}
            video_file = payload.get("video_file") or (
                extra.get("video_file") if isinstance(extra, dict) else None
            )
        if not video_file:
            raise ValueError(
                f"Geen videobestand gevonden voor video {video.get('id')} "
                "(verwacht videos.file_path, payload.video_file of extra.video_file)"
            )
        return str(video_file)

    def _upload_video(
        self, service: Any, video: dict, video_file: str
    ) -> str:
        """Upload het bestand resumable en geef het YouTube video-ID terug."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError(
                "google-api-python-client ontbreekt; installeer met: "
                "pip install google-api-python-client google-auth"
            ) from exc

        body = {
            "snippet": {
                "title": video.get("title", ""),
                "description": video.get("description", ""),
                "tags": video.get("tags") or [],
                "categoryId": "27",  # Education
            },
            "status": {
                "privacyStatus": "public",
                "madeForKids": False,
            },
        }
        media = MediaFileUpload(
            video_file, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024
        )
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                self._log_info(
                    f"Upload video {video.get('id')}: "
                    f"{int(status.progress() * 100)}%"
                )
        youtube_video_id = response.get("id")
        if not youtube_video_id:
            raise RuntimeError("YouTube gaf geen video-ID terug na upload")
        return str(youtube_video_id)
