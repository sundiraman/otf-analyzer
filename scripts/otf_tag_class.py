#!/usr/bin/env python3
"""Tag an OTF class date with a focus type (Strength, Power, Endurance, ESP, Other)."""

import argparse
import csv
import os
from pathlib import Path

TAGS_FILE = Path(__file__).parent / "data" / "otf_class_tags.csv"
VALID_FOCUS = {"Strength", "Power", "Endurance", "ESP", "Other"}


def main():
    parser = argparse.ArgumentParser(description="Tag OTF class focus by date")
    parser.add_argument("--date", required=True, help="Class date YYYY-MM-DD")
    parser.add_argument("--focus", required=True, choices=sorted(VALID_FOCUS),
                        help="Workout focus type")
    args = parser.parse_args()

    # Read existing tags
    rows = {}
    if TAGS_FILE.exists():
        with open(TAGS_FILE, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date"):
                    rows[row["date"]] = row["focus"]

    # Update/add
    rows[args.date] = args.focus

    # Write back
    TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TAGS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "focus"])
        for date in sorted(rows.keys()):
            writer.writerow([date, rows[date]])

    print(f"Tagged {args.date} → {args.focus}")


if __name__ == "__main__":
    main()
