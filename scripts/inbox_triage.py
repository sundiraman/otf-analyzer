#!/usr/bin/env python3
"""
Inbox Triage — fetches recent emails from Microsoft Graph and outputs
a structured summary for action-item extraction.

Uses the same Graph API auth as otf_email_parser.py.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: requests. Install with `python3 -m pip install requests`.")


GRAPH_API_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["Mail.Read", "Mail.Send", "Calendars.ReadWrite", "User.Read", "offline_access"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN_CACHE = os.path.join(SCRIPT_DIR, "data", "graph_token.json")
DEFAULT_ENV = os.path.join(SCRIPT_DIR, "..", ".openclaw", ".env")


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
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)

    now = datetime.now(timezone.utc).timestamp()
    if cache.get("access_token") and cache.get("expires_at", 0) > now + 60:
        return cache["access_token"]

    authority = f"https://login.microsoftonline.com/{tenant}"
    token_url = f"{authority}/oauth2/v2.0/token"
    scope = " ".join(GRAPH_SCOPE)

    if cache.get("refresh_token"):
        r = requests.post(token_url, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": cache["refresh_token"],
            "scope": scope,
        }, timeout=30)
        if r.ok:
            tok = r.json()
            cache = {
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", cache.get("refresh_token")),
                "expires_at": now + int(tok.get("expires_in", 3600)),
            }
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            return cache["access_token"]

    sys.exit("No valid token or refresh token. Run otf_email_parser.py --graph first to authenticate.")


def fetch_messages(token: str, since_days: int = 3, top: int = 50, unread_only: bool = False, folder: str = "inbox"):
    """Fetch recent messages from Graph API."""
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    filters = [f"receivedDateTime ge {since}"]
    if unread_only:
        filters.append("isRead eq false")

    params = {
        "$filter": " and ".join(filters),
        "$orderby": "receivedDateTime desc",
        "$top": top,
        "$select": "id,subject,from,receivedDateTime,isRead,importance,hasAttachments,bodyPreview,categories,flag",
    }

    url = f"{GRAPH_API_ROOT}/me/mailFolders/{folder}/messages"
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.get(url, headers=headers, params=params, timeout=30)
    if not r.ok:
        sys.exit(f"Graph API error {r.status_code}: {r.text[:300]}")

    return r.json().get("value", [])


def categorize_email(msg):
    """Simple heuristic categorization."""
    subject = (msg.get("subject") or "").lower()
    sender = (msg.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
    preview = (msg.get("bodyPreview") or "").lower()
    importance = msg.get("importance", "normal")
    flag_status = msg.get("flag", {}).get("flagStatus", "notFlagged")

    # Action signals — check both subject and body preview
    action_keywords = ["action required", "please respond", "rsvp", "confirm", "approve",
                       "deadline", "due by", "due date", "payment due", "expiring", "verify your",
                       "schedule", "appointment", "interview", "offer letter",
                       "request form", "complete the form", "fill out", "submit by",
                       "request due", "needs to be received by", "last day",
                       "register by", "sign up by", "respond by", "reply by"]
    promo_senders = ["newsletter", "marketing", "promo", "notification"]
    promo_domains = ["hbomax.com", "adidas.com", "colehaan.com", "potterybarn.com",
                     "hulu.com", "disney", "ea.com", "rivian.com", "opentable.com",
                     "newbalance.com", "fragrancenet.com", "macys.com", "ashley"]

    # Check if it's marketing/promo noise
    is_noise = (any(s in sender for s in promo_senders)
                or any(d in sender for d in promo_domains))

    # Check for action signals in subject AND preview
    text = subject + " " + preview
    needs_action = (
        importance == "high"
        or flag_status == "flagged"
        or any(kw in text for kw in action_keywords)
    )

    if needs_action and not is_noise:
        return "ACTION"
    elif is_noise:
        return "FYI/NOISE"
    elif not msg.get("isRead"):
        return "REVIEW"
    else:
        return "FYI"


def format_output(messages, output_format="text"):
    """Format messages into readable output."""
    categorized = {"ACTION": [], "REVIEW": [], "FYI": [], "FYI/NOISE": []}

    for msg in messages:
        cat = categorize_email(msg)
        entry = {
            "subject": msg.get("subject", "(no subject)"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("name")
                    or msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"),
            "date": msg.get("receivedDateTime", "")[:16].replace("T", " "),
            "read": msg.get("isRead", False),
            "preview": (msg.get("bodyPreview") or "")[:120],
            "importance": msg.get("importance", "normal"),
            "has_attachments": msg.get("hasAttachments", False),
        }
        categorized[cat].append(entry)

    if output_format == "json":
        json.dump(categorized, sys.stdout, indent=2)
        return

    # Text output
    total = len(messages)
    action_count = len(categorized["ACTION"])
    review_count = len(categorized["REVIEW"])

    print(f"📬 Inbox Triage — {total} emails scanned")
    print(f"   🔴 Action needed: {action_count}")
    print(f"   🟡 Unread to review: {review_count}")
    print(f"   ⚪ FYI/Noise: {len(categorized['FYI']) + len(categorized['FYI/NOISE'])}")
    print()

    if categorized["ACTION"]:
        print("━━━ 🔴 ACTION NEEDED ━━━")
        for e in categorized["ACTION"]:
            att = " 📎" if e["has_attachments"] else ""
            print(f"  • [{e['date']}] {e['from']}{att}")
            print(f"    {e['subject']}")
            if e["preview"]:
                print(f"    → {e['preview'][:100]}")
            print()

    if categorized["REVIEW"]:
        print("━━━ 🟡 UNREAD — REVIEW ━━━")
        for e in categorized["REVIEW"]:
            att = " 📎" if e["has_attachments"] else ""
            print(f"  • [{e['date']}] {e['from']}{att}")
            print(f"    {e['subject']}")
            if e["preview"]:
                print(f"    → {e['preview'][:100]}")
            print()

    if categorized["FYI"]:
        print("━━━ ⚪ FYI (already read) ━━━")
        for e in categorized["FYI"][:10]:  # cap noise
            print(f"  • [{e['date']}] {e['from']} — {e['subject']}")
        if len(categorized["FYI"]) > 10:
            print(f"  ... and {len(categorized['FYI']) - 10} more")
        print()

    if categorized["FYI/NOISE"]:
        print(f"━━━ 🗑️  NOISE ({len(categorized['FYI/NOISE'])} emails) ━━━")
        senders = set(e["from"] for e in categorized["FYI/NOISE"])
        print(f"  From: {', '.join(list(senders)[:5])}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Inbox triage via Microsoft Graph")
    parser.add_argument("--days", type=int, default=3, help="How many days back to scan (default: 3)")
    parser.add_argument("--top", type=int, default=50, help="Max emails to fetch (default: 50)")
    parser.add_argument("--unread", action="store_true", help="Only unread emails")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--client-id", default=os.getenv("OTF_GRAPH_CLIENT_ID"), help="Azure app client id")
    parser.add_argument("--token-cache", default=DEFAULT_TOKEN_CACHE, help="Token cache path")
    args = parser.parse_args()

    # Load env
    for env_path in [os.path.join(SCRIPT_DIR, ".env"),
                     os.path.expanduser("~/.openclaw/.env")]:
        load_dotenv(env_path)

    if not args.client_id:
        args.client_id = os.getenv("OTF_GRAPH_CLIENT_ID")
    if not args.client_id:
        sys.exit("No client ID. Set OTF_GRAPH_CLIENT_ID or pass --client-id.")

    token = get_token(args.client_id, cache_path=args.token_cache)
    messages = fetch_messages(token, since_days=args.days, top=args.top, unread_only=args.unread)
    format_output(messages, output_format=args.format)


if __name__ == "__main__":
    main()
