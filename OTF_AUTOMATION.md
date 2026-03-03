# OrangeTheory Email Automation (Outlook.com)

This parser ingests OrangeTheory emails and builds a dataset + summary report.

## 1) Outlook rule (one-time)

In Outlook.com:
1. Settings → Mail → Rules
2. New rule:
   - Condition: From contains `orangetheory`
   - Action: Move to folder (e.g. `OrangeTheory`)

## 2) Script location

- Parser: `scripts/otf_email_parser.py`
- Output CSV: `data/otf_classes.csv`
- Output report: `data/otf_report.md`

## 3) Run options

### Option A — Parse exported .eml files
If you export/save emails as `.eml` files:

```bash
python3 scripts/otf_email_parser.py --eml-dir /path/to/eml/files
```

### Option B — Pull directly from Outlook IMAP
Set environment vars:

```bash
export OTF_IMAP_USER="you@outlook.com"
export OTF_IMAP_PASSWORD="<app-password-or-password>"
export OTF_IMAP_FOLDER="OrangeTheory"
```

Then run:

```bash
python3 scripts/otf_email_parser.py --imap --since-days 90
```

## 4) What gets extracted

- Date
- Subject
- Inferred class type (2G/3G/Strength/Power/Endurance/ESP/Tread 50)
- Distance (miles)
- Avg HR / Max HR
- Splat points
- Calories
- HR zone minutes (gray/blue/green/orange/red)

## 5) Recurring automation (cron)

Example: run every day at 6:30am

```bash
crontab -e
```

Add:

```cron
30 6 * * * cd /home/homeautomation/.openclaw/workspace && /usr/bin/python3 scripts/otf_email_parser.py --imap --since-days 14 >> data/otf_cron.log 2>&1
```

## Notes

- If Outlook blocks password auth, use an app password (recommended with MFA).
- Parser is regex-based; share a few real email samples to tune extraction accuracy.
