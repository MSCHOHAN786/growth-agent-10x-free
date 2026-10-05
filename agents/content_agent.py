"""Content-agent: titels + volledig videoscript per onderwerp.

Rendert ``templates/prompts/content.j2`` met het gekozen onderwerp en de
kanaalcontext, roept de LLM aan (max 3000 tokens) en parseert de output in
5 Nederlandse videotitels plus een volledig script van 900–1200 woorden
(HOOK, hoofdstukken, praktische tips, CTA). Het resultaat wordt als
``draft`` opgeslagen in de ``videos``-tabel.
"""

from __future__ import annotations

import re
from typing import Any

from .base_agent import BaseAgent


class ContentAgent(BaseAgent):
    """Schrijft titels en script voor één video-onderwerp."""

    def run(self, channel_id: str, topic: str) -> dict:
        """Genereer 5 titels + script en sla de video op als draft.

        Args:
            channel_id: Kanaal-ID, bijv. ``psy_nl_01``.
            topic: Het onderwerp waarover de video gaat.

        Returns:
            Dict met ``video_id``, ``ideas`` (5 titels) en ``script``.
        """
        ctx = self._channel_context(channel_id)
        prompt = self.niche.render_prompt(
            "templates/prompts/content.j2",
            onderwerp=topic,
            niche_naam=ctx["niche_naam"],
            toon=ctx["toon"],
            pijlers=ctx["pijlers"],
            doelgroep=ctx["doelgroep"],
        )
        raw = self.llm.generate(
            prompt=prompt,
            system=(
                "Je bent een ervaren Nederlandstalige scriptschrijver voor "
                "psychologie-YouTubevideo's. Volg de gevraagde structuur exact."
            ),
            max_tokens=3000,
            temperature=0.8,
        )

        ideas, script = self._parse_content(raw)
        if not ideas:
            ideas = [topic]
        if not script.strip():
            script = raw.strip()

        row = self._db_insert(
            "videos",
            {
                "channel_id": channel_id,
                "topic": topic,
                "title": ideas[0],
                "script": script,
                "status": "draft",
                "created_at": self._today().isoformat(),
            },
        )
        video_id = row.get("id")
        self._log_info(
            f"Video {video_id} als draft opgeslagen voor {channel_id}: "
            f"'{ideas[0]}' ({len(script.split())} woorden script)"
        )
        return {"video_id": video_id, "ideas": ideas, "script": script}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def _parse_content(self, raw: str) -> tuple[list[str], str]:
        """Splits LLM-output in een titellijst en het script.

        Verwacht de structuur ``TITELS:`` (genummerde regels) gevolgd door
        ``SCRIPT:``. Alles wat niet matcht valt terug op ruwe tekst.
        """
        titles: list[str] = []
        titles_section = re.search(
            r"TITELS\s*:(.*?)(?:SCRIPT\s*:|$)", raw, re.S | re.IGNORECASE
        )
        if titles_section:
            for line in titles_section.group(1).splitlines():
                match = re.match(r"\s*\d+\s*[.)\-:]\s*(.+)", line.strip())
                if match:
                    title = match.group(1).strip().strip("\"'“”")
                    if title:
                        titles.append(title)
        script_match = re.search(r"SCRIPT\s*:(.*)", raw, re.S | re.IGNORECASE)
        script = script_match.group(1).strip() if script_match else raw.strip()
        return titles[:5], script
