"""Telegram admin client: notifications, approval buttons, error alerts.

Uses ``python-telegram-bot`` v20+ (async) under the hood but exposes a plain
synchronous API: every public method works with ``asyncio.run`` (or a worker
thread when called from inside an existing event loop), so GitHub Actions
steps and Colab cells can call it without asyncio boilerplate.

All network calls are wrapped in try/except and return ``bool`` — Telegram
must never crash a worker.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import html
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _esc(text: Any) -> str:
    """HTML-escape text for Telegram parse_mode='HTML'."""
    return html.escape("" if text is None else str(text))


class TelegramClient:
    """Synchronous wrapper around ``telegram.Bot``.

    Args:
        token: Bot token. Defaults to ``TELEGRAM_BOT_TOKEN`` env.
        admin_chat_id: Default destination chat. Defaults to
            ``TELEGRAM_ADMIN_CHAT_ID`` env.
    """

    def __init__(
        self, token: Optional[str] = None, admin_chat_id: Optional[str] = None
    ) -> None:
        self.token = (token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.admin_chat_id = (
            admin_chat_id or os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
        ).strip()
        self._bot: Any = None

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #
    def _bot_or_none(self) -> Optional[Any]:
        """Build the telegram.Bot lazily; return None (with a log) when unusable."""
        if self._bot is not None:
            return self._bot
        if not self.token:
            logger.error("TELEGRAM_BOT_TOKEN is not set; Telegram disabled")
            return None
        try:
            from telegram import Bot

            self._bot = Bot(token=self.token)
            return self._bot
        except ImportError:
            logger.error("python-telegram-bot is not installed; Telegram disabled")
            return None

    @staticmethod
    def _run(coro: Any) -> Any:
        """Run an async coroutine from sync code, even inside a running loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    def _target(self, chat_id: Optional[str]) -> Optional[str]:
        target = (chat_id or self.admin_chat_id or "").strip()
        if not target:
            logger.error("No chat_id and no TELEGRAM_ADMIN_CHAT_ID configured")
            return None
        return target

    # ------------------------------------------------------------------ #
    # Primitives                                                           #
    # ------------------------------------------------------------------ #
    def send_message(
        self,
        chat_id: Optional[str],
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Any] = None,
    ) -> bool:
        """Send a message.

        Args:
            chat_id: Destination; falls back to the admin chat id.
            text: Message body (HTML by default).
            parse_mode: Telegram parse mode.
            reply_markup: Optional ``InlineKeyboardMarkup``.

        Returns:
            True on success, False otherwise.
        """
        target = self._target(chat_id)
        bot = self._bot_or_none()
        if target is None or bot is None:
            return False
        try:
            self._run(
                bot.send_message(
                    chat_id=target,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            )
            logger.info("Telegram message sent to %s", target)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram send_message failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Approvals                                                            #
    # ------------------------------------------------------------------ #
    def send_approval_request(self, video: dict) -> bool:
        """Ask the admin to approve/reject/edit a video, with inline buttons.

        Args:
            video: Must contain ``id``; ``title`` and ``channel_id``/``channel``
                are shown when present.

        Returns:
            True if the message was sent.
        """
        video_id = str(video.get("id", "")).strip()
        if not video_id:
            logger.error("send_approval_request: video dict lacks 'id'")
            return False
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            logger.error("python-telegram-bot is not installed; Telegram disabled")
            return False
        title = _esc(video.get("title") or "(zonder titel)")
        channel = _esc(video.get("channel") or video.get("channel_id") or "?")
        text = (
            "🎬 <b>Nieuwe video ter goedkeuring</b>\n\n"
            f"<b>Titel:</b> {title}\n"
            f"<b>Kanaal:</b> {channel}\n"
            f"<b>ID:</b> <code>{_esc(video_id)}</code>"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Goedkeuren", callback_data=f"approve:video:{video_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Afkeuren", callback_data=f"reject:video:{video_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ Aanpassen", callback_data=f"edit:video:{video_id}"
                    )
                ],
            ]
        )
        return self.send_message(None, text, reply_markup=keyboard)

    def send_comment_approval(
        self, item: Optional[dict] = None, **kwargs: Any
    ) -> bool:
        """Ask the admin to approve/reject an engagement (comment/reply) item.

        Accepts either a single ``item`` dict **or** keyword arguments (the
        agents call it with Dutch keywords)::

            send_comment_approval(channel_id=..., video_id=...,
                                 comment_id=..., auteur=..., reactie=...,
                                 concept_antwoord=...)

        Dutch aliases are mapped: ``auteur`` → author, ``reactie`` → text,
        ``concept_antwoord`` → draft reply, ``comment_id`` → id.

        Args:
            item: Dict with ``id``; ``author``, ``text`` and ``video_id``
                shown when present.
            **kwargs: Same fields as keywords.

        Returns:
            True if the message was sent.
        """
        data: dict[str, Any] = dict(item or {})
        # Map Dutch keyword aliases used by the engagement agent.
        alias_map = {
            "auteur": "author",
            "reactie": "text",
            "concept_antwoord": "draft",
            "comment_id": "id",
        }
        for key, value in kwargs.items():
            data[alias_map.get(key, key)] = value
        item_id = str(data.get("id", "")).strip()
        if not item_id:
            logger.error("send_comment_approval: item dict lacks 'id'")
            return False
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except ImportError:
            logger.error("python-telegram-bot is not installed; Telegram disabled")
            return False
        author = _esc(data.get("author") or "(onbekend)")
        body = _esc((data.get("text") or "")[:500])
        draft = _esc((data.get("draft") or data.get("draft_reply") or "")[:800])
        video_ref = _esc(data.get("video_id") or "?")
        text = (
            "💬 <b>Reactie ter goedkeuring</b>\n\n"
            f"<b>Auteur:</b> {author}\n"
            f"<b>Video:</b> <code>{video_ref}</code>\n"
            f"<b>Tekst:</b> {body}"
        )
        if draft:
            text += f"\n\n<b>Concept-antwoord:</b>\n{draft}"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Goedkeuren", callback_data=f"approve:comment:{item_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Afkeuren", callback_data=f"reject:comment:{item_id}"
                    )
                ],
            ]
        )
        return self.send_message(None, text, reply_markup=keyboard)

    def handle_callback(self, callback_data: str, supabase: Any) -> str:
        """Handle an inline-button callback: record the decision in Supabase.

        Args:
            callback_data: ``"<action>:<kind>:<id>"``, e.g.
                ``"approve:video:abc123"``. Action ``edit`` returns an
                instruction instead of recording a decision.
            supabase: :class:`core.supabase_client.SupabaseClient` instance.

        Returns:
            Human-readable Dutch confirmation text for the admin.
        """
        parts = (callback_data or "").split(":")
        if len(parts) != 3:
            logger.warning("Ignoring malformed callback_data: %r", callback_data)
            return "❌ Onbekende knop — niets gedaan."
        action, kind, item_id = parts
        if kind not in ("video", "comment"):
            return "❌ Onbekend type — niets gedaan."
        label = "video" if kind == "video" else "reactie"
        if action == "edit":
            return (
                f"✏️ Pas de {label} aan in het dashboard en keur hem daarna "
                "opnieuw goed."
            )
        if action not in ("approve", "reject"):
            return "❌ Onbekende actie — niets gedaan."
        decision = "approve" if action == "approve" else "reject"
        try:
            supabase.decide_approval(item_id, kind, decision)
        except Exception as exc:  # noqa: BLE001
            logger.error("handle_callback decide_approval failed: %s", exc)
            return f"⚠️ Er ging iets mis bij het opslaan: {_esc(exc)}"
        if decision == "approve":
            return f"✅ {label.capitalize()} <code>{_esc(item_id)}</code> goedgekeurd."
        return f"❌ {label.capitalize()} <code>{_esc(item_id)}</code> afgekeurd."

    # ------------------------------------------------------------------ #
    # Reports & alerts                                                     #
    # ------------------------------------------------------------------ #
    def send_report(self, title: str, lines: list[str]) -> bool:
        """Send a titled multi-line report to the admin chat.

        Args:
            title: Report heading.
            lines: Body lines (already formatted; HTML-escaped automatically).

        Returns:
            True if the message was sent.
        """
        if not title:
            logger.error("send_report: title is required")
            return False
        body = "\n".join(f"• {_esc(line)}" for line in lines)
        text = f"📊 <b>{_esc(title)}</b>\n\n{body}" if body else f"📊 <b>{_esc(title)}</b>"
        return self.send_message(None, text)

    def send_error_alert(self, context: str, error: Exception) -> bool:
        """Send a short error alert to the admin chat.

        Args:
            context: Where the error happened (worker/step name).
            error: The exception instance.

        Returns:
            True if the message was sent.
        """
        text = (
            "🚨 <b>Foutmelding</b>\n\n"
            f"<b>Context:</b> {_esc(context)}\n"
            f"<b>Fout:</b> <code>{_esc(type(error).__name__)}: {error}</code>"
        )
        return self.send_message(None, text)
