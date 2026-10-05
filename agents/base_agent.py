"""Basis-agent voor het Growth Agent 10x Free systeem (Group C).

Bevat de gedeelde bouwstenen voor alle 7 agents:

- gestructureerde logging (:meth:`BaseAgent._log_info` / :meth:`BaseAgent._log_error`)
- Telegram-foutmeldingen (:meth:`BaseAgent._alert_error`)
- YouTube API-service via OAuth refresh token (:meth:`BaseAgent._get_youtube_service`)
- robuuste JSON-extractie uit LLM-output (:meth:`BaseAgent._extract_json`)
- kleine Supabase-helpers (:meth:`BaseAgent._db_select` etc.)

Verwacht contract van Group B (``core``):

- ``SupabaseClient``: ``.table(naam)`` geeft een supabase-py-achtige query
  builder (``select/insert/update/upsert/eq/lte/order/limit/execute``) plus
  ``.add_api_usage(service, units)`` en ``.get_api_usage(service, dag) -> int``.
- ``LLMRouter``: ``.generate(prompt=..., system=..., max_tokens=...,
  temperature=...) -> str``. Alle LLM-aanroepen lopen hierlangs.
- ``NicheEngine``: ``.render_prompt(template_pad, **context) -> str``.
- ``TelegramClient``: ``.send_alert(tekst)`` en
  ``.send_comment_approval(...)``.
- ``decrypt_token(encrypted: str) -> str`` voor OAuth refresh tokens.

Zolang ``core`` nog niet is geleverd, vallen de imports terug op
``typing.Any``-aliassen zodat type hints blijven werken; bij daadwerkelijk
gebruik zonder Group B volgt een duidelijke ``RuntimeError``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Optional

try:  # Group B levert core; importeer de echte klassen zodra beschikbaar.
    from core import (  # type: ignore[import-not-found]
        LLMRouter,
        NicheEngine,
        SupabaseClient,
        TelegramClient,
        decrypt_token,
    )
except ImportError:  # pragma: no cover - core (Group B) nog niet geleverd
    LLMRouter = Any  # type: ignore[assignment,misc]
    NicheEngine = Any  # type: ignore[assignment,misc]
    SupabaseClient = Any  # type: ignore[assignment,misc]
    TelegramClient = Any  # type: ignore[assignment,misc]

    def decrypt_token(_value: str) -> str:  # type: ignore[misc]
        """Fallback: roep nooit aan zonder Group B core."""
        raise RuntimeError(
            "core.decrypt_token is niet beschikbaar: de Group B core-module "
            "is nog niet geleverd."
        )


#: Kanaal-ID's van de 10 Nederlandstalige psychologiekanalen.
CHANNEL_IDS: list[str] = [f"psy_nl_{i:02d}" for i in range(1, 11)]

#: Dagelijkse YouTube API-quota per project (units).
YOUTUBE_DAILY_QUOTA: int = 10_000

#: Kosten van één video-upload in quota-units.
YOUTUBE_UPLOAD_COST: int = 1_600


class BaseAgent(ABC):
    """Abstracte basis voor alle Growth Agent 10x Free agents.

    Args:
        llm: Router voor alle (gratis) LLM-aanroepen.
        db: Supabase-client van Group B.
        niche: Niche-engine (prompt-rendering per kanaal).
        telegram: Optionele Telegram-client voor alerts en approvals.
    """

    def __init__(
        self,
        llm: LLMRouter,
        db: SupabaseClient,
        niche: NicheEngine,
        telegram: Optional[TelegramClient] = None,
    ) -> None:
        self.llm = llm
        self.db = db
        self.niche = niche
        self.telegram = telegram
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    # ------------------------------------------------------------------
    # Verplichte entrypoint
    # ------------------------------------------------------------------
    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Voer de taak van deze agent uit."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _log_info(self, message: str) -> None:
        """Log een informatieve melding met de agentnaam als prefix."""
        self._logger.info("[%s] %s", self.__class__.__name__, message)

    def _log_error(self, message: str) -> None:
        """Log een foutmelding met de agentnaam als prefix."""
        self._logger.error("[%s] %s", self.__class__.__name__, message)

    def _alert_error(self, context: str, exc: BaseException) -> None:
        """Stuur bij een fout een Telegram-alert (als een client is gezet).

        Args:
            context: Korte omschrijving van wat er misging.
            exc: De opgetreden uitzondering.
        """
        self._log_error(f"{context}: {exc!r}")
        if self.telegram is None:
            return
        text = f"⚠️ GrowthAgent {self.__class__.__name__} — {context}: {exc}"
        try:
            if hasattr(self.telegram, "send_alert"):
                self.telegram.send_alert(text)
            elif hasattr(self.telegram, "send_message"):
                self.telegram.send_message(text)
            else:
                self._logger.warning("Telegram-client heeft geen send_alert/send_message")
        except Exception as alert_exc:  # alerting mag nooit zelf crashen
            self._logger.warning("Telegram-alert mislukt: %r", alert_exc)

    # ------------------------------------------------------------------
    # Tijd
    # ------------------------------------------------------------------
    @staticmethod
    def _today() -> date:
        """Geef de datum van vandaag terug."""
        return date.today()

    # ------------------------------------------------------------------
    # LLM-output helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(raw: str) -> Any:
        """Haal JSON uit LLM-tekst, ook als er code fences omheen staan.

        Args:
            raw: Ruwe LLM-output.

        Returns:
            Het geparste JSON-object (dict of list).

        Raises:
            ValueError: Als er geen geldige JSON gevonden kan worden.
        """
        text = raw.strip()
        # Verwijder markdown code fences (```json ... ``` of ``` ... ```).
        fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.S | re.I)
        if fence:
            text = fence.group(1).strip()
        # Zoek het buitenste JSON-object of -array.
        start_obj, start_arr = text.find("{"), text.find("[")
        starts = [s for s in (start_obj, start_arr) if s != -1]
        if not starts:
            raise ValueError("Geen JSON gevonden in LLM-output")
        start = min(starts)
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        end = text.rfind(closer)
        if end <= start:
            raise ValueError("Geen JSON gevonden in LLM-output")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM-output bevat geen geldige JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # Supabase-helpers (supabase-py-stijl query builder van Group B)
    # ------------------------------------------------------------------
    def _db_select(
        self,
        table: str,
        order: Optional[str] = None,
        desc: bool = False,
        limit: Optional[int] = None,
        **filters: Any,
    ) -> list[dict]:
        """Selecteer rijen uit een tabel.

        Filters zijn ``kolom=waarde`` paren; een sleutel met suffix
        ``__lte`` wordt een kleiner-dan-of-gelijk filter. ``None`` als
        waarde betekent IS NULL.
        """
        query = self.db.table(table).select("*")
        for key, value in filters.items():
            if key.endswith("__lte"):
                query = query.lte(key[: -len("__lte")], value)
            elif value is None:
                query = query.is_(key, "null")
            else:
                query = query.eq(key, value)
        if order:
            query = query.order(order, desc=desc)
        if limit is not None:
            query = query.limit(limit)
        response = query.execute()
        return list(response.data or [])

    def _db_insert(self, table: str, row: dict) -> dict:
        """Voeg één rij toe en geef de aangemaakte rij terug."""
        response = self.db.table(table).insert(row).execute()
        data = list(response.data or [])
        return data[0] if data else {}

    def _db_update(self, table: str, values: dict, **filters: Any) -> list[dict]:
        """Werk rijen bij die aan de filters voldoen."""
        query = self.db.table(table).update(values)
        for key, value in filters.items():
            if value is None:
                query = query.is_(key, "null")
            else:
                query = query.eq(key, value)
        response = query.execute()
        return list(response.data or [])

    def _db_upsert(self, table: str, row: dict, on_conflict: str) -> dict:
        """Voeg een rij toe of werk de bestaande bij (conflict-kolommen)."""
        response = self.db.table(table).upsert(row, on_conflict=on_conflict).execute()
        data = list(response.data or [])
        return data[0] if data else {}

    # ------------------------------------------------------------------
    # Gedeelde YouTube-helper (ook gebruikt door Publish- en EngagementAgent)
    # ------------------------------------------------------------------
    def _get_youtube_service(self, channel_id: str) -> Any:
        """Bouw een geauthenticeerde YouTube Data API v3-service.

        Leest het versleutelde refresh token uit
        ``channels.youtube_oauth_encrypted`` (via ``decrypt_token``) en de
        OAuth client-gegevens uit
        ``$YOUTUBE_CLIENT_SECRETS_DIR/<channel_id>.json``. Dat
        client-secrets-bestand wordt nooit gecommit.

        Args:
            channel_id: Kanaal-ID (bijv. ``psy_nl_01``).

        Returns:
            Een ``googleapiclient`` YouTube-resource.

        Raises:
            RuntimeError: Als de Google-clientbibliotheken ontbreken.
            ValueError: Als kanaal of token ontbreekt.
            FileNotFoundError: Als het client-secrets-bestand ontbreekt.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google API-clientbibliotheken ontbreken; installeer ze met: "
                "pip install google-api-python-client google-auth"
            ) from exc

        rows = self._db_select("channels", channel_id=channel_id, limit=1)
        if not rows:
            raise ValueError(f"Geen kanaal gevonden in database: {channel_id}")
        encrypted = rows[0].get("youtube_oauth_encrypted")
        if not encrypted:
            raise ValueError(f"Geen youtube_oauth_encrypted voor kanaal {channel_id}")
        refresh_token = decrypt_token(encrypted)

        secrets_dir = os.environ.get(
            "YOUTUBE_CLIENT_SECRETS_DIR",
            os.path.expanduser("~/.config/growth-agent/client_secrets"),
        )
        secrets_path = os.path.join(secrets_dir, f"{channel_id}.json")
        if not os.path.isfile(secrets_path):
            raise FileNotFoundError(
                f"Client-secrets-bestand ontbreekt: {secrets_path} "
                "(zet YOUTUBE_CLIENT_SECRETS_DIR correct)"
            )
        with open(secrets_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        section = raw.get("installed") or raw.get("web") or raw

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=section["client_id"],
            client_secret=section["client_secret"],
        )
        creds.refresh(Request())
        self._log_info(f"YouTube-service opgebouwd voor kanaal {channel_id}")
        return build("youtube", "v3", credentials=creds)

    # ------------------------------------------------------------------
    # Kanaal-context voor prompt-rendering
    # ------------------------------------------------------------------
    def _channel_context(self, channel_id: str) -> dict:
        """Bouw de template-context uit de kanaalrij in de database."""
        rows = self._db_select("channels", channel_id=channel_id, limit=1)
        channel = rows[0] if rows else {}
        pijlers = channel.get("niche_pijlers") or []
        return {
            "niche_naam": channel.get("niche_naam") or channel.get("name") or channel_id,
            "beschrijving": channel.get("niche_beschrijving") or "",
            "doelgroep": channel.get("niche_doelgroep") or "",
            "pijlers": ", ".join(pijlers) if isinstance(pijlers, list) else str(pijlers),
            "toon": channel.get("toon") or "warm en deskundig",
            "kanaal_naam": channel.get("niche_naam") or channel.get("name") or channel_id,
        }
