#!/usr/bin/env python3
"""
Flights Overhead - Show flights currently above a location.

Uses OpenSky Network (free, no API key) to query all aircraft
within a bounding box around a given latitude/longitude.

Usage:
    python3 flights_overhead.py --lat 47.68 --lng -122.21
    python3 flights_overhead.py --lat 47.68 --lng -122.21 --radius 30 --json
    python3 flights_overhead.py --lat 47.68 --lng -122.21 --limit 5
"""

import argparse
import json
import math
import sys
from urllib.request import urlopen, Request
from urllib.error import URLError


OPENSKY_BASE = "https://opensky-network.org/api"

# ICAO prefix -> airline name + IATA code
AIRLINE_NAMES = {
    "ASA": ("Alaska Airlines", "AS"),
    "AAL": ("American Airlines", "AA"),
    "DAL": ("Delta Air Lines", "DL"),
    "UAL": ("United Airlines", "UA"),
    "SWA": ("Southwest Airlines", "WN"),
    "JBU": ("JetBlue", "B6"),
    "NKS": ("Spirit Airlines", "NK"),
    "FFT": ("Frontier Airlines", "F9"),
    "HAL": ("Hawaiian Airlines", "HA"),
    "SCX": ("Sun Country", "SY"),
    "QXE": ("Horizon Air", "QX"),
    "SKW": ("SkyWest Airlines", "OO"),
    "AAY": ("Allegiant Air", "G4"),
    "DLH": ("Lufthansa", "LH"),
    "AIC": ("Air India", "AI"),
    "SIA": ("Singapore Airlines", "SQ"),
    "UAE": ("Emirates", "EK"),
    "QTR": ("Qatar Airways", "QR"),
    "KLM": ("KLM", "KL"),
    "THA": ("Thai Airways", "TG"),
    "ANA": ("ANA", "NH"),
    "BAW": ("British Airways", "BA"),
    "AFR": ("Air France", "AF"),
    "THY": ("Turkish Airlines", "TK"),
    "ETD": ("Etihad Airways", "EY"),
    "CPA": ("Cathay Pacific", "CX"),
    "JAL": ("Japan Airlines", "JL"),
    "IGO": ("IndiGo", "6E"),
    "AXB": ("Air India Express", "IX"),
    "FDX": ("FedEx", "FX"),
    "UPS": ("UPS Airlines", "5X"),
    "GTI": ("Atlas Air", "5Y"),
    "ACA": ("Air Canada", "AC"),
    "WJA": ("WestJet", "WS"),
    "QFA": ("Qantas", "QF"),
    "ANZ": ("Air New Zealand", "NZ"),
    "CCA": ("Air China", "CA"),
    "CES": ("China Eastern", "MU"),
    "CSN": ("China Southern", "CZ"),
    "KAL": ("Korean Air", "KE"),
    "AAR": ("Asiana Airlines", "OZ"),
    "EVA": ("EVA Air", "BR"),
    "PAL": ("Philippine Airlines", "PR"),
}


def bbox_from_center(lat, lng, radius_km):
    """Calculate bounding box from center point and radius in km."""
    # Approximate degrees per km
    lat_delta = radius_km / 111.0
    lng_delta = radius_km / (111.0 * math.cos(math.radians(lat)))
    return (
        lat - lat_delta,  # south
        lng - lng_delta,  # west
        lat + lat_delta,  # north
        lng + lng_delta,  # east
    )


def get_flights_in_area(lat, lng, radius_km=50):
    """Query OpenSky for all flights in a bounding box."""
    south, west, north, east = bbox_from_center(lat, lng, radius_km)
    url = (f"{OPENSKY_BASE}/states/all"
           f"?lamin={south}&lomin={west}&lamax={north}&lomax={east}")

    try:
        req = Request(url, headers={"User-Agent": "OpenClaw-FlightsOverhead/1.0"})
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        return None, str(e)

    states = data.get("states")
    if not states:
        return [], None

    flights = []
    for s in states:
        callsign = (s[1] or "").strip()
        on_ground = s[8]

        if on_ground:
            continue  # Skip aircraft on ground

        alt_m = s[7] or s[13]  # baro or geo altitude
        alt_ft = round(alt_m * 3.281) if alt_m else None
        speed_kmh = round(s[9] * 3.6, 1) if s[9] else None
        v_speed = s[11]

        # Try to identify airline
        airline_name = None
        flight_number = None
        if callsign and len(callsign) >= 4:
            prefix = callsign[:3]
            if prefix in AIRLINE_NAMES:
                airline_name, iata = AIRLINE_NAMES[prefix]
                number = callsign[3:]
                flight_number = f"{iata}{number}"

        # Calculate distance from observer
        flight_lat = s[6]
        flight_lng = s[5]
        distance_km = None
        slant_distance_km = None
        if flight_lat and flight_lng:
            dlat = math.radians(flight_lat - lat)
            dlng = math.radians(flight_lng - lng)
            a = (math.sin(dlat/2)**2 +
                 math.cos(math.radians(lat)) * math.cos(math.radians(flight_lat)) *
                 math.sin(dlng/2)**2)
            distance_km = round(6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)), 1)
            # 3D slant distance (accounts for altitude)
            if alt_m:
                slant_distance_km = round(math.sqrt(distance_km**2 + (alt_m/1000)**2), 1)

        flights.append({
            "callsign": callsign,
            "airline": airline_name,
            "flight_number": flight_number,
            "lat": flight_lat,
            "lng": flight_lng,
            "altitude_ft": alt_ft,
            "altitude_m": round(alt_m) if alt_m else None,
            "speed_kmh": speed_kmh,
            "heading": round(s[10]) if s[10] else None,
            "vertical_rate_ms": v_speed,
            "distance_km": distance_km,
            "slant_distance_km": slant_distance_km,
            "origin_country": s[2],
        })

    # Sort by slant distance (true 3D distance from observer)
    flights.sort(key=lambda f: f.get("slant_distance_km") or f.get("distance_km") or 9999)
    return flights, None


def main():
    parser = argparse.ArgumentParser(description="Show flights overhead a location")
    parser.add_argument("--lat", type=float, required=True, help="Latitude")
    parser.add_argument("--lng", type=float, required=True, help="Longitude")
    parser.add_argument("--radius", type=float, default=20,
                        help="Search radius in km (default 20)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max flights to show (default 20)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    flights, err = get_flights_in_area(args.lat, args.lng, args.radius)

    if err:
        if args.json:
            print(json.dumps({"error": err}))
        else:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    flights = flights[:args.limit]

    if args.json:
        print(json.dumps({"count": len(flights), "flights": flights}, indent=2))
    else:
        if not flights:
            print("No flights overhead right now.")
            sys.exit(0)

        print(f"Found {len(flights)} flights within {args.radius} km:\n")
        for f in flights:
            airline = f["airline"] or "Unknown"
            call = f["flight_number"] or f["callsign"] or "?"
            alt = f"{f['altitude_ft']:,} ft" if f["altitude_ft"] else "?"
            dist = f"{f['distance_km']} km" if f["distance_km"] else "?"
            speed = f"{f['speed_kmh']} km/h" if f["speed_kmh"] else "?"
            heading = f"{f['heading']}°" if f["heading"] else "?"
            print(f"  {call:<10} {airline:<22} alt: {alt:<12} dist: {dist:<10} spd: {speed:<12} hdg: {heading}")


if __name__ == "__main__":
    main()
