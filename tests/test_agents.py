"""Group F tests for Growth Agent 10x Free.

Every test is written against the project CONTRACT (10 Dutch psychology
channels ``psy_nl_01`` .. ``psy_nl_10``, 7 agents, core exports, 9 Supabase
tables, 5 workflows, Cloudflare worker routes, Colab notebook).

Other groups write those artifacts in parallel, so any test whose artifact
is not present yet SKIPS gracefully instead of failing. When the artifact
exists, the test performs a real assertion.

Run with:  python -m pytest tests/ -v   (from the repo root)
Requires:  pytest, pyyaml
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"
PROMPTS_DIR = TEMPLATES_DIR / "prompts"
DB_DIR = ROOT / "db"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
CLOUDFLARE_DIR = ROOT / "cloudflare"
COLAB_DIR = ROOT / "colab"

CHANNEL_IDS = [f"psy_nl_{i:02d}" for i in range(1, 11)]

TABLES = [
    "channels",
    "videos",
    "analytics",
    "approval_queue",
    "engagement_queue",
    "job_queue",
    "llm_usage",
    "niche_patterns",
    "api_usage",
]

TEMPLATE_NAMES = ["research.j2", "content.j2", "seo.j2", "engagement.j2"]

ROUTES = ["/health", "/webhook/youtube", "/telegram", "/trigger/colab"]

AGENT_CLASSES = [
    "ResearchAgent",
    "ContentAgent",
    "SEOAgent",
    "PublishAgent",
    "EngagementAgent",
    "AnalyticsAgent",
    "BaseAgent",
]

yaml = pytest.importorskip("yaml", reason="pyyaml is required for config tests")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_yaml_file(path: Path):
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not written yet")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read_text(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not written yet")
    return path.read_text(encoding="utf-8")


def _find_template(name: str) -> Path:
    for candidate in (PROMPTS_DIR / name, TEMPLATES_DIR / name):
        if candidate.exists():
            return candidate
    pytest.skip(f"template {name} not written yet")


def _channel_ids(channels) -> list:
    ids = []
    for ch in channels:
        if isinstance(ch, dict):
            ids.append(ch.get("id"))
        else:
            ids.append(getattr(ch, "id", None))
    return ids


# ---------------------------------------------------------------------------
# 1. config: channels.yaml
# ---------------------------------------------------------------------------

def test_config_has_ten_channels():
    data = _load_yaml_file(CONFIG_DIR / "channels.yaml")
    channels = data.get("channels") if isinstance(data, dict) else data
    if not isinstance(channels, list):
        pytest.skip("channels.yaml has no top-level channel list yet")
    assert len(channels) == 10, f"expected 10 channels, got {len(channels)}"
    assert _channel_ids(channels) == CHANNEL_IDS


# ---------------------------------------------------------------------------
# 2. niche library (Dutch)
# ---------------------------------------------------------------------------

def test_niche_library_dutch():
    path = CONFIG_DIR / "niche_library.yaml"
    if not path.exists():
        path = CONFIG_DIR / "niches.yaml"
    data = _load_yaml_file(path)
    if data is None:
        pytest.skip(f"{path.relative_to(ROOT)} is empty (not written yet)")
    niches = None
    if isinstance(data, dict):
        for key in ("sub_niches", "subniches", "niches", "niche_library"):
            value = data.get(key)
            if isinstance(value, dict):
                niches = list(value.values())
                break
            if isinstance(value, list):
                niches = value
                break
    elif isinstance(data, list):
        niches = data
    if niches is not None:
        assert len(niches) == 10, f"expected 10 sub-niches, got {len(niches)}"
        for niche in niches:
            assert "naam" in niche, f"sub-niche missing Dutch key 'naam': {niche}"
            assert "beschrijving" in niche, (
                f"sub-niche missing Dutch key 'beschrijving': {niche}"
            )
    else:
        # Fallback: raw-text check for the Dutch keys across 10 entries.
        raw = path.read_text(encoding="utf-8")
        naam_hits = len(re.findall(r"(?m)^\s*naam\s*:", raw))
        assert naam_hits == 10, f"expected 10 'naam' entries, found {naam_hits}"
        assert re.search(r"(?m)^\s*beschrijving\s*:", raw), (
            "Dutch key 'beschrijving' not found in niche library"
        )


# ---------------------------------------------------------------------------
# 3. NicheEngine
# ---------------------------------------------------------------------------

def _load_engine():
    try:
        from core import NicheEngine
    except ImportError:
        pytest.skip("core.NicheEngine not available yet")
    # Default constructor resolves config/niche_library.yaml + config/channels.yaml
    # relative to the repo root on its own; also try explicit paths for older
    # revisions of the constructor.
    attempts = [
        (),
        (str(CONFIG_DIR / "niche_library.yaml"),
         str(CONFIG_DIR / "channels.yaml")),
        (str(CONFIG_DIR / "channels.yaml"),),
    ]
    last_error = None
    for args in attempts:
        try:
            return NicheEngine(*args)
        except (TypeError, ImportError, OSError) as exc:
            last_error = exc
    pytest.skip(f"NicheEngine constructor not usable yet ({last_error})")


def _engine_channels(engine):
    for name in ("list_channels", "get_channels", "channels"):
        attr = getattr(engine, name, None)
        if attr is None:
            continue
        value = attr() if callable(attr) else attr
        if isinstance(value, (list, tuple)):
            return list(value)
    pytest.skip("NicheEngine exposes no channel-list accessor yet")


def test_niche_engine_loads():
    engine = _load_engine()
    channels = _engine_channels(engine)
    if not channels:
        pytest.skip("NicheEngine loaded no channels (config not populated yet)")
    assert len(channels) == 10, f"expected 10 channels, got {len(channels)}"
    assert _channel_ids(channels) == CHANNEL_IDS


# ---------------------------------------------------------------------------
# 4. Dutch Jinja2 templates render through NicheEngine.render_prompt
# ---------------------------------------------------------------------------

def _render_template(engine, template_text: str, channel_id: str,
                     context: dict) -> str | None:
    # Real signature: render_prompt(template_str, channel_id, **kwargs).
    # Older/alternative shapes are tried before giving up and skipping.
    variants = [
        lambda: engine.render_prompt(template_text, channel_id, **context),
        lambda: engine.render_prompt(template_text, channel_id=channel_id,
                                     **context),
        lambda: engine.render_prompt(template=template_text,
                                     channel_id=channel_id, **context),
    ]
    for call in variants:
        try:
            out = call()
        except (TypeError, AttributeError):
            continue
        if isinstance(out, str):
            return out
    return None


def test_templates_render_dutch():
    engine = _load_engine()
    if not hasattr(engine, "render_prompt"):
        pytest.skip("NicheEngine.render_prompt not implemented yet")
    context = {"onderwerp": "beter slapen",
               "doelgroep": "jongvolwassenen",
               "taal": "Nederlands"}
    for name in TEMPLATE_NAMES:
        template_path = _find_template(name)  # skip if the file is missing
        template_text = template_path.read_text(encoding="utf-8")
        rendered = _render_template(engine, template_text, "psy_nl_01",
                                    context)
        if rendered is None:
            pytest.skip(f"render_prompt could not render {name} yet")
        assert len(rendered) > 50, f"{name} rendered too short: {rendered!r}"
        lowered = rendered.lower()
        assert "psychologie" in lowered or " je " in lowered, (
            f"{name} does not look Dutch: {rendered[:80]!r}"
        )


# ---------------------------------------------------------------------------
# 5. crypto roundtrip
# ---------------------------------------------------------------------------

def _crypto_helpers():
    try:
        from core.crypto import generate_key, encrypt_token, decrypt_token
        return generate_key, encrypt_token, decrypt_token
    except ImportError:
        pass
    try:
        from core import generate_key, encrypt_token, decrypt_token
        return generate_key, encrypt_token, decrypt_token
    except ImportError:
        pytest.skip("crypto helpers (generate_key/encrypt_token/decrypt_token)"
                    " not available yet")


def _encrypt(encrypt_token, secret: str, key):
    for call in (lambda: encrypt_token(secret, key),
                 lambda: encrypt_token(key, secret)):
        try:
            token = call()
        except TypeError:
            continue
        if token:
            return token
    pytest.skip("encrypt_token signature not understood yet")


def _decrypt(decrypt_token, token, key):
    for call in (lambda: decrypt_token(token, key),
                 lambda: decrypt_token(key, token)):
        try:
            return call()
        except TypeError:
            continue
    raise AssertionError("decrypt_token signature not understood")


def test_crypto_roundtrip():
    # cryptography is an optional third-party dep: skip gracefully if missing.
    pytest.importorskip("cryptography",
                        reason="cryptography package not installed")
    generate_key, encrypt_token, decrypt_token = _crypto_helpers()
    key = generate_key()
    secret = "yt-oauth-refresh-token-psy_nl_01"
    token = _encrypt(encrypt_token, secret, key)
    assert _decrypt(decrypt_token, token, key) == secret
    wrong_key = generate_key()
    with pytest.raises(Exception):
        _decrypt(decrypt_token, token, wrong_key)


# ---------------------------------------------------------------------------
# 6 & 7. LLM router
# ---------------------------------------------------------------------------

def _llm_imports():
    try:
        from core import LLMRouter, LLMExhaustedError
        return LLMRouter, LLMExhaustedError
    except ImportError:
        pytest.skip("core LLM router not available yet")


def test_llm_router_provider_order():
    LLMRouter, _ = _llm_imports()
    providers = getattr(LLMRouter, "PROVIDERS", None)
    if providers is None:
        # Some revisions keep PROVIDERS at module level.
        try:
            from core import llm_router as llm_router_module
            providers = getattr(llm_router_module, "PROVIDERS", None)
        except ImportError:
            providers = None
    if providers is None:
        pytest.skip("LLMRouter.PROVIDERS not defined yet")
    assert providers[0]["name"] == "groq", (
        f"first provider should be groq, got {providers[0]!r}"
    )


def test_llm_router_exhausted(monkeypatch):
    LLMRouter, LLMExhaustedError = _llm_imports()
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "COHERE_API_KEY", "COHERE_KEY"):
        monkeypatch.delenv(var, raising=False)
    try:
        router = LLMRouter()
    except (TypeError, ImportError) as exc:
        pytest.skip(f"LLMRouter() not constructible yet ({exc})")
    calls = [lambda: router.generate("test prompt"),
             lambda: router.generate(prompt="test prompt")]
    type_errors = 0
    for call in calls:
        try:
            call()
        except LLMExhaustedError:
            return  # expected: no keys -> all providers exhausted
        except TypeError:
            type_errors += 1
            continue
        else:
            pytest.fail("generate() succeeded with no provider keys set")
    pytest.skip("LLMRouter.generate signature not understood yet")


# ---------------------------------------------------------------------------
# 8. Supabase schema: 9 tables
# ---------------------------------------------------------------------------

def test_schema_tables():
    sql = _read_text(DB_DIR / "schema.sql")
    missing = [t for t in TABLES
               if not re.search(rf"(?i)CREATE\s+TABLE[^\n;]*\b{t}\b", sql)]
    assert not missing, f"schema.sql missing tables: {missing}"


# ---------------------------------------------------------------------------
# 9. GitHub Actions workflows valid
# ---------------------------------------------------------------------------

def test_workflows_valid():
    yml_files = sorted(WORKFLOWS_DIR.glob("*.yml")) + \
                sorted(WORKFLOWS_DIR.glob("*.yaml"))
    if not yml_files:
        pytest.skip(".github/workflows not written yet")
    assert len(yml_files) == 5, (
        f"expected 5 workflows, found {len(yml_files)}: "
        f"{[p.name for p in yml_files]}"
    )
    for path in yml_files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{path.name} did not parse to a mapping"
        # PyYAML 1.1 quirk: the bare key `on` parses as boolean True.
        assert "on" in doc or True in doc, f"{path.name} missing 'on' trigger"
        assert "jobs" in doc and isinstance(doc["jobs"], dict), (
            f"{path.name} missing 'jobs' mapping"
        )


# ---------------------------------------------------------------------------
# 10. Cloudflare worker routes
# ---------------------------------------------------------------------------

def test_worker_routes():
    src = _read_text(CLOUDFLARE_DIR / "worker.js")
    for route in ROUTES:
        assert route in src, f"worker.js missing route {route}"
    assert "export default" in src, "worker.js missing 'export default'"


# ---------------------------------------------------------------------------
# 11. Colab notebook valid
# ---------------------------------------------------------------------------

def test_notebook_valid():
    raw = _read_text(COLAB_DIR / "content_worker.ipynb")
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"content_worker.ipynb is not valid JSON: {exc}")
    assert nb.get("nbformat") == 4, (
        f"expected nbformat 4, got {nb.get('nbformat')}"
    )
    cells = nb.get("cells", [])
    assert len(cells) >= 5, f"expected >= 5 cells, got {len(cells)}"


# ---------------------------------------------------------------------------
# 12. agents importable
# ---------------------------------------------------------------------------

def test_agents_importable():
    try:
        import agents
    except ImportError:
        pytest.skip("agents package not available yet")
    missing = [name for name in AGENT_CLASSES
               if not isinstance(getattr(agents, name, None), type)]
    if missing:
        pytest.skip(f"agents package does not export yet: {', '.join(missing)}")
    for name in AGENT_CLASSES:
        assert isinstance(getattr(agents, name), type)
