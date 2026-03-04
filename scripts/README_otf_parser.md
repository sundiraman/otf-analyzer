# OrangeTheory Email Parser (Graph OAuth)

## One-time setup

1. Copy env template:

```bash
cp scripts/.env.example scripts/.env
```

2. Edit `scripts/.env` and set your values:
- `OTF_GRAPH_CLIENT_ID`
- `OTF_GRAPH_TENANT=consumers` (for personal Hotmail/Outlook)
- `OTF_GRAPH_FOLDER` (exact folder name/path)

## First run (verify folder)

```bash
python3 scripts/otf_email_parser.py --graph --graph-list-folders --graph-include-hidden-folders
```

## Normal run

```bash
python3 scripts/otf_email_parser.py --graph --since-days 30
```

Outputs go to:
- `data/otf_classes.csv`
- `data/otf_report.md`

## If account is wrong

```bash
rm -f data/graph_token.json
```

Then run again and sign in with the correct Microsoft account.
