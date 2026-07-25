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

This skill analyzes OrangeTheory Fitness (OTF) class data that has already been
extracted from the user's mailbox by `scripts/otf_email_parser.py`.

## Data location (relative to workspace root)

- Parser: `scripts/otf_email_parser.py`
- Structured data: `scripts/data/otf_classes.csv` — one row per class with columns:
  `id, date, subject, class_type, distance_miles, avg_hr, max_hr, splat_points,
  calories, zone_gray_min, zone_blue_min, zone_green_min, zone_orange_min,
  zone_red_min, source`
- Pre-built summaries (regenerated each parser run):
  - `scripts/data/otf_report.md` — quick markdown snapshot
  - `scripts/data/otf_report.html` — styled HTML report with stat cards and tables

## Step 1 — Refresh data if needed

If the user wants current/recent data and it may be stale, refresh first:

```bash
cd scripts && python3 otf_email_parser.py --graph --since-days 30
```

This requires `scripts/.env` (or exported env vars) with `OTF_GRAPH_CLIENT_ID`
set, and a browser-based device-code sign-in on first use (token cached at
`scripts/data/graph_token.json`, gitignored). If the user hasn't set this up,
fall back to whatever is already in `otf_classes.csv`.

## Step 2 — Load and analyze

Don't guess at trends from `otf_report.md` alone if deeper analysis is needed
— read `otf_classes.csv` directly (e.g. via `python3` with the `csv` module or
`pandas` if available) and compute what the user actually asked for. Useful
analyses:

- **Trend over time**: sort by `date`, plot/describe how distance, avg_hr, or
  splat_points change week over week or month over month.
- **Class-type comparison**: group by `class_type` (2G/3G/Strength/Power/
  Endurance/Tread 50/Unknown) and compare average distance, HR, efficiency
  (distance/avg_hr), splat points, calories.
- **HR zone breakdown**: `zone_gray_min` through `zone_red_min` show minutes
  spent in each heart-rate zone per class — useful for gauging workout
  intensity distribution (more orange/red = higher intensity).
- **Consistency/frequency**: count classes per week/month, gaps between
  sessions, longest streaks.
- **Personal records / anomalies**: max distance, max splat points, unusually
  low/high HR classes (could indicate under-recovery or a particularly hard
  class).
- **Efficiency trend**: distance per average-HR-beat over time as a rough
  fitness-improvement proxy (this is also computed per class-type in
  `otf_report.md`/`otf_report.html`).

Missing/blank numeric fields mean that metric wasn't found in the source email
— exclude them from averages rather than treating as zero.

## Step 3 — Present results

- For quick answers, summarize directly in chat (numbers + 1-2 sentence takeaway).
- If the user wants a visual/shareable artifact, point them to
  `scripts/data/otf_report.html` (or regenerate it by re-running the parser,
  which always rewrites both the markdown and HTML reports) rather than
  re-inventing report generation from scratch.
- Never fabricate workout data — if the CSV is empty or missing a metric,
  say so plainly instead of estimating.
