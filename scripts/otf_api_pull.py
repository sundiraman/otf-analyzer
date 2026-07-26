#!/usr/bin/env python3
"""Pull OTF workout history via the otf-api library and output to CSV.

Usage:
    python3 otf_api_pull.py [--output PATH] [--format csv|json]

Requires OTF_EMAIL and OTF_PASSWORD in environment or scripts/.env
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

# Load .env if present
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from otf_api import Otf, OtfUser


def get_client() -> Otf:
    email = os.environ.get("OTF_EMAIL")
    password = os.environ.get("OTF_PASSWORD")
    if not email or not password:
        print("ERROR: Set OTF_EMAIL and OTF_PASSWORD in environment or .env", file=sys.stderr)
        sys.exit(1)
    return Otf(user=OtfUser(email, password))


def extract_metric(data, key, sub="metric_value"):
    """Safely extract a nested metric value."""
    if data is None:
        return ""
    obj = getattr(data, key, None) if not isinstance(data, dict) else data.get(key)
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return obj.get(sub, "")
    if hasattr(obj, sub):
        return getattr(obj, sub, "")
    # It might be a pydantic model with .metric_value
    if hasattr(obj, "metric_value"):
        return obj.metric_value or ""
    return ""


def pull_workouts(otf: Otf, start_date: str = "2020-01-01") -> list[dict]:
    workouts = otf.workouts.get_workouts(start_date=start_date)
    rows = []
    for w in workouts:
        cls = w.otf_class
        zone = w.zone_time_minutes
        hr = w.heart_rate
        tread = w.treadmill_data
        rower = w.rower_data

        row = {
            "date": cls.starts_at.strftime("%Y-%m-%d") if cls and cls.starts_at else "",
            "time": cls.starts_at.strftime("%H:%M") if cls and cls.starts_at else "",
            "class_name": cls.name if cls else "",
            "class_type": str(cls.class_type.value) if cls and cls.class_type else "",
            "coach": w.coach or (cls.coach if cls else ""),
            "studio": cls.studio.name if cls and cls.studio else "",
            "calories": w.calories_burned or "",
            "splat_points": w.splat_points or "",
            "avg_hr": hr.avg_hr if hr else "",
            "max_hr": hr.max_hr if hr else "",
            "peak_hr": hr.peak_hr if hr else "",
            "avg_hr_percent": hr.avg_hr_percent if hr else "",
            "peak_hr_percent": hr.peak_hr_percent if hr else "",
            "zone_gray_min": zone.gray if zone else "",
            "zone_blue_min": zone.blue if zone else "",
            "zone_green_min": zone.green if zone else "",
            "zone_orange_min": zone.orange if zone else "",
            "zone_red_min": zone.red if zone else "",
            "active_time_sec": w.active_time_seconds or "",
            "step_count": w.step_count or "",
            # Treadmill
            "tread_distance_mi": extract_metric(tread, "total_distance"),
            "tread_avg_speed_mph": extract_metric(tread, "avg_speed"),
            "tread_max_speed_mph": extract_metric(tread, "max_speed"),
            "tread_avg_incline": extract_metric(tread, "avg_incline"),
            "tread_max_incline": extract_metric(tread, "max_incline"),
            "tread_elevation_ft": extract_metric(tread, "elevation_gained"),
            "tread_moving_time_sec": extract_metric(tread, "moving_time"),
            # Rower
            "rower_distance_m": extract_metric(rower, "total_distance"),
            "rower_avg_power_w": extract_metric(rower, "avg_power"),
            "rower_avg_cadence": extract_metric(rower, "avg_cadence"),
            "rower_max_cadence": extract_metric(rower, "max_cadence"),
            "rower_moving_time_sec": extract_metric(rower, "moving_time"),
        }
        rows.append(row)

    # Sort by date ascending
    rows.sort(key=lambda r: r["date"])
    return rows


def write_csv(rows: list[dict], path: str):
    if not rows:
        print("No workouts found.")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} workouts to {path}")


def main():
    parser = argparse.ArgumentParser(description="Pull OTF workouts via API")
    parser.add_argument("--output", "-o", default="data/otf_workouts_api.csv",
                        help="Output file path (default: data/otf_workouts_api.csv)")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--since", default="2020-01-01",
                        help="Start date YYYY-MM-DD (default: 2020-01-01, pulls full history)")
    args = parser.parse_args()

    otf = get_client()
    rows = pull_workouts(otf, start_date=args.since)

    if args.format == "json":
        out = args.output.replace(".csv", ".json") if args.output.endswith(".csv") else args.output
        with open(out, "w") as f:
            json.dump(rows, f, indent=2, default=str)
        print(f"Wrote {len(rows)} workouts to {out}")
    else:
        write_csv(rows, args.output)


if __name__ == "__main__":
    main()
