"""Research-agent: dagelijkse trendonderwerpen per kanaal (idempotent).

Genereert 5 trending video-onderwerpen per kanaal via de LLM-router en
cacht het resultaat per kanaal per dag in ``niche_patterns`` onder de
pattern_key ``research:<YYYY-MM-DD>``. Bij een tweede run op dezelfde dag
wordt de cache teruggegeven en wordt de LLM niet opnieuw aangeroepen.
"""

from __future__ import annotations

from typing import Any

from .base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """Zoekt trending psychologie-onderwerpen voor één kanaal."""

    def run(self, channel_id: str) -> list[dict]:
        """Genereer (of haal uit cache) 5 trending onderwerpen.

        Args:
            channel_id: Kanaal-ID, bijv. ``psy_nl_01``.

        Returns:
            Lijst van dicts met ``topic``, ``keywords``, ``angle`` en
            ``why_trending``.

        Raises:
            ValueError: Als de LLM-output geen geldige JSON-lijst bevat.
        """
        today = self._today()
        pattern_key = f"research:{today.isoformat()}"

        # Idempotentie: cache per kanaal per dag.
        cached = self._db_select(
            "niche_patterns",
            pattern_key=pattern_key,
            channel_id=channel_id,
            limit=1,
        )
        if cached:
            value: Any = cached[0].get("pattern_value")
            if isinstance(value, list) and value:
                self._log_info(
                    f"Cache hit voor {channel_id} ({pattern_key}): "
                    f"{len(value)} onderwerpen hergebruikt"
                )
                return value
            self._log_error(
                f"Cache-rij voor {channel_id} ({pattern_key}) is corrupt; "
                "genereer opnieuw"
            )

        ctx = self._channel_context(channel_id)
        prompt = self.niche.render_prompt(
            "templates/prompts/research.j2",
            niche_naam=ctx["niche_naam"],
            beschrijving=ctx["beschrijving"],
            doelgroep=ctx["doelgroep"],
            pijlers=ctx["pijlers"],
        )
        raw = self.llm.generate(
            prompt=prompt,
            system=(
                "Je bent een YouTube-trendonderzoeker. "
                "Antwoord ALLEEN met strikt geldige JSON."
            ),
            max_tokens=1500,
            temperature=0.7,
        )
        ideas = self._extract_json(raw)
        if not isinstance(ideas, list) or not ideas:
            raise ValueError(
                f"ResearchAgent: geen geldige JSON-lijst ontvangen voor {channel_id}"
            )

        self._db_insert(
            "niche_patterns",
            {
                "pattern_key": pattern_key,
                "channel_id": channel_id,
                "pattern_value": ideas,
                "hits": 1,
                "created_at": today.isoformat(),
            },
        )
        self._log_info(
            f"{len(ideas)} onderwerpen gegenereerd voor {channel_id} "
            f"en gecacht als {pattern_key}"
        )
        return ideas
