# OpenClaw Automation

Personal automation toolkit powered by [OpenClaw](https://openclaw.ai/). A collection of Python scripts and OpenClaw skills for fitness tracking, email management, flight monitoring, and more.

## Scripts

| Script | Description |
|--------|-------------|
| `otf_email_parser.py` | Parse OrangeTheory workout emails (Graph/IMAP/EML) into CSV + reports |
| `otf_api_pull.py` | Pull OTF workouts directly via the otf-api library |
| `otf_tag_class.py` | Manually tag OTF class dates with a workout focus type |
| `inbox_triage.py` | Categorize recent emails into Action / Review / FYI / Noise |
| `deadline_watcher.py` | Scan inbox for deadline-related emails and extract due dates |
| `send_reminder.py` | Send reminder emails to family with auto-routing and rate limiting |
| `calendar_helper.py` | Read upcoming Outlook calendar events / create deadline events |
| `flight_tracker.py` | Track a flight in real-time via OpenSky Network for airport pickup |
| `flights_overhead.py` | List aircraft currently flying near a location |
| `drive_time.py` | Real-time drive time with traffic via Google Maps Directions API |
| `mountain_weather.py` | Hourly weather forecast along a GPX hiking route (Open-Meteo) |

## Skills

| Skill | Description |
|-------|-------------|
| `otf-analysis` | Analyze OTF workout history — trends, comparisons, HR zones |
| `flight-pickup-tracker` | Track flight + drive time, alert when to leave for pickup |
| `flights-overhead` | Identify aircraft / contrails overhead |

## Setup

1. Copy `scripts/.env.example` to `scripts/.env` and fill in your Microsoft Graph credentials.
2. Copy `scripts/family_config.example.json` to `scripts/family_config.json` for reminder routing.
3. Run `python3 scripts/otf_email_parser.py --graph` to authenticate via device-code flow (token cached locally).

See `scripts/README_otf_parser.md` for detailed OTF parser setup.

## Data

All scripts output to `scripts/data/`. Sensitive files (tokens, API keys, family config) are gitignored.
