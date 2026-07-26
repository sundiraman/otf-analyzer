#!/usr/bin/env python3
"""
Deadline Watcher — scans recent emails for deadlines and outputs
structured deadline info for reminder scheduling.

Looks for date/time patterns near deadline-related keywords.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing: requests")

GRAPH_API_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["Mail.Read", "Mail.Send", "Calendars.ReadWrite", "User.Read", "offline_access"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKEN_CACHE = os.path.join(SCRIPT_DIR, "data", "graph_token.json")
SEEN_FILE = os.path.join(SCRIPT_DIR, "data", "deadline_seen.json")


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

    sys.exit("No valid token. Run otf_email_parser.py --graph first.")


def extract_deadlines(subject, body_preview, received_date):
    """Try to extract deadline dates from email text."""
    text = f"{subject} {body_preview}".lower()
    
    # Deadline keyword proximity check
    deadline_keywords = ["due", "deadline", "by", "before", "must be received",
                         "submit by", "request due", "expires", "last day",
                         "needs to be received by"]
    
    has_deadline_signal = any(kw in text for kw in deadline_keywords)
    if not has_deadline_signal:
        return None

    # Try to find dates in the text
    full_text = f"{subject} {body_preview}"
    
    # Pattern: Month Day (e.g., "July 28", "Aug 15")
    month_names = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    
    # Match "Month Day" or "Month Day, Year"
    pattern = r'(' + '|'.join(month_names.keys()) + r')\w*[\s,]+(\d{1,2})(?:\s*,?\s*(\d{4}))?'
    matches = re.finditer(pattern, full_text, re.IGNORECASE)
    
    now = datetime.now(timezone.utc)
    recv = datetime.fromisoformat(received_date.replace('Z', '+00:00')) if received_date else now
    year = recv.year
    
    deadlines = []
    for m in matches:
        month_str = m.group(1).lower()
        day = int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else year
        
        month = month_names.get(month_str)
        if not month or day > 31:
            continue
        
        try:
            dt = datetime(yr, month, day, 23, 59, tzinfo=timezone.utc)
            # Only future deadlines (or very recent)
            if dt > now - timedelta(days=1):
                deadlines.append(dt)
        except ValueError:
            continue

    # Also try MM/DD or MM/DD/YY patterns
    slash_pattern = r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?'
    for m in re.finditer(slash_pattern, full_text):
        month = int(m.group(1))
        day = int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else year
        if yr < 100:
            yr += 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                dt = datetime(yr, month, day, 23, 59, tzinfo=timezone.utc)
                if dt > now - timedelta(days=1):
                    deadlines.append(dt)
            except ValueError:
                continue

    # Return the earliest future deadline
    if deadlines:
        return min(deadlines)
    return None


def get_full_body_text(token, msg_id):
    """Fetch full email body and strip HTML."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{GRAPH_API_ROOT}/me/messages/{msg_id}?$select=body",
                     headers=headers, timeout=30)
    if not r.ok:
        return ""
    body = r.json().get("body", {})
    content = body.get("content", "")
    # Strip HTML tags
    text = re.sub(r'<[^>]+>', ' ', content)
    text = re.sub(r'\s+', ' ', text)
    return text[:5000]


def scan_for_deadlines(token, since_days=2, top=50):
    """Fetch recent emails and find ones with deadlines."""
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$top": top,
        "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead",
    }
    
    r = requests.get(f"{GRAPH_API_ROOT}/me/mailFolders/inbox/messages",
                     headers=headers, params=params, timeout=30)
    if not r.ok:
        sys.exit(f"Graph API error: {r.status_code}")
    
    # Load seen deadlines
    seen = {}
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            seen = json.load(f)
    
    # Deadline signal keywords for initial filter
    signal_keywords = ["due", "deadline", "submit", "request", "expires", "last day",
                       "must be received", "register", "respond", "complete",
                       "form", "apply", "application", "rsvp", "confirm"]
    
    results = []
    for msg in r.json().get("value", []):
        msg_id = msg["id"]
        if msg_id in seen:
            continue
        
        subject = msg.get("subject", "")
        preview = msg.get("bodyPreview", "")
        combined = f"{subject} {preview}".lower()
        
        # First pass: check if email might contain a deadline
        has_signal = any(kw in combined for kw in signal_keywords)
        if not has_signal:
            continue
        
        # Try with preview first
        deadline = extract_deadlines(subject, preview, msg.get("receivedDateTime", ""))
        
        # If no deadline found in preview, fetch full body
        if not deadline:
            full_body = get_full_body_text(token, msg_id)
            deadline = extract_deadlines(subject, full_body, msg.get("receivedDateTime", ""))
        
        if deadline:
            sender = (msg.get("from", {}).get("emailAddress", {}).get("name")
                      or msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"))
            results.append({
                "id": msg_id,
                "subject": msg.get("subject", "(no subject)"),
                "from": sender,
                "received": msg.get("receivedDateTime", "")[:16],
                "deadline": deadline.isoformat(),
                "deadline_display": deadline.strftime("%A, %B %d at %I:%M %p"),
            })
            seen[msg_id] = deadline.isoformat()
    
    # Save seen
    os.makedirs(os.path.dirname(SEEN_FILE) or ".", exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Scan inbox for deadline emails")
    parser.add_argument("--days", type=int, default=2, help="Days back to scan")
    parser.add_argument("--top", type=int, default=50, help="Max emails")
    parser.add_argument("--client-id", default=os.getenv("OTF_GRAPH_CLIENT_ID"))
    parser.add_argument("--token-cache", default=DEFAULT_TOKEN_CACHE)
    parser.add_argument("--reset", action="store_true", help="Reset seen cache")
    args = parser.parse_args()

    for env_path in [os.path.join(SCRIPT_DIR, ".env"),
                     os.path.expanduser("~/.openclaw/.env")]:
        load_dotenv(env_path)

    if not args.client_id:
        args.client_id = os.getenv("OTF_GRAPH_CLIENT_ID")
    if not args.client_id:
        sys.exit("No client ID.")

    if args.reset and os.path.exists(SEEN_FILE):
        os.remove(SEEN_FILE)

    token = get_token(args.client_id, cache_path=args.token_cache)
    deadlines = scan_for_deadlines(token, since_days=args.days, top=args.top)
    
    print(json.dumps(deadlines, indent=2))


if __name__ == "__main__":
    main()
