#!/usr/bin/env python3
"""
Send Reminder — sends reminder emails to family members via Microsoft Graph API.

Guardrails:
- Recipients limited to family allowlist only
- Max 5 sends per day (resets at midnight local time)
- All sends logged to data/reminder_send_log.json
- Subject always prefixed with [Giri Reminder]
- Routing logic determines who gets each reminder based on content
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: requests. Install with `python3 -m pip install requests`.")

GRAPH_API_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["Mail.Read", "Mail.Send", "Calendars.ReadWrite", "User.Read", "offline_access"]
MAX_SENDS_PER_DAY = 5
SUBJECT_PREFIX = "[Giri Reminder]"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN_CACHE = os.path.join(SCRIPT_DIR, "data", "graph_token.json")
SEND_LOG = os.path.join(SCRIPT_DIR, "data", "reminder_send_log.json")
FAMILY_CONFIG_PATH = os.path.join(SCRIPT_DIR, "family_config.json")

# === FAMILY CONFIG ===
# Loaded from scripts/family_config.json (gitignored — contains real names/
# emails/keywords). See scripts/family_config.example.json for the template.


def load_family_config(path: str = FAMILY_CONFIG_PATH):
    if not os.path.exists(path):
        sys.exit(
            f"Missing family config: {path}\n"
            "Copy scripts/family_config.example.json to scripts/family_config.json "
            "and fill in real values."
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["family"], cfg.get("son_keywords", []), cfg.get("daughter_keywords", [])


FAMILY, SON_KEYWORDS, DAUGHTER_KEYWORDS = load_family_config()

# Allowlist — only these addresses can ever receive emails
ALLOWED_RECIPIENTS = set(FAMILY.values())

# Keywords that signal kid-related content (general)
KID_KEYWORDS = [
    "school", "student", "parent", "guardian",
    "enrollment", "registration", "field trip",
    "report card", "grades", "homework",
    "pediatric", "paediatric", "allegro",
] + SON_KEYWORDS + DAUGHTER_KEYWORDS


def classify_reminder(subject: str, body: str) -> list:
    """
    Determine who should receive this reminder.
    Returns list of email addresses.
    """
    text = f"{subject} {body}".lower()

    is_son = any(kw in text for kw in SON_KEYWORDS)
    is_daughter = any(kw in text for kw in DAUGHTER_KEYWORDS)
    is_kid_related = any(kw in text for kw in KID_KEYWORDS)

    recipients = [FAMILY["sunder"]]  # Sunder always gets it

    if is_kid_related or is_son or is_daughter:
        # Kid-related: wife always included
        if FAMILY["wife"] not in recipients:
            recipients.append(FAMILY["wife"])

    if is_son:
        recipients.append(FAMILY["son"])

    if is_daughter:
        recipients.append(FAMILY["daughter"])

    return recipients


def load_dotenv(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


def get_token(client_id: str, tenant: str = "common", cache_path: str = DEFAULT_TOKEN_CACHE):
    """Reuse cached token or refresh."""
    if not os.path.exists(cache_path):
        sys.exit(f"Token cache not found: {cache_path}\nRun otf_email_parser.py --graph first to authenticate.")

    with open(cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)

    now = datetime.now(timezone.utc).timestamp()
    if cache.get("access_token") and cache.get("expires_at", 0) > now + 60:
        return cache["access_token"]

    refresh_token = cache.get("refresh_token")
    if not refresh_token:
        sys.exit("No refresh token in cache. Re-authenticate with device-code flow.")

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(GRAPH_SCOPE),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Token refresh failed ({resp.status_code}): {resp.text}")

    tokens = resp.json()
    cache["access_token"] = tokens["access_token"]
    if "refresh_token" in tokens:
        cache["refresh_token"] = tokens["refresh_token"]
    cache["expires_at"] = now + tokens.get("expires_in", 3600)

    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    return tokens["access_token"]


def load_send_log():
    if not os.path.exists(SEND_LOG):
        return []
    with open(SEND_LOG, "r", encoding="utf-8") as f:
        return json.load(f)


def save_send_log(log):
    os.makedirs(os.path.dirname(SEND_LOG) or ".", exist_ok=True)
    with open(SEND_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def check_rate_limit():
    """Returns True if under daily limit."""
    log = load_send_log()
    today = datetime.now().strftime("%Y-%m-%d")
    today_sends = [e for e in log if e.get("date") == today]
    return len(today_sends) < MAX_SENDS_PER_DAY


def send_email(token: str, subject: str, body: str, recipients: list, save_to_sent: bool = True):
    """Send an email via Graph API. Only sends to allowlisted addresses."""
    # Safety check
    for addr in recipients:
        if addr not in ALLOWED_RECIPIENTS:
            print(f"✗ Blocked: {addr} not in allowlist", file=sys.stderr)
            return False

    if not subject.startswith(SUBJECT_PREFIX):
        subject = f"{SUBJECT_PREFIX} {subject}"

    to_recipients = [{"emailAddress": {"address": addr}} for addr in recipients]

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": to_recipients,
        },
        "saveToSentItems": save_to_sent,
    }

    resp = requests.post(
        f"{GRAPH_API_ROOT}/me/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code == 202:
        log = load_send_log()
        log.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().isoformat(),
            "subject": subject,
            "recipients": recipients,
            "status": "sent",
        })
        save_send_log(log)
        return True
    else:
        print(f"Send failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Send a reminder email to family")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body", required=True, help="Email body text")
    parser.add_argument("--to", nargs="*", help="Override recipients (names: sunder/wife/son/daughter, or emails)")
    parser.add_argument("--auto-route", action="store_true",
                        help="Auto-determine recipients from subject+body content")
    parser.add_argument("--client-id", default=os.getenv("OTF_GRAPH_CLIENT_ID"))
    parser.add_argument("--token-cache", default=DEFAULT_TOKEN_CACHE)
    parser.add_argument("--no-save-sent", action="store_true")
    parser.add_argument("--force", action="store_true", help="Bypass rate limit")
    parser.add_argument("--dry-run", action="store_true", help="Show who would receive without sending")
    args = parser.parse_args()

    for env_path in [os.path.join(SCRIPT_DIR, ".env"),
                     os.path.expanduser("~/.openclaw/.env")]:
        load_dotenv(env_path)

    if not args.client_id:
        args.client_id = os.getenv("OTF_GRAPH_CLIENT_ID")
    if not args.client_id:
        sys.exit("No client ID. Set OTF_GRAPH_CLIENT_ID in .env")

    # Determine recipients
    if args.to:
        recipients = []
        for t in args.to:
            if t in FAMILY:
                recipients.append(FAMILY[t])
            elif t in ALLOWED_RECIPIENTS:
                recipients.append(t)
            else:
                sys.exit(f"Unknown recipient: {t}. Use: {', '.join(FAMILY.keys())} or an allowlisted email.")
    elif args.auto_route:
        recipients = classify_reminder(args.subject, args.body)
    else:
        # Default: just Sunder
        recipients = [FAMILY["sunder"]]

    if args.dry_run:
        print(f"Would send to: {', '.join(recipients)}")
        print(f"Subject: {SUBJECT_PREFIX} {args.subject}")
        return

    if not args.force and not check_rate_limit():
        sys.exit(f"Rate limit reached ({MAX_SENDS_PER_DAY} emails/day). Use --force to override.")

    token = get_token(args.client_id, cache_path=args.token_cache)
    success = send_email(token, args.subject, args.body, recipients, save_to_sent=not args.no_save_sent)

    if success:
        print(f"✓ Reminder sent to {', '.join(recipients)}: {args.subject}")
    else:
        sys.exit("Failed to send.")


if __name__ == "__main__":
    main()
