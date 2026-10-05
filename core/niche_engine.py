"""Niche engine: channel + niche library config and Jinja2 prompt rendering.

Reads ``config/channels.yaml`` and ``config/niche_library.yaml`` (relative to
the repo root) once at construction. Key lookups are tolerant of both Dutch
and English key spellings (``toon``/``tone``, ``pijlers``/``pillars``,
``doelgroep``/``audience``), since config files may be authored in either.

Expected shapes (a channel points at a niche by key)::

    # config/channels.yaml
    channels:
      - id: psy_nl_01
        name: "…"
        niche: angst          # key into niche_library.yaml
        status: active

    # config/niche_library.yaml
    niches:
      angst:
        niche: "Angst & piekeren"
        doelgroep: "…"
        toon: "…"
        pijlers: ["…", "…"]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """Repo root = parent of the ``core/`` package directory."""
    return Path(__file__).resolve().parent.parent


def _pick(mapping: dict, *keys: str, default: Any = None) -> Any:
    """Return the first present key's value from a dict."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


class NicheEngine:
    """Loads channel + niche config and renders Jinja2 prompt templates.

    Args:
        library_path: Path to the niche library YAML, relative to the repo
            root unless absolute.
        channels_path: Path to the channels YAML, relative to the repo root
            unless absolute.

    Missing files are tolerated (empty configs); lookups then raise a
    helpful :class:`KeyError`.
    """

    def __init__(
        self,
        library_path: str = "config/niche_library.yaml",
        channels_path: str = "config/channels.yaml",
    ) -> None:
        root = _repo_root()
        lib_file = Path(library_path)
        ch_file = Path(channels_path)
        self.library_file = lib_file if lib_file.is_absolute() else root / lib_file
        self.channels_file = ch_file if ch_file.is_absolute() else root / ch_file

        raw_library = self._load_yaml(self.library_file)
        raw_channels = self._load_yaml(self.channels_file)

        self.niches: dict[str, dict] = self._index_niches(raw_library)
        self.channels: dict[str, dict] = self._index_channels(raw_channels)
        logger.info(
            "NicheEngine loaded: %d niches, %d channels",
            len(self.niches),
            len(self.channels),
        )

    # ------------------------------------------------------------------ #
    # Loading                                                              #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file; return {} when missing (log), raise on bad YAML."""
        if not path.exists():
            logger.warning("Config file not found (using empty config): %s", path)
            return {}
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "The 'pyyaml' package is required to read config files. "
                "Install it with: pip install pyyaml"
            ) from exc
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001 - malformed YAML
            logger.error("Failed to parse YAML %s: %s", path, exc)
            raise
        if not isinstance(data, dict):
            raise ValueError(f"Top-level YAML in {path} must be a mapping")
        return data

    @staticmethod
    def _index_niches(raw: dict) -> dict[str, dict]:
        """Normalise the niche library to {niche_key: niche_dict}."""
        niches = raw.get("niches") or raw.get("sub_niches") or {}
        if isinstance(niches, list):  # allow a list with 'key'/'id' per entry
            indexed: dict[str, dict] = {}
            for entry in niches:
                if isinstance(entry, dict):
                    key = str(entry.get("key") or entry.get("id") or "")
                    if key:
                        indexed[key] = entry
            return indexed
        return {str(k): (v or {}) for k, v in niches.items()} if isinstance(niches, dict) else {}

    @staticmethod
    def _index_channels(raw: dict) -> dict[str, dict]:
        """Normalise channels to {channel_id: channel_dict}; lists indexed by id."""
        channels = raw.get("channels") or {}
        if isinstance(channels, list):
            indexed: dict[str, dict] = {}
            for entry in channels:
                if isinstance(entry, dict) and entry.get("id"):
                    indexed[str(entry["id"])] = entry
            return indexed
        return {str(k): (v or {}) for k, v in channels.items()} if isinstance(channels, dict) else {}

    # ------------------------------------------------------------------ #
    # Lookups                                                              #
    # ------------------------------------------------------------------ #
    def list_channels(self) -> list[dict]:
        """Return all configured channels as a list of dicts."""
        return list(self.channels.values())

    def get_channel(self, channel_id: str) -> dict:
        """Return the channel config dict for a channel id.

        Args:
            channel_id: e.g. ``psy_nl_01``.

        Raises:
            KeyError: If the channel id is unknown, listing available ids.
        """
        try:
            return self.channels[channel_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.channels)) or "(none loaded)"
            raise KeyError(
                f"Unknown channel_id '{channel_id}'. Available: {available}. "
                f"Check {self.channels_file}."
            ) from exc

    def get_niche(self, channel_id: str) -> dict:
        """Return the sub-niche entry for a channel.

        Args:
            channel_id: Channel id; its ``niche`` key selects the library entry.

        Raises:
            KeyError: If the channel or its niche key is unknown.
        """
        channel = self.get_channel(channel_id)
        niche_key = str(_pick(channel, "niche", "sub_niche", "subniche", default="") or "")
        if not niche_key:
            raise KeyError(
                f"Channel '{channel_id}' has no 'niche' key set in {self.channels_file}."
            )
        try:
            return self.niches[niche_key]
        except KeyError as exc:
            available = ", ".join(sorted(self.niches)) or "(none loaded)"
            raise KeyError(
                f"Niche '{niche_key}' (from channel '{channel_id}') not found in "
                f"{self.library_file}. Available: {available}."
            ) from exc

    def get_tone(self, channel_id: str) -> str:
        """Return the tone ('toon_richtlijnen'/'toon'/'tone') for a channel's niche."""
        niche = self.get_niche(channel_id)
        return str(_pick(niche, "toon_richtlijnen", "toon", "tone", default="") or "")

    def list_pillars(self, channel_id: str) -> list[str]:
        """Return the content pillars for a channel's niche."""
        niche = self.get_niche(channel_id)
        pillars = _pick(niche, "content_pijlers", "pijlers", "pillars", default=[]) or []
        return [str(p) for p in pillars] if isinstance(pillars, list) else [str(pillars)]

    # ------------------------------------------------------------------ #
    # Prompt rendering                                                     #
    # ------------------------------------------------------------------ #
    def render_prompt(
        self, template_str: str, channel_id: Optional[str] = None, **kwargs: Any
    ) -> str:
        """Render a Jinja2 template with channel/niche context.

        ``template_str`` may be either raw Jinja2 source **or** a path to a
        ``.j2`` file (relative paths resolve against the repo root) — the
        agents call it with paths like ``"templates/prompts/research.j2"``.

        ``channel_id`` may be passed positionally or as a ``channel_id`` /
        ``channel`` keyword. When omitted, only the explicit ``kwargs`` are
        used as context (no channel/niche auto-context).

        The template context always contains (Dutch + English aliases):
        ``niche``, ``doelgroep``/``audience``, ``toon``/``tone``,
        ``pijlers``/``pillars``, ``channel`` (the channel dict), plus any
        extra ``kwargs`` (which take precedence).

        Args:
            template_str: Jinja2 template source or path to a ``.j2`` file.
            channel_id: Channel id to build context for (optional).
            **kwargs: Extra template variables.

        Returns:
            The rendered string.

        Raises:
            ValueError: If the template is empty or the file cannot be read.
            RuntimeError: If ``jinja2`` is not installed.
        """
        if not template_str or not template_str.strip():
            raise ValueError("template_str must be a non-empty string")
        # Allow callers to pass channel_id as a keyword (or 'channel').
        if channel_id is None:
            channel_id = kwargs.pop("channel_id", None) or kwargs.pop("channel", None)
        # If template_str looks like a readable file path, load it.
        # (Template *source* always contains newlines; a path never does,
        # so this discriminator is safe even for very long source strings.)
        candidate: Optional[Path] = None
        if "\n" not in template_str and len(template_str) < 512:
            maybe = Path(template_str)
            if not maybe.is_absolute():
                maybe = _repo_root() / template_str
            try:
                if maybe.is_file():
                    candidate = maybe
            except OSError:
                candidate = None
        if candidate is not None:
            try:
                template_str = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(f"Cannot read template file {candidate}: {exc}") from exc
        if not template_str.strip():
            raise ValueError("template is empty")
        try:
            from jinja2 import Template
        except ImportError as exc:
            raise RuntimeError(
                "The 'jinja2' package is required for prompt rendering. "
                "Install it with: pip install jinja2"
            ) from exc

        context: dict[str, Any] = {}
        if channel_id:
            channel = self.get_channel(channel_id)
            niche = self.get_niche(channel_id)
            niche_name = str(_pick(niche, "niche", "naam", "name", default="") or "")
            audience = str(_pick(niche, "doelgroep", "audience", default="") or "")
            tone = str(_pick(niche, "toon_richtlijnen", "toon", "tone", default="") or "")
            pillars = self.list_pillars(channel_id)
            description = str(_pick(niche, "beschrijving", "description", default="") or "")

            context.update(
                {
                    "niche": niche_name,
                    "doelgroep": audience,
                    "audience": audience,
                    "toon": tone,
                    "tone": tone,
                    "pijlers": pillars,
                    "pillars": pillars,
                    "beschrijving": description,
                    "description": description,
                    "channel": channel,
                    "channel_id": channel_id,
                }
            )
        context.update(kwargs)
        try:
            return Template(template_str).render(**context)
        except Exception as exc:  # noqa: BLE001 - template errors
            logger.error("Prompt rendering failed for channel %s: %s", channel_id, exc)
            raise
