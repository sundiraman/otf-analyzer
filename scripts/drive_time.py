#!/usr/bin/env python3
"""
Drive Time Helper - Get real-time drive time using Google Maps Directions API.

Usage:
    python3 drive_time.py --origin "123 Main St, Kirkland WA 98033" --dest "SEA-TAC Airport Arrivals"
    python3 drive_time.py --origin "123 Main St, Kirkland WA" --dest "Seattle-Tacoma International Airport" --json

Environment:
    GOOGLE_MAPS_API_KEY   Alternative to --api-key
"""

import argparse
import json
import os
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlencode


DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


def get_drive_time(origin, destination, api_key, departure_time="now"):
    """Query Google Maps Directions API for drive time with traffic."""
    params = {
        "origin": origin,
        "destination": destination,
        "departure_time": departure_time,
        "key": api_key,
    }
    url = f"{DIRECTIONS_URL}?{urlencode(params)}"

    try:
        req = Request(url, headers={"User-Agent": "OpenClaw-DriveTime/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        return None, str(e)

    if data.get("status") != "OK":
        return None, f"API error: {data.get('status')} - {data.get('error_message', '')}"

    route = data["routes"][0]["legs"][0]

    result = {
        "duration_text": route["duration"]["text"],
        "duration_seconds": route["duration"]["value"],
        "duration_minutes": round(route["duration"]["value"] / 60),
    }

    # duration_in_traffic is only available with departure_time
    if "duration_in_traffic" in route:
        result["traffic_duration_text"] = route["duration_in_traffic"]["text"]
        result["traffic_duration_seconds"] = route["duration_in_traffic"]["value"]
        result["traffic_duration_minutes"] = round(route["duration_in_traffic"]["value"] / 60)

    result["distance_text"] = route["distance"]["text"]
    result["distance_meters"] = route["distance"]["value"]
    result["summary"] = data["routes"][0].get("summary", "")

    return result, None


def main():
    parser = argparse.ArgumentParser(description="Get real-time drive time")
    parser.add_argument("--origin", required=True, help="Starting address")
    parser.add_argument("--destination", "--dest", required=True, help="Destination address")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
                        help="Google Maps API key (or set GOOGLE_MAPS_API_KEY env)")
    parser.add_argument("--departure-time", default="now",
                        help="Departure time (unix timestamp or 'now')")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not args.api_key:
        print("Error: No API key. Use --api-key or set GOOGLE_MAPS_API_KEY", file=sys.stderr)
        sys.exit(1)

    result, err = get_drive_time(args.origin, args.destination, args.api_key, args.departure_time)

    if err:
        if args.json:
            print(json.dumps({"error": err}))
        else:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        traffic = result.get("traffic_duration_text", result["duration_text"])
        print(f"Drive time: {traffic} ({result['distance_text']})")
        if result.get("summary"):
            print(f"Route: {result['summary']}")


if __name__ == "__main__":
    main()
