#!/usr/bin/env python3
import argparse
import csv
import email
import hashlib
import imaplib
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993

FIELDNAMES = [
    "id",
    "date",
    "subject",
    "class_type",
    "distance_miles",
    "avg_hr",
    "max_hr",
    "splat_points",
    "calories",
    "zone_gray_min",
    "zone_blue_min",
    "zone_green_min",
    "zone_orange_min",
    "zone_red_min",
    "source",
]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_email_body(msg) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode("utf-8", "ignore"))
        return "\n".join(parts)
    try:
        return msg.get_content()
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        return payload.decode("utf-8", "ignore")


def first_match(patterns, text, cast=float):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(",", "").strip()
            try:
                return cast(val)
            except Exception:
                continue
    return ""


def infer_class_type(subject: str, body: str) -> str:
    hay = f"{subject} {body}".lower()
    labels = []
    if "2g" in hay:
        labels.append("2G")
    if "3g" in hay:
        labels.append("3G")
    for key in ["strength 50", "tread 50", "strength", "power", "endurance", "esp"]:
        if key in hay:
            labels.append(key.title())
    return ", ".join(dict.fromkeys(labels)) if labels else "Unknown"


def parse_metrics(subject: str, body: str, source: str, date_hdr: str = ""):
    text = clean_text(body)
    distance = first_match([
        r"distance\s*(?:run|ran)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mi|miles)",
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:mi|miles)\s*(?:distance|run|ran)",
    ], text)
    avg_hr = first_match([r"avg(?:\.|erage)?\s*hr\s*[:\-]?\s*([0-9]{2,3})", r"average heart rate\s*[:\-]?\s*([0-9]{2,3})"], text, int)
    max_hr = first_match([r"max\s*hr\s*[:\-]?\s*([0-9]{2,3})", r"maximum heart rate\s*[:\-]?\s*([0-9]{2,3})"], text, int)
    splat = first_match([r"splat\s*points?\s*[:\-]?\s*([0-9]{1,3})"], text, int)
    calories = first_match([r"calories\s*(?:burned)?\s*[:\-]?\s*([0-9]{2,4})"], text, int)

    zone_gray = first_match([r"gray\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_blue = first_match([r"blue\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_green = first_match([r"green\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_orange = first_match([r"orange\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_red = first_match([r"red\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)

    dt = ""
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr).isoformat()
        except Exception:
            dt = ""

    digest_src = f"{subject}|{date_hdr}|{source}|{distance}|{avg_hr}|{max_hr}|{splat}"
    row_id = hashlib.sha1(digest_src.encode()).hexdigest()[:12]

    return {
        "id": row_id,
        "date": dt,
        "subject": clean_text(subject),
        "class_type": infer_class_type(subject, text),
        "distance_miles": distance,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "splat_points": splat,
        "calories": calories,
        "zone_gray_min": zone_gray,
        "zone_blue_min": zone_blue,
        "zone_green_min": zone_green,
        "zone_orange_min": zone_orange,
        "zone_red_min": zone_red,
        "source": source,
    }


def load_existing_ids(csv_path):
    ids = set()
    if not os.path.exists(csv_path):
        return ids
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ids.add(r.get("id"))
    return ids


def append_rows(csv_path, rows):
    exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


def parse_eml_dir(eml_dir):
    rows = []
    for root, _, files in os.walk(eml_dir):
        for name in files:
            if not name.lower().endswith(".eml"):
                continue
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                msg = BytesParser(policy=policy.default).parse(f)
            subj = msg.get("subject", "")
            body = parse_email_body(msg)
            rows.append(parse_metrics(subj, body, source=path, date_hdr=msg.get("date", "")))
    return rows


def fetch_imap_rows(user, password, folder="INBOX", since_days=60):
    rows = []
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(user, password)
    conn.select(folder)

    dt = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    status, data = conn.search(None, f'(SINCE "{dt}" FROM "orangetheory")')
    if status != "OK":
        conn.logout()
        return rows

    for num in data[0].split():
        status, msg_data = conn.fetch(num, "(RFC822)")
        if status != "OK" or not msg_data:
            continue
        raw = msg_data[0][1]
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        subj = msg.get("subject", "")
        body = parse_email_body(msg)
        rows.append(parse_metrics(subj, body, source=f"imap:{folder}:{num.decode()}", date_hdr=msg.get("date", "")))

    conn.logout()
    return rows


def to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def summarize(csv_path, report_path):
    if not os.path.exists(csv_path):
        return
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return

    total = len(rows)
    dist_vals = [to_float(r["distance_miles"]) for r in rows]
    hr_vals = [to_float(r["avg_hr"]) for r in rows]
    dist_vals = [x for x in dist_vals if x is not None]
    hr_vals = [x for x in hr_vals if x is not None]

    by_class = defaultdict(list)
    for r in rows:
        by_class[r.get("class_type", "Unknown")].append(r)

    lines = [
        "# OrangeTheory Weekly/All-time Snapshot",
        "",
        f"- Classes parsed: **{total}**",
        f"- Avg distance: **{(sum(dist_vals)/len(dist_vals)):.2f} mi**" if dist_vals else "- Avg distance: n/a",
        f"- Avg heart rate: **{(sum(hr_vals)/len(hr_vals)):.0f} bpm**" if hr_vals else "- Avg heart rate: n/a",
        "",
        "## By class type",
    ]

    for klass, group in sorted(by_class.items(), key=lambda kv: len(kv[1]), reverse=True):
        d = [to_float(x["distance_miles"]) for x in group]
        h = [to_float(x["avg_hr"]) for x in group]
        d = [x for x in d if x is not None]
        h = [x for x in h if x is not None]
        eff = "n/a"
        if d and h:
            eff = f"{(sum(d)/len(d)) / (sum(h)/len(h)):.5f} mi/bpm"
        lines.append(
            f"- **{klass}** ({len(group)} classes): avg distance {((sum(d)/len(d)) if d else 0):.2f} mi, avg HR {((sum(h)/len(h)) if h else 0):.0f} bpm, efficiency {eff}"
        )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    p = argparse.ArgumentParser(description="Parse OrangeTheory emails into CSV and summary report.")
    p.add_argument("--eml-dir", help="Directory containing .eml files")
    p.add_argument("--imap", action="store_true", help="Fetch from Outlook IMAP")
    p.add_argument("--imap-user", default=os.getenv("OTF_IMAP_USER"))
    p.add_argument("--imap-password", default=os.getenv("OTF_IMAP_PASSWORD"))
    p.add_argument("--imap-folder", default=os.getenv("OTF_IMAP_FOLDER", "INBOX"))
    p.add_argument("--since-days", type=int, default=60)
    p.add_argument("--csv", default="data/otf_classes.csv")
    p.add_argument("--report", default="data/otf_report.md")
    args = p.parse_args()

    rows = []
    if args.eml_dir:
        rows.extend(parse_eml_dir(args.eml_dir))

    if args.imap:
        if not args.imap_user or not args.imap_password:
            raise SystemExit("For --imap, set --imap-user/--imap-password or OTF_IMAP_USER/OTF_IMAP_PASSWORD")
        rows.extend(fetch_imap_rows(args.imap_user, args.imap_password, args.imap_folder, args.since_days))

    if not rows:
        raise SystemExit("No emails parsed. Use --eml-dir and/or --imap.")

    existing = load_existing_ids(args.csv)
    new_rows = [r for r in rows if r["id"] not in existing]
    if new_rows:
        append_rows(args.csv, new_rows)

    summarize(args.csv, args.report)
    print(f"Parsed: {len(rows)} | New rows: {len(new_rows)} | CSV: {args.csv} | Report: {args.report}")


if __name__ == "__main__":
    main()
