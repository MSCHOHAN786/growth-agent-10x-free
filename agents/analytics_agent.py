"""Analytics-agent: verzamelt YouTube-statistieken per kanaal.

Haalt voor recent gepubliceerde video's de statistieken op via
``videos.list`` (1 quota-unit per aanroep, max 50 video-ID's per keer),
slaat ze per video op in de ``analytics``-tabel en berekent een
weeksamenvatting: totale views, 7-daagse groei (%), best bekeken video en
aantal video's. De samenvatting wordt bewaard in ``niche_patterns`` onder
de pattern_key ``analytics:<ISO-jaar>-W<week>``.

Er wordt nergens medisch of financieel advies gegeven.
"""

from __future__ import annotations

from typing import Any

from .base_agent import BaseAgent

#: Maximaal aantal video-ID's per videos.list-aanroep.
MAX_IDS_PER_REQUEST = 50


class AnalyticsAgent(BaseAgent):
    """Verzamelt statistieken en berekent een weeksamenvatting per kanaal."""

    def run(self, channel_id: str) -> dict:
        """Haal statistieken op en sla een weeksamenvatting op.

        Args:
            channel_id: Kanaal-ID, bijv. ``psy_nl_01``.

        Returns:
            Dict met ``channel_id``, ``week``, ``total_views``,
            ``views_7d_growth_pct``, ``top_video`` (titel/views),
            ``video_count`` en ``generated_at``.
        """
        today = self._today()
        iso_year, iso_week, _ = today.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"

        videos = self._db_select(
            "videos",
            channel_id=channel_id,
            status="published",
            order="published_at",
            desc=True,
            limit=MAX_IDS_PER_REQUEST,
        )
        videos = [v for v in videos if v.get("youtube_video_id")]
        if not videos:
            self._log_info(f"Geen gepubliceerde video's voor {channel_id}")
            summary = self._empty_summary(channel_id, week_key)
            self._store_summary(channel_id, week_key, summary)
            return summary

        # Vorige meting per video (voor groeiberekening) — vóór insert ophalen.
        previous: dict[str, int] = {}
        for video in videos:
            rows = self._db_select(
                "analytics", video_id=video.get("id"),
                order="fetched_at", desc=True, limit=1,
            )
            if rows:
                previous[str(video.get("id"))] = int(rows[0].get("views") or 0)

        stats = self._fetch_statistics(channel_id, videos)

        total_views = 0
        prev_total = 0
        top_video = {"title": "", "views": 0}
        for video in videos:
            video_id = str(video.get("id"))
            stat = stats.get(str(video.get("youtube_video_id")), {})
            views = int(stat.get("viewCount") or 0)
            likes = int(stat.get("likeCount") or 0)
            comments = int(stat.get("commentCount") or 0)
            self._db_insert(
                "analytics",
                {
                    "channel_id": channel_id,
                    "video_id": video.get("id"),
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "fetched_at": today.isoformat(),
                },
            )
            total_views += views
            prev_total += previous.get(video_id, 0)
            if views > top_video["views"]:
                top_video = {"title": video.get("title", ""), "views": views}

        if prev_total > 0:
            growth_pct = round((total_views - prev_total) / prev_total * 100, 2)
        else:
            growth_pct = 0.0

        summary = {
            "channel_id": channel_id,
            "week": week_key,
            "total_views": total_views,
            "views_7d_growth_pct": growth_pct,
            "top_video": top_video,
            "video_count": len(videos),
            "generated_at": today.isoformat(),
        }
        self._store_summary(channel_id, week_key, summary)
        self._log_info(
            f"Analytics {channel_id} week {week_key}: {total_views} views "
            f"({growth_pct:+.2f}%), {len(videos)} video's"
        )
        return summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fetch_statistics(
        self, channel_id: str, videos: list[dict]
    ) -> dict[str, dict]:
        """Haal statistieken op via videos.list (max 50 ID's per aanroep)."""
        service = self._get_youtube_service(channel_id)
        stats: dict[str, dict] = {}
        ids = [str(v.get("youtube_video_id")) for v in videos]
        for chunk_start in range(0, len(ids), MAX_IDS_PER_REQUEST):
            chunk = ids[chunk_start : chunk_start + MAX_IDS_PER_REQUEST]
            response = (
                service.videos()
                .list(part="statistics", id=",".join(chunk))
                .execute()
            )
            self.db.add_api_usage("youtube", 1)
            for item in response.get("items", []):
                stats[str(item.get("id"))] = item.get("statistics") or {}
        return stats

    def _empty_summary(self, channel_id: str, week_key: str) -> dict:
        """Samenvatting voor een kanaal zonder gepubliceerde video's."""
        return {
            "channel_id": channel_id,
            "week": week_key,
            "total_views": 0,
            "views_7d_growth_pct": 0.0,
            "top_video": {"title": "", "views": 0},
            "video_count": 0,
            "generated_at": self._today().isoformat(),
        }

    def _store_summary(self, channel_id: str, week_key: str, summary: dict) -> None:
        """Bewaar de weeksamenvatting in niche_patterns (idempotent)."""
        self._db_upsert(
            "niche_patterns",
            {
                "pattern_key": f"analytics:{week_key}",
                "channel_id": channel_id,
                "pattern_value": summary,
                "hits": 1,
            },
            on_conflict="pattern_key,channel_id",
        )
