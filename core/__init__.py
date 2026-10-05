"""core — shared foundation for Growth Agent 10x Free.

Everything that talks to the outside world or is reused by every workflow
lives here, so all agents, workers and crons share one contract:

- :mod:`core.crypto` — Fernet token encryption (YouTube OAuth refresh tokens).
- :mod:`core.supabase_client` — all database access (channels, videos, jobs,
  approvals, engagement, usage counters).
- :mod:`core.llm_router` — free-tier LLM access with automatic failover:
  groq → gemini → cohere.
- :mod:`core.telegram_client` — admin notifications and approval buttons.
- :mod:`core.niche_engine` — channel + niche library config and prompt rendering.

Public API is re-exported below so consumers can do::

    from core import SupabaseClient, LLMRouter, TelegramClient, NicheEngine
"""

from core.crypto import decrypt_token, encrypt_token, generate_key, get_encryption_key
from core.llm_router import LLMExhaustedError, LLMResponse, LLMRouter
from core.niche_engine import NicheEngine
from core.supabase_client import SupabaseClient
from core.telegram_client import TelegramClient

__version__ = "1.0.0"

__all__ = [
    "SupabaseClient",
    "LLMRouter",
    "LLMResponse",
    "LLMExhaustedError",
    "TelegramClient",
    "NicheEngine",
    "encrypt_token",
    "decrypt_token",
    "get_encryption_key",
    "generate_key",
    "__version__",
]
