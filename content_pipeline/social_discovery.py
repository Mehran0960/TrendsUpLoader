#!/usr/bin/env python3
"""Multi-platform social discovery lane.

Uses Firecrawl Search when FIRECRAWL_API_KEY is present, otherwise exits cleanly.
This is discovery intelligence only: it does not publish or download media.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "attention_state" / "social_discovery.json"
KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

QUERIES = [
    'site:tiktok.com/@ ("فارسی" OR "ایران" OR "ایرانی") ("views" OR "بازدید")',
    'site:instagram.com/reel ("فارسی" OR "ایران" OR "ایرانی") ("views" OR "بازدید")',
    'site:x.com ("ویدئو" OR "ویدیو") ("ایران" OR "ایرانی" OR "فارسی")',
    'site:youtube.com/shorts ("فارسی" OR "ایران" OR "ایرانی")',
    'site:aparat.com/v ("بازدید" OR "پربازدید" OR "وایرال")',
]

def normalize_digits(s):
    return str(s or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def parse_num(raw):
    s = normalize_digits(raw).replace("٫", ".").replace("٬", ",").replace("،", ",").strip()
    m = re.search(r"([0-9][0-9,\.\s]*)\s*(تریلیون|میلیارد|بیلیون|میلیون|هزار|[KkMmBb])?", s, re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", "").replace(" ", ""))
    except Exception:
        return 0
    unit = (m.group(2) or "").replace(" ", "").lower()
    return int(n * {
        "k":1e3, "m":1e6, "b":1e9,
        "هزار":1e3, "میلیون":1e6, "میلیارد":1e9,
        "بیلیون":1e9, "تریلیون":1e12,
    }.get(unit, 1.0))

def metric(text, names):
    for name in names:
        p = rf"(?:{name})[^0-9۰-۹]{{0,30}}([0-9۰-۹][0-9۰-۹,\.\s]*\s*(?:[KkMmBb]|هزار|میلیون|میلیارد|بیلیون|تریلیون)?)"
        m = re.search(p, text or "", re.I)
        if m:
            v = parse_num(m.group(1))
            if v:
                return v
    return 0

def platform(url):
    h = re.sub(r"^www\.", "", re.sub(r"^https?://", "", str(url or "")).split("/")[0].lower())
    if "tiktok.com" in h: return "tiktok"
    if "instagram.com" in h: return "instagram"
    if "youtube.com" in h or "youtu.be" in h: return "youtube"
    if "x.com" in h or "twitter.com" in h: return "x"
    if "aparat.com" in h: return "aparat"
    return "web"

def flatten_results(obj):
    found = []
    def walk(x):
        if isinstance(x, dict):
            url = x.get("url") or x.get("link") or x.get("sourceURL")
            title = x.get("title") or x.get("name")
            if url and title:
                found.append({
                    "url": str(url),
                    "title": str(title),
                    "description": str(x.get("description") or x.get("snippet") or x.get("content") or x.get("markdown") or ""),
                    "raw": x,
                })
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    seen = set()
    out = []
    for x in found:
        k = x["url"].split("#")[0]
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out

def firecrawl_search(query, limit=10):
    headers = {"Content-Type":"application/json", "User-Agent":UA}
    if KEY:
        headers["Authorization"] = f"Bearer {KEY}"
    payload = {
        "query": query,
        "limit": limit,
        "sources": [{"type":"web"}],
    }
    r = requests.post("https://api.firecrawl.dev/v2/search", headers=headers, json=payload, timeout=35)
    r.raise_for_status()
    return r.json()

def extract_metrics(x):
    blob = " ".join([x["title"], x["description"], json.dumps(x["raw"], ensure_ascii=False)])
    views = metric(blob, ["views", "viewCount", "view_count", "بازدید", "playCount", "play_count"])
    likes = metric(blob, ["likes", "likeCount", "like_count", "پسند", "لایک"])
    comments = metric(blob, ["comments", "commentCount", "comment_count", "نظر", "کامنت"])
    shares = metric(blob, ["shares", "shareCount", "share_count", "اشتراک", "share"])
    return views, likes, comments, shares

def main():
    if not KEY:
        print("FIRECRAWL_API_KEY_MISSING")
        return 0

    now = datetime.now(timezone.utc)
    # Rotate one query per run. With a 4-hour schedule this stays inside the
    # current Firecrawl free allowance while covering all major platforms.
    idx = int(now.timestamp() // (4 * 3600)) % len(QUERIES)
    q = QUERIES[idx]
    try:
        data = firecrawl_search(q, limit=10)
    except Exception as exc:
        print("FIRECRAWL_SEARCH_FAIL", type(exc).__name__)
        return 0

    results = []
    for x in flatten_results(data):
        p = platform(x["url"])
        if p not in {"tiktok","instagram","youtube","x","aparat"}:
            continue
        views, likes, comments, shares = extract_metrics(x)
        text_blob = x["title"] + " " + x["description"]
        persian = len(re.findall(r"[\u0600-\u06ff]", text_blob))
        results.append({
            "id": hashlib.sha256(x["url"].encode()).hexdigest(),
            "url": x["url"],
            "title": x["title"],
            "description": x["description"][:1500],
            "platform": p,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "persian_signal": persian >= 3,
            "discovered_at": now.isoformat(),
            "source": "firecrawl_search",
            "query": q,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    previous = []
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        pass
    merged = {str(x.get("id")): x for x in previous}
    for x in results:
        merged[x["id"]] = x
    items = list(merged.values())
    items.sort(key=lambda x: (
        -int(x.get("shares") or 0),
        -int(x.get("likes") or 0),
        -int(x.get("views") or 0),
        x.get("discovered_at",""),
    ))
    items = items[:500]

    OUT.write_text(json.dumps({
        "version": 1,
        "updated_at": now.isoformat(),
        "query": q,
        "count": len(items),
        "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SOCIAL_DISCOVERY_ITEMS", len(results))
    print("SOCIAL_DISCOVERY_STATE", str(OUT))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
