#!/usr/bin/env python3
"""
Calendar Helper — read/create calendar events via Microsoft Graph API.

Used to:
- Check upcoming events (next N hours/days)
- Create deadline events from detected email deadlines
"""

import argparse
import json
import os
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
    if not os.path.exists(cache_path):
        sys.exit(f"Token cache not found: {cache_path}")

    with open(cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)

    now = datetime.now(timezone.utc).timestamp()
    if cache.get("access_token") and cache.get("expires_at", 0) > now + 60:
        return cache["access_token"]

    refresh_token = cache.get("refresh_token")
    if not refresh_token:
        sys.exit("No refresh token in cache.")

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

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    return tokens["access_token"]


def get_upcoming_events(token, hours=48):
    """Get calendar events in the next N hours."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours)

    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="America/Los_Angeles"'}
    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end.isoformat(),
        "$orderby": "start/dateTime",
        "$top": 20,
        "$select": "subject,start,end,location,isAllDay,bodyPreview",
    }

    resp = requests.get(
        f"{GRAPH_API_ROOT}/me/calendarView",
        headers=headers, params=params, timeout=30,
    )
    if not resp.ok:
        sys.exit(f"Calendar API error ({resp.status_code}): {resp.text}")

    events = []
    for ev in resp.json().get("value", []):
        events.append({
            "subject": ev.get("subject", "(no subject)"),
            "start": ev.get("start", {}).get("dateTime", ""),
            "end": ev.get("end", {}).get("dateTime", ""),
            "allDay": ev.get("isAllDay", False),
            "location": ev.get("location", {}).get("displayName", ""),
        })
    return events


def create_deadline_event(token, subject, deadline_iso, description="", reminder_minutes=60):
    """Create an all-day event on the deadline date with a reminder."""
    deadline = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))

    # All-day event on deadline date
    date_str = deadline.strftime("%Y-%m-%d")
    end_date = (deadline + timedelta(days=1)).strftime("%Y-%m-%d")

    payload = {
        "subject": f"⏰ DEADLINE: {subject}",
        "body": {
            "contentType": "Text",
            "content": description or f"Deadline detected from email: {subject}",
        },
        "start": {"dateTime": date_str, "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": end_date, "timeZone": "America/Los_Angeles"},
        "isAllDay": True,
        "isReminderOn": True,
        "reminderMinutesBeforeStart": reminder_minutes,
        "showAs": "free",
        "categories": ["Deadline"],
    }

    resp = requests.post(
        f"{GRAPH_API_ROOT}/me/events",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if resp.status_code in (200, 201):
        event_id = resp.json().get("id", "")
        print(f"✓ Created calendar event: ⏰ DEADLINE: {subject} on {date_str}")
        return event_id
    else:
        print(f"✗ Failed to create event ({resp.status_code}): {resp.text}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Calendar helper for Graph API")
    sub = parser.add_subparsers(dest="command")

    # Upcoming events
    upcoming = sub.add_parser("upcoming", help="List upcoming events")
    upcoming.add_argument("--hours", type=int, default=48)

    # Create deadline event
    create = sub.add_parser("create-deadline", help="Create a deadline calendar event")
    create.add_argument("--subject", required=True)
    create.add_argument("--deadline", required=True, help="ISO date/datetime")
    create.add_argument("--description", default="")
    create.add_argument("--reminder-minutes", type=int, default=60)

    parser.add_argument("--client-id", default=os.getenv("OTF_GRAPH_CLIENT_ID"))
    parser.add_argument("--token-cache", default=DEFAULT_TOKEN_CACHE)

    args = parser.parse_args()

    for env_path in [os.path.join(SCRIPT_DIR, ".env"),
                     os.path.expanduser("~/.openclaw/.env")]:
        load_dotenv(env_path)

    if not args.client_id:
        args.client_id = os.getenv("OTF_GRAPH_CLIENT_ID")
    if not args.client_id:
        sys.exit("No client ID. Set OTF_GRAPH_CLIENT_ID in .env")

    token = get_token(args.client_id, cache_path=args.token_cache)

    if args.command == "upcoming":
        events = get_upcoming_events(token, hours=args.hours)
        print(json.dumps(events, indent=2))
    elif args.command == "create-deadline":
        create_deadline_event(token, args.subject, args.deadline, args.description, args.reminder_minutes)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
