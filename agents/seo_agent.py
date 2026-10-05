"""SEO-agent: geoptimaliseerde metadata voor een video.

Rendert ``templates/prompts/seo.j2`` met titel en script, verwacht strikt
JSON terug (titel ≤ 100 tekens, Nederlandse beschrijving met hook +
hoofdstukindeling + 3 hashtags, 12 tags, vastgezette reactie) en werkt de
rij in de ``videos``-tabel bij.
"""

from __future__ import annotations

from .base_agent import BaseAgent


class SEOAgent(BaseAgent):
    """Genereert SEO-metadata voor een bestaande video-draft."""

    def run(self, video_id: str) -> dict:
        """Genereer SEO-metadata en werk de videorij bij.

        Args:
            video_id: Primaire sleutel van de rij in de ``videos``-tabel.

        Returns:
            Dict met ``title``, ``description``, ``tags`` en
            ``pinned_comment``.

        Raises:
            ValueError: Als de video niet bestaat of de LLM-output geen
                geldige JSON bevat.
        """
        rows = self._db_select("videos", id=video_id, limit=1)
        if not rows:
            raise ValueError(f"Video niet gevonden: {video_id}")
        video = rows[0]

        ctx = self._channel_context(video.get("channel_id", ""))
        prompt = self.niche.render_prompt(
            "templates/prompts/seo.j2",
            titel=video.get("title", ""),
            script=video.get("script", ""),
            niche_naam=ctx["niche_naam"],
        )
        raw = self.llm.generate(
            prompt=prompt,
            system=(
                "Je bent een YouTube-SEO-specialist. "
                "Antwoord ALLEEN met strikt geldige JSON."
            ),
            max_tokens=1500,
            temperature=0.6,
        )
        seo = self._extract_json(raw)
        if not isinstance(seo, dict):
            raise ValueError(
                f"SEOAgent: geen geldig JSON-object ontvangen voor video {video_id}"
            )

        # Titel mag maximaal 100 tekens zijn (YouTube-limiet).
        title = str(seo.get("title", video.get("title", ""))).strip()
        if len(title) > 100:
            cut = title[:100].rsplit(" ", 1)[0] or title[:97]
            title = cut.rstrip() + "…"

        tags = seo.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        tags = [str(t).strip() for t in tags if str(t).strip()][:12]

        seo_payload = {
            "title": title,
            "description": str(seo.get("description", "")).strip(),
            "tags": tags,
            "pinned_comment": str(seo.get("pinned_comment", "")).strip(),
        }
        self._db_update(
            "videos",
            {
                "title": seo_payload["title"],
                "description": seo_payload["description"],
                "tags": seo_payload["tags"],
            },
            id=video_id,
        )
        self._log_info(f"SEO-metadata bijgewerkt voor video {video_id}: '{title}'")
        return seo_payload
