"""Engagement-agent: stelt Nederlandse reacties op kijkerscomments voor.

Doorloopt de actieve kanalen, haalt per kanaal de recent gepubliceerde
video's op en leest de top-level comments via
``commentThreads.list`` (1 quota-unit per aanroep). Voor comments waarop
nog niet is gereageerd wordt een warme Nederlandse concept-reactie
(maximaal 3 zinnen) gegenereerd en in de ``engagement_queue`` gezet met
status ``pending_approval``; de Telegram-client krijgt een
goedkeuringsverzoek.

BELANGRIJK: er wordt NOOIT automatisch een reactie via de API geplaatst —
publicatie gebeurt alleen na menselijke goedkeuring.
"""

from __future__ import annotations

from typing import Any

from .base_agent import CHANNEL_IDS, BaseAgent

#: Aantal recente video's per kanaal waarvan comments worden gelezen.
RECENT_VIDEOS_PER_CHANNEL = 5

#: Maximaal aantal comments per video dat wordt verwerkt.
MAX_COMMENTS_PER_VIDEO = 20


class EngagementAgent(BaseAgent):
    """Maakt concept-reacties op kijkerscomments (alleen voorstellen)."""

    def run(self, limit_channels: int = 10) -> list[dict]:
        """Genereer concept-reacties voor alle actieve kanalen.

        Args:
            limit_channels: Maximaal aantal te verwerken kanalen.

        Returns:
            Lijst met drafts: ``channel_id``, ``video_id``, ``comment_id``,
            ``comment_author``, ``comment_text`` en ``draft_reply``.
        """
        drafts: list[dict] = []
        channels = self._active_channels(limit_channels)
        self._log_info(f"{len(channels)} actieve kanalen voor engagement")

        for channel_id in channels:
            try:
                drafts.extend(self._process_channel(channel_id))
            except Exception as exc:  # noqa: BLE001 - per-kanaal foutafhandeling
                self._alert_error(f"engagement voor kanaal {channel_id}", exc)
        self._log_info(f"{len(drafts)} concept-reacties aangemaakt")
        return drafts

    # ------------------------------------------------------------------
    # Per kanaal
    # ------------------------------------------------------------------
    def _active_channels(self, limit: int) -> list[str]:
        """Geef actieve kanaal-ID's terug (database leidend, fallback config)."""
        rows = self._db_select("channels", active=True, limit=limit)
        ids = [r.get("channel_id") for r in rows if r.get("channel_id")]
        if ids:
            return ids[:limit]
        self._log_info("Geen actieve kanalen in database; gebruik configuratie")
        return CHANNEL_IDS[:limit]

    def _process_channel(self, channel_id: str) -> list[dict]:
        drafts: list[dict] = []
        service = self._get_youtube_service(channel_id)
        videos = self._db_select(
            "videos",
            channel_id=channel_id,
            status="published",
            order="published_at",
            desc=True,
            limit=RECENT_VIDEOS_PER_CHANNEL,
        )
        for video in videos:
            youtube_video_id = video.get("youtube_video_id")
            if not youtube_video_id:
                continue
            try:
                drafts.extend(
                    self._process_video(service, channel_id, video, youtube_video_id)
                )
            except Exception as exc:  # noqa: BLE001 - per-video foutafhandeling
                self._alert_error(
                    f"engagement voor video {video.get('id')}", exc
                )
        return drafts

    def _process_video(
        self, service: Any, channel_id: str, video: dict, youtube_video_id: str
    ) -> list[dict]:
        drafts: list[dict] = []
        response = (
            service.commentThreads()
            .list(
                part="snippet",
                videoId=youtube_video_id,
                maxResults=MAX_COMMENTS_PER_VIDEO,
                order="relevance",
                textFormat="plainText",
            )
            .execute()
        )
        self.db.add_api_usage("youtube", 1)

        for item in response.get("items", []):
            top = (item.get("snippet") or {}).get("topLevelComment") or {}
            comment_id = top.get("id")
            snippet = top.get("snippet") or {}
            text = (snippet.get("textDisplay") or "").strip()
            author = snippet.get("authorDisplayName") or "kijker"
            if not comment_id or not text:
                continue
            # Idempotentie: niet twee keer een draft voor dezelfde comment.
            if self._db_select("engagement_queue", comment_id=comment_id, limit=1):
                continue
            draft_reply = self._draft_reply(channel_id, author, text)
            self._db_insert(
                "engagement_queue",
                {
                    "channel_id": channel_id,
                    "video_id": video.get("id"),
                    "comment_id": comment_id,
                    "comment_text": text,
                    "comment_author": author,
                    "draft_reply": draft_reply,
                    "status": "pending_approval",
                },
            )
            self._notify_approval(channel_id, video, comment_id, author, text, draft_reply)
            drafts.append(
                {
                    "channel_id": channel_id,
                    "video_id": video.get("id"),
                    "comment_id": comment_id,
                    "comment_author": author,
                    "comment_text": text,
                    "draft_reply": draft_reply,
                }
            )
            self._log_info(
                f"Concept-reactie aangemaakt voor comment {comment_id} "
                f"(kanaal {channel_id})"
            )
        return drafts

    # ------------------------------------------------------------------
    # Draft + notificatie
    # ------------------------------------------------------------------
    def _draft_reply(self, channel_id: str, author: str, text: str) -> str:
        """Genereer een warme Nederlandse concept-reactie (max 3 zinnen)."""
        ctx = self._channel_context(channel_id)
        prompt = self.niche.render_prompt(
            "templates/prompts/engagement.j2",
            reactie=text,
            auteur=author,
            toon=ctx["toon"],
            kanaal_naam=ctx["kanaal_naam"],
        )
        reply = self.llm.generate(
            prompt=prompt,
            system=(
                "Je bent een warme, professionele communitymanager. "
                "Geef alleen de reactie terug."
            ),
            max_tokens=200,
            temperature=0.7,
        )
        return reply.strip().strip("\"'“”")

    def _notify_approval(
        self,
        channel_id: str,
        video: dict,
        comment_id: str,
        author: str,
        text: str,
        draft_reply: str,
    ) -> None:
        """Vraag via Telegram om goedkeuring van de concept-reactie."""
        if self.telegram is None:
            return
        try:
            self.telegram.send_comment_approval(
                channel_id=channel_id,
                video_id=video.get("id"),
                comment_id=comment_id,
                auteur=author,
                reactie=text,
                concept_antwoord=draft_reply,
            )
        except Exception as exc:  # noqa: BLE001 - notificatie mag niet crashen
            self._log_error(f"Telegram goedkeuringsverzoek mislukt: {exc!r}")
