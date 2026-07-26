#!/usr/bin/env python3
"""
Mountain Route Weather - Get hourly weather forecast along a GPX route.

Parses a GPX file, samples key waypoints, estimates timing based on pace,
and queries Open-Meteo for hourly conditions at each point.

Usage:
    python3 mountain_weather.py --gpx route.gpx --date 2026-07-28
    python3 mountain_weather.py --gpx route.gpx --date 2026-07-28 --pace slow
    python3 mountain_weather.py --gpx route.gpx --date 2026-07-28 --start-hour 3 --json

Pace profiles (vertical gain rate):
    elite:    450 m/hr up, 700 m/hr down
    moderate: 300 m/hr up, 500 m/hr down (default)
    slow:     200 m/hr up, 400 m/hr down
"""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlencode

PACE_PROFILES = {
    "elite": {"up_mhr": 450, "down_mhr": 700, "flat_kmh": 5.5},
    "moderate": {"up_mhr": 300, "down_mhr": 500, "flat_kmh": 4.0},
    "slow": {"up_mhr": 200, "down_mhr": 400, "flat_kmh": 3.0},
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def parse_gpx(gpx_path, ascent_only=True):
    """Parse GPX file and return list of trackpoints with lat, lng, ele, name.
    
    If ascent_only=True, detects the summit (highest elevation point) and
    returns only the ascent portion. Handles out-and-back routes from Garmin etc.
    """
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    
    # Handle namespace
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    
    points = []
    for trkpt in root.findall(".//gpx:trkpt", ns):
        lat = float(trkpt.get("lat"))
        lng = float(trkpt.get("lon"))
        ele_el = trkpt.find("gpx:ele", ns)
        ele = float(ele_el.text) if ele_el is not None else None
        name_el = trkpt.find("gpx:name", ns)
        name = name_el.text if name_el is not None else None
        points.append({"lat": lat, "lng": lng, "ele": ele, "name": name})
    
    if ascent_only and points:
        # Find summit (highest elevation point)
        summit_idx = max(range(len(points)), key=lambda i: points[i]["ele"] or 0)
        points = points[:summit_idx + 1]
        # Label the summit
        if not points[-1].get("name"):
            points[-1]["name"] = "Summit"
    
    return points


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points in km."""
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng/2)**2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def estimate_segment_time_hr(dist_km, ele_gain_m, ele_loss_m, pace):
    """Estimate time for a segment considering distance and elevation change."""
    p = PACE_PROFILES[pace]
    
    # Time from elevation gain
    time_up = ele_gain_m / p["up_mhr"] if ele_gain_m > 0 else 0
    # Time from elevation loss
    time_down = ele_loss_m / p["down_mhr"] if ele_loss_m > 0 else 0
    # Time from horizontal distance (only the flat component)
    time_flat = dist_km / p["flat_kmh"]
    
    # Use the larger of vertical time or flat time (they overlap)
    return max(time_up + time_down, time_flat)


def compute_route_timing(points, pace, start_hour=3):
    """Compute estimated arrival time at each point."""
    timed_points = []
    cumulative_hr = 0.0
    
    for i, pt in enumerate(points):
        if i == 0:
            pt["time_hr"] = 0.0
            pt["cumulative_hr"] = 0.0
            timed_points.append(pt)
            continue
        
        prev = points[i-1]
        dist = haversine_km(prev["lat"], prev["lng"], pt["lat"], pt["lng"])
        ele_gain = max(0, (pt["ele"] or 0) - (prev["ele"] or 0))
        ele_loss = max(0, (prev["ele"] or 0) - (pt["ele"] or 0))
        
        seg_time = estimate_segment_time_hr(dist, ele_gain, ele_loss, pace)
        cumulative_hr += seg_time
        
        pt["time_hr"] = seg_time
        pt["cumulative_hr"] = cumulative_hr
        timed_points.append(pt)
    
    return timed_points


def sample_key_points(points, max_points=8):
    """Sample key waypoints: named points + evenly spaced by time."""
    if len(points) <= max_points:
        return points
    
    # Always include first, last, and named points
    key = [points[0], points[-1]]
    named = [p for p in points[1:-1] if p.get("name")]
    key.extend(named)
    
    # Fill remaining slots evenly by cumulative time
    remaining = max_points - len(key)
    if remaining > 0 and len(points) > 2:
        total_time = points[-1]["cumulative_hr"]
        interval = total_time / (remaining + 1)
        for i in range(1, remaining + 1):
            target_time = interval * i
            closest = min(points, key=lambda p: abs(p["cumulative_hr"] - target_time))
            if closest not in key:
                key.append(closest)
    
    # Sort by cumulative time
    key.sort(key=lambda p: p["cumulative_hr"])
    return key


def fetch_weather(lat, lng, ele, date, timezone="America/Los_Angeles"):
    """Fetch hourly weather from Open-Meteo for a specific point and date."""
    params = {
        "latitude": lat,
        "longitude": lng,
        "elevation": ele,
        "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,snowfall,cloud_cover,freezing_level_height,visibility",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": timezone,
        "start_date": date,
        "end_date": date,
    }
    url = f"{OPEN_METEO_URL}?{urlencode(params)}"
    
    try:
        req = Request(url, headers={"User-Agent": "OpenClaw-MountainWeather/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        return None, str(e)
    
    return data.get("hourly"), None


def get_weather_at_hour(hourly_data, hour):
    """Extract weather at a specific hour from hourly data."""
    if not hourly_data or hour < 0 or hour >= len(hourly_data.get("temperature_2m", [])):
        return None
    
    return {
        "temp_f": hourly_data["temperature_2m"][hour],
        "wind_mph": hourly_data["wind_speed_10m"][hour],
        "wind_gust_mph": hourly_data["wind_gusts_10m"][hour],
        "precip_in": hourly_data["precipitation"][hour],
        "snowfall_cm": hourly_data["snowfall"][hour],
        "cloud_pct": hourly_data["cloud_cover"][hour],
        "freezing_level_ft": round(hourly_data["freezing_level_height"][hour] * 3.281) if hourly_data["freezing_level_height"][hour] else None,
        "visibility_m": hourly_data.get("visibility", [None]*24)[hour],
    }


def find_best_start(points, hourly_data_summit, pace, date):
    """Find the best start hour to minimize summit wind/cloud."""
    summit = points[-1]
    summit_time_hr = summit["cumulative_hr"]
    
    best_start = None
    best_score = float("inf")
    
    # Try start hours from 1 AM to 6 AM
    for start in range(1, 7):
        summit_hour = int(start + summit_time_hr)
        if summit_hour >= 24:
            continue
        
        w = get_weather_at_hour(hourly_data_summit, summit_hour)
        if not w:
            continue
        
        # Score: lower is better (wind + cloud penalty)
        score = w["wind_mph"] * 2 + w["wind_gust_mph"] + w["cloud_pct"] * 0.3
        # Penalize precip heavily
        if w["precip_in"] and w["precip_in"] > 0:
            score += 1000
        
        if score < best_score:
            best_score = score
            best_start = start
    
    return best_start


def main():
    parser = argparse.ArgumentParser(description="Mountain route weather forecast")
    parser.add_argument("--gpx", required=True, help="GPX file path")
    parser.add_argument("--date", required=True, help="Forecast date (YYYY-MM-DD)")
    parser.add_argument("--pace", default="moderate", choices=["elite", "moderate", "slow"],
                        help="Climbing pace (default: moderate)")
    parser.add_argument("--start-hour", type=int, default=None,
                        help="Start hour (0-23). If omitted, auto-picks best window.")
    parser.add_argument("--timezone", default="America/Los_Angeles",
                        help="Timezone (default: America/Los_Angeles)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    # Parse GPX
    points = parse_gpx(args.gpx)
    if not points:
        print("Error: No trackpoints found in GPX file", file=sys.stderr)
        sys.exit(1)
    
    # Compute timing
    timed_points = compute_route_timing(points, args.pace)
    
    # Sample key waypoints
    key_points = sample_key_points(timed_points, max_points=8)
    
    # Fetch weather for summit to determine best start
    summit = timed_points[-1]
    summit_weather, err = fetch_weather(summit["lat"], summit["lng"], summit["ele"], args.date, args.timezone)
    if err:
        print(f"Error fetching weather: {err}", file=sys.stderr)
        sys.exit(1)
    
    # Determine start hour
    if args.start_hour is not None:
        start_hour = args.start_hour
    else:
        start_hour = find_best_start(timed_points, summit_weather, args.pace, args.date)
        if start_hour is None:
            start_hour = 3  # Default alpine start
    
    # Fetch weather for each key point and compute conditions at arrival time
    results = []
    for pt in key_points:
        arrival_hour = int(start_hour + pt["cumulative_hr"])
        if arrival_hour >= 24:
            arrival_hour = 23
        
        hourly, err = fetch_weather(pt["lat"], pt["lng"], pt["ele"], args.date, args.timezone)
        if err:
            results.append({"point": pt, "error": err})
            continue
        
        weather = get_weather_at_hour(hourly, arrival_hour)
        
        results.append({
            "name": pt.get("name") or f"{round(pt['ele'])}m",
            "elevation_m": round(pt["ele"]),
            "elevation_ft": round(pt["ele"] * 3.281),
            "arrival_hour": arrival_hour,
            "arrival_time": f"{arrival_hour:02d}:00",
            "cumulative_hr": round(pt["cumulative_hr"], 1),
            "weather": weather,
        })
    
    # Compute descent timing
    total_up_hr = timed_points[-1]["cumulative_hr"]
    # Descent is roughly 60% of ascent time
    descent_hr = total_up_hr * 0.6
    back_at_camp_hour = int(start_hour + total_up_hr + descent_hr)
    
    output = {
        "route": args.gpx,
        "date": args.date,
        "pace": args.pace,
        "recommended_start": f"{start_hour:02d}:00",
        "estimated_summit_time": f"{int(start_hour + total_up_hr):02d}:00",
        "estimated_return": f"{min(back_at_camp_hour, 23):02d}:00",
        "total_ascent_hr": round(total_up_hr, 1),
        "waypoints": results,
    }
    
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"\n🏔️  Route: {args.gpx}")
        print(f"📅  Date: {args.date} | Pace: {args.pace}")
        print(f"⏰  Recommended start: {output['recommended_start']}")
        print(f"🔝  Summit by: {output['estimated_summit_time']}")
        print(f"🏕️  Back at camp: {output['estimated_return']}")
        print(f"\n{'Time':<8} {'Location':<25} {'Elev':<12} {'Temp':<8} {'Wind':<14} {'Cloud':<8} {'Precip'}")
        print("-" * 95)
        
        for r in results:
            w = r.get("weather")
            if not w:
                print(f"{r['arrival_time']:<8} {r['name']:<25} {r['elevation_ft']} ft     [no data]")
                continue
            wind_str = f"{w['wind_mph']:.0f} ({w['wind_gust_mph']:.0f}g) mph"
            precip_str = f"{w['precip_in']:.2f}\"" if w['precip_in'] else "None"
            print(f"{r['arrival_time']:<8} {r['name']:<25} {r['elevation_ft']:<7} ft  {w['temp_f']:<6.0f}°F {wind_str:<14} {w['cloud_pct']:<6}%  {precip_str}")
        
        # Warnings
        summit_w = results[-1].get("weather") if results else None
        if summit_w:
            print(f"\n📊 Summit conditions at {results[-1]['arrival_time']}:")
            print(f"   Freezing level: {summit_w['freezing_level_ft']} ft")
            if summit_w["wind_gust_mph"] > 30:
                print(f"   ⚠️  HIGH WIND WARNING: Gusts to {summit_w['wind_gust_mph']:.0f} mph on summit")
            if summit_w["precip_in"] and summit_w["precip_in"] > 0:
                print(f"   ⚠️  PRECIPITATION expected at summit")
            if summit_w["cloud_pct"] > 80:
                print(f"   ⚠️  LOW VISIBILITY likely (cloud cover {summit_w['cloud_pct']}%)")


if __name__ == "__main__":
    main()
