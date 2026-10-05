#!/usr/bin/env python3
"""
Interactive YouTube OAuth setup for the 10 channels — GROUP D script.

Run ONCE per channel on a machine with a browser. Steps:

  1. Prints numbered manual instructions (GCP project, YouTube Data API v3,
     OAuth consent screen, Desktop OAuth client, download client-secrets JSON).
  2. Prompts for the client-secrets file path
     (default: $YOUTUBE_CLIENT_SECRETS_DIR/<channel_id>_client_secrets.json).
  3. Runs the OAuth flow (google_auth_oauthlib InstalledAppFlow, local server).
  4. Encrypts the refresh token via encrypt_token (ENCRYPTION_KEY) and upserts
     channels.youtube_oauth_encrypted for the channel id.

Tokens are NEVER printed — not even partially. Per-channel continue-on-error
when looping all 10 (--channel selects a single channel).

Real interfaces (Group B):
    from core import SupabaseClient
    from core.crypto import encrypt_token   (fallback: from core import encrypt_token)
    db.get_channel(channel_id) -> dict | None
    db.client.table("channels").upsert({"id": ..., "youtube_oauth_encrypted": ...})

Exit codes: 0 = success, 1 = failure, 2 = config error.
"""
from __future__ import annotations

import argparse
import logging
import os
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

try:
    from core import SupabaseClient
except ImportError as exc:
    print(f"CONFIG ERROR: core modules missing: {exc}", file=sys.stderr)
    sys.exit(2)

try:
    from core.crypto import encrypt_token
except ImportError:
    try:
        from core import encrypt_token  # type: ignore[no-redef]
    except ImportError as exc:
        print(f"CONFIG ERROR: encrypt_token not available: {exc}", file=sys.stderr)
        sys.exit(2)

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("CONFIG ERROR: google-auth-oauthlib is not installed. "
          "Install it with: pip install google-auth-oauthlib", file=sys.stderr)
    sys.exit(2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("setup_oauth")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CHANNEL_IDS = [f"psy_nl_{i:02d}" for i in range(1, 11)]

INSTRUCTIONS = """\
Manual steps — do these ONCE in your Google Cloud Console (use the Google
account that owns the YouTube channel):

  1. Go to https://console.cloud.google.com/ and create (or select) a
     project, e.g. "growth-agent-10x".
  2. Open "APIs & Services" -> "Library", search for "YouTube Data API v3"
     and click ENABLE.
  3. Open "APIs & Services" -> "OAuth consent screen":
       - User type: External
       - Fill in app name + your email, add yourself as a test user.
       - No sensitive scopes beyond the two YouTube scopes below are needed.
  4. Open "APIs & Services" -> "Credentials" -> "Create Credentials" ->
     "OAuth client ID" -> Application type: "Desktop app".
     Name it e.g. "growth-agent-{channel_id}".
  5. Download the client JSON (download icon next to the client) and save it
     on this machine.

Press ENTER when you have completed the steps above for channel {channel_id}."""


def default_secrets_path(channel_id: str) -> Path:
    base = os.environ.get("YOUTUBE_CLIENT_SECRETS_DIR") or str(Path.home() / ".secrets")
    return Path(base) / f"{channel_id}_client_secrets.json"


def prompt_secrets_path(channel_id: str) -> Path:
    default = default_secrets_path(channel_id)
    try:
        answer = input(
            f"Path to client-secrets JSON for {channel_id}\n[{default}]: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("Aborted by user")
    path = Path(answer).expanduser() if answer else default
    if not path.is_file():
        raise FileNotFoundError(f"Client-secrets file not found: {path}")
    return path


def run_oauth_flow(secrets_path: Path):
    """Run the browser OAuth flow; return the credentials object."""
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    print("\nA browser window will open — sign in with the Google account that "
          "owns the YouTube channel and grant access.")
    return flow.run_local_server(port=0, open_browser=True)


def save_encrypted_token(db, channel_id: str, encrypted: str) -> None:
    """Upsert the encrypted refresh token into channels.youtube_oauth_encrypted."""
    client = getattr(db, "client", None)
    if client is not None:
        client.table("channels").upsert(
            {"id": channel_id, "youtube_oauth_encrypted": encrypted}
        ).execute()
        return
    # Fallbacks in case Group B exposes a helper instead of .client.
    for method, kwargs in (
        ("update_channel", {"channel_id": channel_id,
                            "fields": {"youtube_oauth_encrypted": encrypted}}),
        ("save_oauth_token", {"channel_id": channel_id,
                              "encrypted_token": encrypted}),
    ):
        fn = getattr(db, method, None)
        if callable(fn):
            fn(**kwargs)
            return
    raise AttributeError("db exposes neither .client nor a channel-update helper")


def setup_channel(db, channel_id: str) -> bool:
    """Full interactive setup for one channel. Returns True on success."""
    print("=" * 70)
    print(f"YouTube OAuth setup — channel: {channel_id}")
    print("=" * 70)

    try:
        existing = db.get_channel(channel_id)
    except Exception as exc:
        log.warning("Could not verify channel %s in db: %s", channel_id, exc)
        existing = None
    if existing and existing.get("youtube_oauth_encrypted"):
        print(f"Channel {channel_id} already has an encrypted OAuth token stored.")
        try:
            redo = input("Replace it? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted by user.")
            return False
        if redo != "y":
            log.info("Skipping %s (token already stored)", channel_id)
            return True

    print(INSTRUCTIONS.format(channel_id=channel_id))
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted by user.")
        return False

    try:
        secrets_path = prompt_secrets_path(channel_id)
    except (FileNotFoundError, SystemExit) as exc:
        log.error("%s", exc)
        return False

    try:
        creds = run_oauth_flow(secrets_path)
    except Exception as exc:
        log.error("OAuth flow failed for %s: %s", channel_id, exc)
        return False

    refresh_token = getattr(creds, "refresh_token", None)
    if not refresh_token:
        log.error("OAuth flow for %s returned no refresh token "
                  "(grant offline access and try again)", channel_id)
        return False

    try:
        # NEVER print the token — not even partially.
        encrypted = encrypt_token(refresh_token)
    except Exception as exc:
        log.error("Token encryption failed for %s: %s "
                  "(is ENCRYPTION_KEY set?)", channel_id, exc)
        return False
    finally:
        # Drop the raw token from memory as soon as possible.
        try:
            del refresh_token
        except NameError:
            pass

    try:
        save_encrypted_token(db, channel_id, encrypted)
    except Exception as exc:
        log.error("Could not save encrypted token for %s: %s", channel_id, exc)
        return False

    print(f"\nOAuth token for {channel_id} encrypted and saved "
          "(channels.youtube_oauth_encrypted).")
    log.info("OAuth setup complete for channel %s", channel_id)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive YouTube OAuth setup for the 10 Dutch psychology channels")
    parser.add_argument("--channel",
                        help="Set up a single channel id (e.g. psy_nl_01). "
                             "Without this flag, all 10 channels are processed in a loop.")
    args = parser.parse_args()

    if args.channel and args.channel not in CHANNEL_IDS:
        print(f"Unknown channel {args.channel!r}; expected one of: "
              f"{', '.join(CHANNEL_IDS)}", file=sys.stderr)
        return 2

    try:
        db = SupabaseClient()
    except Exception as exc:
        log.error("Could not initialize SupabaseClient: %s", exc)
        return 2

    if not os.environ.get("ENCRYPTION_KEY"):
        log.error("ENCRYPTION_KEY is not set — cannot encrypt tokens")
        return 2

    targets = [args.channel] if args.channel else CHANNEL_IDS
    log.info("Starting setup_oauth for %d channel(s)", len(targets))

    ok, failed = 0, []
    for cid in targets:
        try:
            if setup_channel(db, cid):
                ok += 1
            else:
                failed.append(cid)
        except Exception as exc:  # per-channel continue-on-error
            log.exception("Unexpected error for channel %s: %s", cid, exc)
            failed.append(cid)
        if not args.channel and cid != targets[-1]:
            print("\nContinuing to next channel…\n")

    print("=" * 70)
    print(f"Done: {ok}/{len(targets)} channels configured.")
    if failed:
        print(f"Failed / skipped: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
