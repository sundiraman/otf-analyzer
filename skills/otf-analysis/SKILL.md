---
name: otf-analysis
description: Analyze OrangeTheory Fitness (OTF) workout history parsed from the user's mailbox — trends in distance, heart rate, splat points, calories, and HR zone minutes. Use when the user asks about their OTF workouts, fitness progress, splat points, heart-rate zones, class-type comparisons, or wants a fresh report/refresh of their OTF data.
metadata:
  {
    "openclaw":
      {
        "emoji": "🏃",
        "requires": { "bins": ["python3"] },
      },
  }
---

# OTF Analysis

This skill analyzes OrangeTheory Fitness (OTF) class data pulled via the OTF
API (`scripts/otf_api_pull.py`) or, as a fallback, parsed from email
(`scripts/otf_email_parser.py`).

## Data Sources

### Primary: OTF API

- Script: `scripts/otf_api_pull.py`
- Output: `scripts/data/otf_workouts_api.csv`
- Requires: `OTF_EMAIL` and `OTF_PASSWORD` in `scripts/.env`
- Dependency: `otf-api` pip package (already installed)

CSV columns:
`date, time, class_name, class_type, coach, studio, calories, splat_points,
avg_hr, max_hr, peak_hr, avg_hr_percent, peak_hr_percent,
zone_gray_min, zone_blue_min, zone_green_min, zone_orange_min, zone_red_min,
active_time_sec, step_count,
tread_distance_mi, tread_avg_speed_mph, tread_max_speed_mph,
tread_avg_incline, tread_max_incline, tread_elevation_ft, tread_moving_time_sec,
rower_distance_m, rower_avg_power_w, rower_avg_cadence, rower_max_cadence,
rower_moving_time_sec`

### Fallback: Email Parser

- Script: `scripts/otf_email_parser.py`
- Output: `scripts/data/otf_classes.csv`
- Use when API credentials aren't configured or API is unavailable.

### Manual Class-Focus Tags

- File: `scripts/data/otf_class_tags.csv`
- Columns: `date,focus` (values: Strength, Power, Endurance, ESP, Other)
- Tagging script: `scripts/otf_tag_class.py --date YYYY-MM-DD --focus <type>`
- Merge with `otf_workouts_api.csv` on the `date` column to enrich analysis
  with workout focus/template information.

## Step 1 — Determine the date range the user needs

Figure out the earliest date the query requires *before* deciding whether to
refresh. Check `scripts/data/otf_workouts_api.csv` first — if rows already
cover the requested range, no refresh needed.

## Step 2 — Refresh data if needed

```bash
cd scripts && python3 otf_api_pull.py
```

This pulls the latest workout history from the OTF API. If credentials are
missing or the API fails, fall back to whatever is already in the CSV and say
so. For email-based fallback:

```bash
cd scripts && python3 otf_email_parser.py --graph --since-days <N>
```

## Step 3 — Load and analyze

Read `scripts/data/otf_workouts_api.csv` directly (with pandas or csv module).
Merge with `scripts/data/otf_class_tags.csv` on `date` if the tags file exists
to add the `focus` column. Useful analyses:

- **Trend over time**: distance, avg_hr, splat_points week-over-week or
  month-over-month.
- **Period comparison**: bucket by month or user-specified ranges, compare
  totals/averages.
- **Class-type / focus comparison**: group by `class_type` or merged `focus`
  tag and compare metrics.
- **Treadmill breakdown**: tread_distance_mi, avg/max speed, incline trends,
  elevation gain over time. Good for tracking running improvement.
- **Rower breakdown**: rower_distance_m, avg_power_w, cadence trends. Good
  for tracking rowing power gains.
- **HR zone breakdown**: zone minutes per class for intensity distribution.
- **Consistency/frequency**: classes per week/month, gaps, streaks.
- **Personal records**: max tread distance, max rower distance, max splat
  points, max speed, max power.
- **Coach comparison**: group by `coach` for performance differences.
- **Efficiency trend**: tread distance per avg HR or per active time as a
  fitness proxy.

Missing/blank numeric fields → exclude from averages, don't treat as zero.

## Step 4 — Present results

- Quick answers: summarize in chat with numbers + takeaway.
- For visuals, point to `scripts/data/otf_report.html` or generate plots
  with matplotlib if available.
- Never fabricate data.
