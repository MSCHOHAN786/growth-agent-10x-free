#!/usr/bin/env python3
"""
Smoke test suite runner — GROUP D script.

Checks:
  (a) all core + agents modules import
  (b) config/*.yaml parse and 10 channels (psy_nl_01..psy_nl_10) present
  (c) all 4 .j2 templates render with dummy Dutch context
  (d) required env vars are SET (values never printed)
  (e) LLMRouter instantiates and generate() raises LLMExhaustedError when no
      keys are configured (dry-run, no crash)
  (f) crypto roundtrip (encrypt_token / decrypt_token)
  (g) db/schema.sql contains all 9 table names
  (h) all 5 workflow yamls parse and have on/schedule/jobs keys
  (i) cloudflare worker.js contains routes
  (j) colab ipynb is valid JSON with nbformat 4

Prints PASS/FAIL/SKIP per check, then summary counts.
Exit 0 iff no check FAILED (SKIPs tolerated for missing optional deps).

Exit codes: 0 = all pass (no failures), 1 = at least one failure.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # tolerate missing dotenv
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False

load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("test_all")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []  # (check_id, status, detail)

CORE_CLASSES = ("SupabaseClient", "LLMRouter", "NicheEngine", "TelegramClient")
AGENT_CLASSES = (
    ("ResearchAgent", "research_agent"),
    ("ContentAgent", "content_agent"),
    ("SEOAgent", "seo_agent"),
    ("PublishAgent", "publish_agent"),
    ("EngagementAgent", "engagement_agent"),
    ("AnalyticsAgent", "analytics_agent"),
    ("BaseAgent", "base_agent"),
)


def record(check_id: str, status: str, detail: str = "") -> None:
    results.append((check_id, status, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"[{status}] {check_id}{suffix}")


def _import_agent_class(class_name: str, module_name: str):
    """Import an agent class via the package or its submodule; None if missing."""
    try:
        pkg = importlib.import_module("agents")
        cls = getattr(pkg, class_name, None)
        if cls is not None:
            return cls
    except ImportError:
        pass
    try:
        mod = importlib.import_module(f"agents.{module_name}")
        return getattr(mod, class_name, None)
    except ImportError:
        return None


def check_a_imports() -> None:
    """(a) all core + agents modules import."""
    try:
        core = importlib.import_module("core")
    except ImportError as exc:
        record("a-imports", FAIL, f"core import failed: {exc}")
        return
    missing = [f"core.{n}" for n in CORE_CLASSES if not hasattr(core, n)]
    for class_name, module_name in AGENT_CLASSES:
        if _import_agent_class(class_name, module_name) is None:
            missing.append(f"agents.{module_name}.{class_name}")
    if missing:
        record("a-imports", FAIL, "missing: " + ", ".join(missing))
    else:
        record("a-imports", PASS, "core + agents import, all classes present")


def check_b_config() -> None:
    """(b) config/*.yaml parse and 10 channels present."""
    try:
        import yaml
    except ImportError:
        record("b-config", SKIP, "pyyaml not installed")
        return
    yaml_files = sorted((REPO_ROOT / "config").glob("*.yaml")) + \
        sorted((REPO_ROOT / "config").glob("*.yml"))
    if not yaml_files:
        record("b-config", FAIL, "no yaml files in config/")
        return
    found_ids: set[str] = set()
    parse_errors = []
    for path in yaml_files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_errors.append(f"{path.name}: {exc}")
            continue
        found_ids.update(re.findall(r"psy_nl_\d{2}", json.dumps(data, default=str)))
    expected = {f"psy_nl_{i:02d}" for i in range(1, 11)}
    if parse_errors:
        record("b-config", FAIL, "yaml parse errors: " + "; ".join(parse_errors))
    elif expected <= found_ids:
        record("b-config", PASS, f"{len(yaml_files)} yaml files, 10 channels present")
    else:
        record("b-config", FAIL,
               "missing channels: " + ", ".join(sorted(expected - found_ids)))


def check_c_templates() -> None:
    """(c) all 4 .j2 templates render with dummy Dutch context."""
    try:
        import jinja2
    except ImportError:
        record("c-templates", SKIP, "jinja2 not installed")
        return
    templates = sorted((REPO_ROOT / "templates").rglob("*.j2"))
    if len(templates) != 4:
        record("c-templates", FAIL,
               f"expected 4 .j2 templates, found {len(templates)}")
        return
    dummy = {
        "niche_naam": "Testniche psychologie",
        "kanaal_naam": "Testkanaal",
        "beschrijving": "Een Nederlandstalig psychologie-kanaal.",
        "doelgroep": "Nederlandstalige volwassenen met interesse in psychologie.",
        "pijlers": ["mindset", "relaties", "zelfontwikkeling"],
        "toon": "warm en deskundig",
        "onderwerp": "Waarom slimme mensen zich eenzaam voelen",
        "titel": "Waarom slimme mensen zich eenzaam voelen",
        "script": "Dit is een voorbeeldscript in het Nederlands.",
        "reactie": "Bedankt voor je reactie!",
        "auteur": "Testauteur",
    }
    env = jinja2.Environment()
    for path in templates:
        try:
            env.from_string(path.read_text(encoding="utf-8")).render(dummy)
        except Exception as exc:
            record("c-templates", FAIL, f"{path.name} render error: {exc}")
            return
    record("c-templates", PASS, "4 templates render with dummy Dutch context")


def check_d_env() -> None:
    """(d) required env vars are SET — values are never printed."""
    # Names match .env.example / Group B (SupabaseClient reads
    # SUPABASE_URL + SUPABASE_SERVICE_KEY; TelegramClient uses the admin chat id).
    required = [
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "ENCRYPTION_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ADMIN_CHAT_ID",
        "DASHBOARD_URL",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        record("d-env", FAIL, "not set: " + ", ".join(missing))
    else:
        record("d-env", PASS, f"{len(required)} required env vars set")


def check_e_llm_router() -> None:
    """(e) LLMRouter instantiates; generate() raises LLMExhaustedError with no keys."""
    try:
        core = importlib.import_module("core")
        router_cls = getattr(core, "LLMRouter")
        exhausted = getattr(core, "LLMExhaustedError")
    except (ImportError, AttributeError) as exc:
        record("e-llm-router", FAIL, f"LLMRouter/LLMExhaustedError unavailable: {exc}")
        return
    # Strip provider keys (but never Telegram's) for a true no-key dry run.
    saved = {}
    for var in list(os.environ):
        if re.search(r"API[_-]?KEY", var) and "TELEGRAM" not in var.upper():
            saved[var] = os.environ.pop(var)
    try:
        router = router_cls()
        try:
            router.generate(prompt="Testprompt", system="Systeem")
        except exhausted:
            record("e-llm-router", PASS,
                   "generate() raised LLMExhaustedError gracefully with no keys")
        except Exception as exc:
            record("e-llm-router", FAIL,
                   f"generate() raised unexpected {type(exc).__name__}: {exc}")
        else:
            record("e-llm-router", FAIL,
                   "generate() did not raise with no keys configured")
    except Exception as exc:
        record("e-llm-router", FAIL, f"LLMRouter() raised: {exc}")
    finally:
        os.environ.update(saved)


def check_f_crypto() -> None:
    """(f) crypto roundtrip via encrypt_token / decrypt_token."""
    try:
        try:
            crypto = importlib.import_module("core.crypto")
        except ImportError:
            crypto = importlib.import_module("core")
        encrypt = getattr(crypto, "encrypt_token")
        decrypt = getattr(crypto, "decrypt_token")
    except (ImportError, AttributeError) as exc:
        record("f-crypto", SKIP, f"crypto helpers unavailable: {exc}")
        return
    if not os.environ.get("ENCRYPTION_KEY"):
        record("f-crypto", SKIP, "ENCRYPTION_KEY not set")
        return
    try:
        plaintext = "smoke-test-refresh-token-123"
        ciphertext = encrypt(plaintext)
        assert isinstance(ciphertext, str) and ciphertext != plaintext, \
            "ciphertext invalid"
        assert decrypt(ciphertext) == plaintext, "roundtrip mismatch"
        record("f-crypto", PASS, "encrypt/decrypt roundtrip OK")
    except Exception as exc:
        record("f-crypto", FAIL, f"roundtrip failed: {exc}")


def check_g_schema() -> None:
    """(g) db/schema.sql contains all 9 table names."""
    schema = REPO_ROOT / "db" / "schema.sql"
    if not schema.is_file():
        record("g-schema", FAIL, "db/schema.sql not found")
        return
    text = schema.read_text(encoding="utf-8").lower()
    created = set(re.findall(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)", text))
    # Tables referenced by name anywhere in the Group D contract must exist;
    # the project defines 9 tables in total.
    contract_tables = {"channels", "llm_usage", "api_usage", "niche_patterns"}
    missing_contract = contract_tables - created
    if missing_contract:
        record("g-schema", FAIL,
               "missing contract tables: " + ", ".join(sorted(missing_contract)))
    elif len(created) < 9:
        record("g-schema", FAIL, f"only {len(created)} tables found, expected 9")
    else:
        record("g-schema", PASS, f"{len(created)} tables: "
               + ", ".join(sorted(created)))


def check_h_workflows() -> None:
    """(h) all 5 workflow yamls parse and have on/schedule/jobs keys."""
    try:
        import yaml
    except ImportError:
        record("h-workflows", SKIP, "pyyaml not installed")
        return
    wf_dir = REPO_ROOT / ".github" / "workflows"
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    if len(files) != 5:
        record("h-workflows", FAIL,
               f"expected 5 workflow files, found {len(files)} in {wf_dir}")
        return
    for path in files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            record("h-workflows", FAIL, f"{path.name} parse error: {exc}")
            return
        if not isinstance(data, dict):
            record("h-workflows", FAIL, f"{path.name} is not a mapping")
            return
        # 'on' may parse as boolean True under YAML 1.1 — accept either key.
        missing_keys = [k for k in ("jobs",) if k not in data]
        if "on" not in data and True not in data:
            missing_keys.append("on")
        if missing_keys:
            record("h-workflows", FAIL,
                   f"{path.name} missing keys: {', '.join(missing_keys)}")
            return
        raw = path.read_text(encoding="utf-8")
        if "schedule" not in raw or "cron" not in raw:
            record("h-workflows", FAIL, f"{path.name} has no schedule/cron trigger")
            return
    record("h-workflows", PASS, "5 workflows parse with on/schedule/jobs")


def check_i_worker() -> None:
    """(i) cloudflare worker.js contains routes."""
    candidates = list((REPO_ROOT / "cloudflare").rglob("worker.js"))
    if not candidates:
        record("i-worker", FAIL, "no worker.js found under cloudflare/")
        return
    path = candidates[0]
    text = path.read_text(encoding="utf-8")
    if "routes" in text or ("pathname" in text and "fetch" in text):
        record("i-worker", PASS, f"{path.relative_to(REPO_ROOT)} contains routes")
    else:
        record("i-worker", FAIL, f"{path.name} contains no route handling")


def check_j_notebook() -> None:
    """(j) colab ipynb is valid JSON with nbformat 4."""
    notebooks = sorted((REPO_ROOT / "colab").glob("*.ipynb"))
    if not notebooks:
        record("j-notebook", FAIL, "no .ipynb found under colab/")
        return
    for path in notebooks:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            record("j-notebook", FAIL, f"{path.name} invalid JSON: {exc}")
            return
        if data.get("nbformat") != 4:
            record("j-notebook", FAIL,
                   f"{path.name} nbformat={data.get('nbformat')}, expected 4")
            return
    record("j-notebook", PASS, f"{len(notebooks)} notebook(s), nbformat 4 valid JSON")


def main() -> int:
    print("=" * 60)
    print("Growth Agent 10x Free — smoke test suite")
    print("=" * 60)
    check_a_imports()
    check_b_config()
    check_c_templates()
    check_d_env()
    check_e_llm_router()
    check_f_crypto()
    check_g_schema()
    check_h_workflows()
    check_i_worker()
    check_j_notebook()
    print("=" * 60)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print(f"Summary: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP "
          f"({len(results)} checks)")
    if n_fail:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
