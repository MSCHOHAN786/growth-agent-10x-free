"""Agents van het Growth Agent 10x Free systeem (Group C).

Bevat de 7 agents voor de dagelijkse YouTube-automatisering van de
10 Nederlandstalige psychologiekanalen (``psy_nl_01`` t/m ``psy_nl_10``):

- :class:`BaseAgent` — gedeelde basis (logging, Telegram-alerts,
  YouTube-service, JSON-extractie, Supabase-helpers).
- :class:`ResearchAgent` — dagelijkse trending onderwerpen per kanaal
  (idempotent via ``niche_patterns``-cache).
- :class:`ContentAgent` — 5 titels + volledig videoscript, opgeslagen als draft.
- :class:`SEOAgent` — geoptimaliseerde Nederlandse metadata als JSON.
- :class:`PublishAgent` — uploadt alleen goedgekeurde video's, met
  YouTube-quota-bewaking (max 10.000 units/dag).
- :class:`EngagementAgent` — stelt concept-reacties op comments voor;
  publicatie alleen na menselijke goedkeuring (nooit automatisch).
- :class:`AnalyticsAgent` — statistieken per kanaal + weeksamenvatting.

Alle LLM-aanroepen lopen via ``LLMRouter.generate``; publicatie gebeurt
alleen bij ``approval_queue``-beslissing ``'approved'``.
"""

from .base_agent import BaseAgent
from .research_agent import ResearchAgent
from .content_agent import ContentAgent
from .seo_agent import SEOAgent
from .publish_agent import PublishAgent
from .engagement_agent import EngagementAgent
from .analytics_agent import AnalyticsAgent

__all__ = [
    "BaseAgent",
    "ResearchAgent",
    "ContentAgent",
    "SEOAgent",
    "PublishAgent",
    "EngagementAgent",
    "AnalyticsAgent",
]
