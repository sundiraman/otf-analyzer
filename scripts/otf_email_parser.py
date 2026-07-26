#!/usr/bin/env python3
import argparse
import csv
import email
import hashlib
import html
import imaplib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

try:
    import requests
except Exception:  # optional until --graph is used
    requests = None

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993
GRAPH_AUTHORITY = "https://login.microsoftonline.com/common"
GRAPH_SCOPE = ["Mail.Read", "Mail.Send", "Calendars.ReadWrite", "User.Read", "offline_access"]
GRAPH_API_ROOT = "https://graph.microsoft.com/v1.0"


def load_dotenv(path: str = ".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except Exception:
        pass

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


def strip_html(s: str) -> str:
    s = s or ""
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return clean_text(s)


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
    raw_text = body or ""
    text = strip_html(raw_text) if "<" in raw_text and ">" in raw_text else clean_text(raw_text)

    distance = first_match([
        r"distance\s*(?:run|ran|total)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mi|miles)",
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:mi|miles)\s*(?:distance|run|ran|total)",
    ], text)

    avg_hr = first_match([
        r"avg(?:\.|erage)?\s*[- ]?heart[- ]?rate\s*[:\-]?\s*([0-9]{2,3})",
        r"avg(?:\.|erage)?\s*hr\s*[:\-]?\s*([0-9]{2,3})",
        r"([0-9]{2,3})\s+AVG\.?\s*HEART[- ]?RATE",
    ], text, int)

    max_hr = first_match([
        r"max(?:imum)?\s*hr\s*[:\-]?\s*([0-9]{2,3})",
        r"maximum\s*heart\s*rate\s*[:\-]?\s*([0-9]{2,3})",
        r"peak\s*hr[^0-9]{0,30}([0-9]{2,3})",
    ], text, int)

    this_class = re.search(r"THIS\s+CLASS\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)", text, re.IGNORECASE)

    splat = ""
    calories = ""
    if this_class:
        calories = int(this_class.group(1).replace(",", ""))
        splat = int(this_class.group(2).replace(",", ""))

    if splat == "":
        splat = first_match([
            r"([0-9]{1,3})\s+SPLAT\s*POINTS",
            r"splat\s*points?\s*[:\-]\s*([0-9]{1,3})",
        ], text, int)

    if calories == "":
        calories = first_match([
            r"([0-9]{2,4})\s+CALORIES\s*BURNED",
            r"calories\s*(?:burned)?\s*[:\-]\s*([0-9]{2,4})",
        ], text, int)

    zone_gray = first_match([r"gray\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_blue = first_match([r"blue\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_green = first_match([r"green\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_orange = first_match([r"orange\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)
    zone_red = first_match([r"red\s*[:\-]?\s*([0-9]{1,3})\s*(?:min|minutes?)"], text, int)

    # OTF v3 HTML: zone minutes appear as five bar labels in order Gray, Blue, Green, Orange, Red.
    if "" in [zone_gray, zone_blue, zone_green, zone_orange, zone_red]:
        zone_vals = re.findall(r"bar-bumber[^>]*>\s*([0-9]{1,3})\s*<", raw_text, re.IGNORECASE)
        if len(zone_vals) >= 5:
            zone_gray, zone_blue, zone_green, zone_orange, zone_red = [int(v) for v in zone_vals[:5]]

    dt = ""
    if date_hdr:
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr).isoformat()
        except Exception:
            try:
                dt = datetime.fromisoformat(str(date_hdr).replace("Z", "+00:00")).isoformat()
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
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
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


def fetch_imap_rows(
    user,
    password,
    folder="INBOX",
    since_days=60,
    host=IMAP_HOST,
    port=IMAP_PORT,
):
    rows = []
    conn = imaplib.IMAP4_SSL(host, port)
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


def graph_get_token(client_id: str, tenant: str = "common", cache_path: str = "data/graph_token.json"):
    if requests is None:
        raise SystemExit("Missing dependency: requests. Install with `python3 -m pip install requests`.")

    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    now = datetime.now(timezone.utc).timestamp()
    if cache.get("access_token") and cache.get("expires_at", 0) > now + 60:
        return cache["access_token"]

    authority = f"https://login.microsoftonline.com/{tenant}"
    token_url = f"{authority}/oauth2/v2.0/token"
    scope = " ".join(GRAPH_SCOPE)

    if cache.get("refresh_token"):
        r = requests.post(token_url, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": cache["refresh_token"],
            "scope": scope,
        }, timeout=30)
        if r.ok:
            tok = r.json()
            cache = {
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", cache.get("refresh_token")),
                "expires_at": now + int(tok.get("expires_in", 3600)),
            }
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            return cache["access_token"]

    device_code_url = f"{authority}/oauth2/v2.0/devicecode"
    dc = requests.post(device_code_url, data={"client_id": client_id, "scope": scope}, timeout=30)
    if not dc.ok:
        raise SystemExit(f"Device code init failed: {dc.status_code} {dc.text}")

    payload = dc.json()
    print(payload.get("message", "Open the verification URL and enter the code to sign in."))
    interval = int(payload.get("interval", 5))

    import time
    deadline = time.time() + int(payload.get("expires_in", 900))
    while time.time() < deadline:
        tr = requests.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": payload["device_code"],
        }, timeout=30)
        if tr.ok:
            tok = tr.json()
            cache = {
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token"),
                "expires_at": datetime.now(timezone.utc).timestamp() + int(tok.get("expires_in", 3600)),
            }
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            return cache["access_token"]

        err = tr.json().get("error", "") if tr.headers.get("content-type", "").startswith("application/json") else ""
        if err in {"authorization_pending", "slow_down"}:
            time.sleep(interval + (2 if err == "slow_down" else 0))
            continue
        raise SystemExit(f"Token error: {tr.status_code} {tr.text}")

    raise SystemExit("Device code flow timed out. Please retry.")


def graph_list_folders(headers, include_hidden=False):
    def list_children(parent_id=None):
        base = f"{GRAPH_API_ROOT}/me/mailFolders"
        if parent_id:
            base = f"{GRAPH_API_ROOT}/me/mailFolders/{parent_id}/childFolders"
        hidden = "true" if include_hidden else "false"
        url = f"{base}?$top=200&$select=id,displayName&includeHiddenFolders={hidden}"
        out = []
        while url:
            r = requests.get(url, headers=headers, timeout=30)
            if not r.ok:
                raise SystemExit(f"Graph folder list error: {r.status_code} {r.text}")
            data = r.json()
            out.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return out

    folders = []
    queue = [(None, "")]
    while queue:
        parent_id, parent_path = queue.pop(0)
        for f in list_children(parent_id):
            name = (f.get("displayName") or "").strip()
            fid = f.get("id")
            path = f"{parent_path}/{name}" if parent_path else name
            folders.append({"id": fid, "name": name, "path": path})
            if fid:
                queue.append((fid, path))
    return folders


def graph_get_me(headers):
    r = requests.get(f"{GRAPH_API_ROOT}/me?$select=id,displayName,mail,userPrincipalName", headers=headers, timeout=30)
    if not r.ok:
        raise SystemExit(f"Graph /me error: {r.status_code} {r.text}")
    return r.json()


def graph_find_folder_id(headers, folder_name: str, include_hidden=False):
    target = (folder_name or "").strip().lower()
    if not target:
        return None

    folders = graph_list_folders(headers, include_hidden=include_hidden)
    # exact path first
    for f in folders:
        if (f["path"] or "").strip().lower() == target:
            return f["id"]
    # exact leaf name next
    for f in folders:
        if (f["name"] or "").strip().lower() == target:
            return f["id"]
    # contains path fallback
    for f in folders:
        if target in (f["path"] or "").strip().lower():
            return f["id"]

    return None


def fetch_graph_rows(
    client_id,
    tenant="common",
    since_days=60,
    sender_contains="orangetheory",
    token_cache="data/graph_token.json",
    folder_name="",
    list_folders=False,
    include_hidden_folders=False,
):
    if requests is None:
        raise SystemExit("Missing dependency: requests. Install with `python3 -m pip install requests`.")

    token = graph_get_token(client_id, tenant=tenant, cache_path=token_cache)
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)

    rows = []
    headers = {"Authorization": f"Bearer {token}"}

    endpoint = f"{GRAPH_API_ROOT}/me/messages"
    me = graph_get_me(headers)
    all_folders = graph_list_folders(headers, include_hidden=include_hidden_folders)
    if list_folders:
        who = me.get("mail") or me.get("userPrincipalName") or me.get("displayName") or me.get("id")
        print(f"Graph account: {who}")
        print("Graph folders:")
        for f in all_folders:
            print(f"- {f['path']}")
        return rows

    if folder_name:
        folder_id = graph_find_folder_id(headers, folder_name, include_hidden=include_hidden_folders)
        if not folder_id:
            preview = "\n".join([f"- {f['path']}" for f in all_folders[:50]])
            who = me.get("mail") or me.get("userPrincipalName") or me.get("displayName") or me.get("id")
            raise SystemExit(f"Graph folder not found: {folder_name}\nGraph account: {who}\nAvailable folders (first 50):\n{preview}")
        endpoint = f"{GRAPH_API_ROOT}/me/mailFolders/{folder_id}/messages"

    url = (
        f"{endpoint}"
        "?$select=id,subject,from,receivedDateTime,body"
        "&$orderby=receivedDateTime desc"
        "&$top=50"
    )

    while url:
        r = requests.get(url, headers=headers, timeout=30)
        if not r.ok:
            raise SystemExit(f"Graph messages error: {r.status_code} {r.text}")
        data = r.json()
        for m in data.get("value", []):
            from_addr = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
            subj = m.get("subject", "") or ""
            dt_raw = m.get("receivedDateTime", "")
            try:
                dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if dt < since_dt:
                return rows
            hay = f"{from_addr} {subj}".lower()
            if sender_contains.lower() not in hay:
                continue

            body_obj = m.get("body") or {}
            content = body_obj.get("content", "")
            # Keep raw HTML when provided; parse_metrics handles HTML-aware extraction
            # (zone bars + modern OTF templates rely on structure/classes).

            rows.append(parse_metrics(
                subj,
                content,
                source=f"graph:{m.get('id', '')}",
                date_hdr=dt_raw,
            ))

        url = data.get("@odata.nextLink")

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

    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def summarize_html(csv_path, html_path):
    if not os.path.exists(csv_path):
        return
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return

    def esc(v):
        return html.escape(str(v) if v is not None else "")

    def td_num(v, fmt):
        return f"<td>{format(v, fmt)}</td>" if v is not None else "<td>n/a</td>"

    total = len(rows)
    dist_vals = [x for x in (to_float(r["distance_miles"]) for r in rows) if x is not None]
    hr_vals = [x for x in (to_float(r["avg_hr"]) for r in rows) if x is not None]
    avg_dist = (sum(dist_vals) / len(dist_vals)) if dist_vals else None
    avg_hr = (sum(hr_vals) / len(hr_vals)) if hr_vals else None

    by_class = defaultdict(list)
    for r in rows:
        by_class[r.get("class_type") or "Unknown"].append(r)

    class_rows_html = []
    for klass, group in sorted(by_class.items(), key=lambda kv: len(kv[1]), reverse=True):
        d = [x for x in (to_float(g["distance_miles"]) for g in group) if x is not None]
        h = [x for x in (to_float(g["avg_hr"]) for g in group) if x is not None]
        g_avg_dist = (sum(d) / len(d)) if d else None
        g_avg_hr = (sum(h) / len(h)) if h else None
        eff = f"{(g_avg_dist / g_avg_hr):.5f} mi/bpm" if g_avg_dist and g_avg_hr else "n/a"
        class_rows_html.append(
            "<tr>"
            f"<td>{esc(klass)}</td>"
            f"<td>{len(group)}</td>"
            + td_num(g_avg_dist, ".2f")
            + td_num(g_avg_hr, ".0f")
            + f"<td>{esc(eff)}</td>"
            "</tr>"
        )

    def sort_key(r):
        return r.get("date") or ""

    sessions_sorted = sorted(rows, key=sort_key, reverse=True)
    session_rows_html = []
    for r in sessions_sorted:
        dist = to_float(r.get("distance_miles"))
        hr = to_float(r.get("avg_hr"))
        max_hr = to_float(r.get("max_hr"))
        splat = r.get("splat_points") or ""
        cal = r.get("calories") or ""
        session_rows_html.append(
            "<tr>"
            f"<td>{esc(r.get('date'))}</td>"
            f"<td>{esc(r.get('class_type') or 'Unknown')}</td>"
            + td_num(dist, ".2f")
            + td_num(hr, ".0f")
            + td_num(max_hr, ".0f")
            + f"<td>{esc(splat)}</td>"
            f"<td>{esc(cal)}</td>"
            "</tr>"
        )

    avg_dist_html = f"{avg_dist:.2f} mi" if avg_dist is not None else "n/a"
    avg_hr_html = f"{avg_hr:.0f} bpm" if avg_hr is not None else "n/a"

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OrangeTheory Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem; background: #f7f7f9; color: #222; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .subtitle {{ color: #666; margin-top: 0; }}
  .stats {{ display: flex; gap: 1rem; margin: 1.5rem 0; flex-wrap: wrap; }}
  .card {{ background: #fff; border-radius: 8px; padding: 1rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 140px; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; color: #d64000; }}
  .card .label {{ font-size: 0.85rem; color: #666; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 2rem; }}
  th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #333; color: #fff; position: sticky; top: 0; }}
  tr:hover {{ background: #fffaf5; }}
  h2 {{ margin-top: 2rem; }}
  .table-wrap {{ max-height: 600px; overflow-y: auto; }}
</style>
</head>
<body>
  <h1>OrangeTheory Report</h1>
  <p class="subtitle">Generated {esc(datetime.now().strftime("%Y-%m-%d %H:%M"))}</p>

  <div class="stats">
    <div class="card"><div class="value">{total}</div><div class="label">Classes parsed</div></div>
    <div class="card"><div class="value">{esc(avg_dist_html)}</div><div class="label">Avg distance</div></div>
    <div class="card"><div class="value">{esc(avg_hr_html)}</div><div class="label">Avg heart rate</div></div>
  </div>

  <h2>By class type</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Class type</th><th>Count</th><th>Avg distance (mi)</th><th>Avg HR (bpm)</th><th>Efficiency</th></tr></thead>
    <tbody>
      {''.join(class_rows_html)}
    </tbody>
  </table>
  </div>

  <h2>All sessions</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Date</th><th>Class type</th><th>Distance (mi)</th><th>Avg HR</th><th>Max HR</th><th>Splat pts</th><th>Calories</th></tr></thead>
    <tbody>
      {''.join(session_rows_html)}
    </tbody>
  </table>
  </div>
</body>
</html>
"""

    os.makedirs(os.path.dirname(html_path) or ".", exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_doc)


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="Parse OrangeTheory emails into CSV and summary report.")
    p.add_argument("--eml-dir", help="Directory containing .eml files")
    p.add_argument("--imap", action="store_true", help="Fetch from Outlook IMAP")
    p.add_argument("--imap-user", default=os.getenv("OTF_IMAP_USER"))
    p.add_argument("--imap-password", default=os.getenv("OTF_IMAP_PASSWORD"))
    p.add_argument("--imap-folder", default=os.getenv("OTF_IMAP_FOLDER", "INBOX"))
    p.add_argument("--imap-host", default=os.getenv("OTF_IMAP_HOST", IMAP_HOST))
    p.add_argument("--imap-port", type=int, default=int(os.getenv("OTF_IMAP_PORT", str(IMAP_PORT))))

    p.add_argument("--graph", action="store_true", help="Fetch using Microsoft Graph OAuth device-code flow")
    p.add_argument("--graph-client-id", default=os.getenv("OTF_GRAPH_CLIENT_ID"), help="Azure app client id")
    p.add_argument("--graph-tenant", default=os.getenv("OTF_GRAPH_TENANT", "common"), help="Tenant id or 'common'")
    p.add_argument("--graph-token-cache", default=os.getenv("OTF_GRAPH_TOKEN_CACHE", "data/graph_token.json"))
    p.add_argument("--graph-sender-contains", default=os.getenv("OTF_GRAPH_SENDER", "orangetheory"))
    p.add_argument("--graph-folder", default=os.getenv("OTF_GRAPH_FOLDER", ""), help="Mail folder name or path, e.g. 'OrangeTheory' or 'Inbox/OrangeTheory' (optional)")
    p.add_argument("--graph-list-folders", action="store_true", help="List Graph mail folders and exit")
    p.add_argument("--graph-include-hidden-folders", action="store_true", help="Include hidden folders when listing/finding")

    p.add_argument("--since-days", type=int, default=60)
    p.add_argument("--csv", default="data/otf_classes.csv")
    p.add_argument("--report", default="data/otf_report.md")
    p.add_argument("--html-report", default=os.getenv("OTF_HTML_REPORT", "data/otf_report.html"))
    args = p.parse_args()

    rows = []
    if args.eml_dir:
        rows.extend(parse_eml_dir(args.eml_dir))

    if args.imap:
        if not args.imap_user or not args.imap_password:
            raise SystemExit("For --imap, set --imap-user/--imap-password or OTF_IMAP_USER/OTF_IMAP_PASSWORD")
        rows.extend(
            fetch_imap_rows(
                args.imap_user,
                args.imap_password,
                args.imap_folder,
                args.since_days,
                args.imap_host,
                args.imap_port,
            )
        )

    if args.graph:
        if not args.graph_client_id:
            raise SystemExit("For --graph, set --graph-client-id or OTF_GRAPH_CLIENT_ID")
        rows.extend(
            fetch_graph_rows(
                client_id=args.graph_client_id,
                tenant=args.graph_tenant,
                since_days=args.since_days,
                sender_contains=args.graph_sender_contains,
                token_cache=args.graph_token_cache,
                folder_name=args.graph_folder,
                list_folders=args.graph_list_folders,
                include_hidden_folders=args.graph_include_hidden_folders,
            )
        )

    if args.graph and args.graph_list_folders:
        return

    if not rows:
        raise SystemExit("No emails parsed. Use --eml-dir and/or --imap and/or --graph.")

    existing = load_existing_ids(args.csv)
    new_rows = [r for r in rows if r["id"] not in existing]
    if new_rows:
        append_rows(args.csv, new_rows)

    summarize(args.csv, args.report)
    summarize_html(args.csv, args.html_report)
    print(f"Parsed: {len(rows)} | New rows: {len(new_rows)} | CSV: {args.csv} | Report: {args.report} | HTML: {args.html_report}")


if __name__ == "__main__":
    main()
