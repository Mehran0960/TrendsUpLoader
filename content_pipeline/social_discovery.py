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
    if h in {"t.me", "telegram.me"}: return "telegram"
    return "web"


def is_video_post(url):
    """Reject profile/search pages: downstream must receive an actual post/video URL."""
    from urllib.parse import urlparse
    p = platform(url)
    parsed = urlparse(str(url or ""))
    path = parsed.path or "/"
    if p == "instagram":
        return bool(re.match(r"^/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+/?$", path))
    if p == "tiktok":
        return bool(re.search(r"/video/\d+", path))
    if p == "youtube":
        return bool(re.match(r"^/shorts/[A-Za-z0-9_-]+", path) or
                    (re.match(r"^/watch$", path) and bool(parsed.query)) or
                    "youtu.be" in parsed.netloc)
    if p == "x":
        return bool(re.search(r"/status/\d+", path))
    if p == "aparat":
        return bool(re.match(r"^/v/[A-Za-z0-9]+", path))
    if p == "telegram":
        return bool(re.match(r"^/[A-Za-z0-9_]+/\d+/?$", path))
    return False


def decode_bing_url(value):
    """Decode Bing's signed redirect URL into the real destination."""
    from base64 import urlsafe_b64decode
    s = str(value or "")
    m = re.search(r"(?:^|[?&])u=(a1[^&]+)", s)
    if not m:
        return ""
    token = m.group(1)
    if token.startswith("a1"):
        token = token[2:]
    token += "=" * (-len(token) % 4)
    try:
        return urlsafe_b64decode(token.encode()).decode("utf-8", "ignore")
    except Exception:
        return ""

def bing_search(query, limit=12, timeout=14):
    """Zero-key fallback discovery using Bing's public HTML results."""
    from html import unescape
    from urllib.parse import quote_plus
    headers = {"User-Agent": UA, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7"}
    url = "https://www.bing.com/search?count=%d&q=%s" % (limit, quote_plus(query))
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    text = r.text
    out = []
    seen = set()
    blocks = re.findall(r'<li[^>]+class=["\\\'][^"\\\']*b_algo[^"\\\']*["\\\'].*?</li>', text, re.I | re.S)
    for block in blocks:
        hm = re.search(r'<a[^>]+href=["\\\']([^"\\\']+)["\\\'][^>]*>(.*?)</a>', block, re.I | re.S)
        if not hm:
            continue
        dest = decode_bing_url(unescape(hm.group(1)))
        if not dest:
            dest = unescape(hm.group(1))
        dest = dest.strip()
        if not dest or dest in seen:
            continue
        seen.add(dest)
        title = re.sub(r"<[^>]+>", " ", unescape(hm.group(2)))
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.I | re.S)
        desc = ""
        if pm:
            desc = re.sub(r"<[^>]+>", " ", unescape(pm.group(1)))
            desc = re.sub(r"\s+", " ", desc).strip()
        out.append({"url": dest, "title": title, "description": desc, "raw": {"source":"bing_search"}})
        if len(out) >= limit:
            break
    return out

def telegram_repost_candidates():
    """Read public Persian Telegram channels for direct Instagram video links."""
    from html import unescape
    # Public Persian short-video and comedy channels. They only provide leads;
    # native engagement and the media-quality gates still decide what can publish.
    channels = [
        "gizmiztel",
        "regaplus",
        "insta_clip85",
        "comedi",
        "khandehabadd",
        "vaybabamumad",
        "khandbazar20",
        "bikhiyalbaba",
        "kafeh_khande",
        "nicebest",
        "teacheryar",
        "VahidOnline",
        "tanzolemareh_t",
        "aranbidgoliha",
        "bandaranzali_aliabad",
    ]
    link_re = re.compile(
        r"(?:https?://)?(?:(?:www\.)|dd)?instagram\.com/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+",
        re.I,
    )
    out = []
    seen = set()
    by_url = {}
    for channel in channels:
        try:
            r = requests.get(
                "https://t.me/s/" + channel,
                headers={"User-Agent": UA, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6"},
                timeout=12,
            )
            if not r.ok:
                print("TELEGRAM_REPOST_FAIL", channel, r.status_code)
                continue
            page = r.text[:8_000_000]
            blocks = re.split(r'(?=<div class="tgme_widget_message_wrap\b)', page)
            channel_count = 0
            for block in blocks[1:]:
                message_id = re.search(r'data-post="([^"]+)"', block[:16000])
                if not message_id:
                    continue
                view_match = re.search(
                    r'class="tgme_widget_message_views"[^>]*>(.*?)</span>',
                    block[:30000], re.I | re.S,
                )
                relay_views = parse_num(re.sub(r"<[^>]+>", " ", unescape(view_match.group(1)))) if view_match else 0
                time_match = re.search(r'<time[^>]+datetime="([^"]+)"', block[:30000], re.I)
                published_at = time_match.group(1) if time_match else ""
                age_hours = 999999.0
                if published_at:
                    try:
                        age_hours = max(0.0, (datetime.now(timezone.utc) -
                            datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds()/3600.0)
                    except Exception:
                        pass
                caption_match = re.search(
                    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                    block[:60000], re.I | re.S,
                )
                caption = ""
                if caption_match:
                    caption = unescape(re.sub(r"<br\s*/?>", " ", caption_match.group(1), flags=re.I))
                    caption = re.sub(r"<[^>]+>", " ", caption)
                    caption = re.sub(r"\s+", " ", caption).strip()
                if len(re.findall(r"[\u0600-\u06ff]", caption)) < 3:
                    continue
                raw_social_urls = link_re.findall(block[:60000])
                has_video_marker = bool(re.search(
                    r"tgme_widget_message_video_(?:player|thumb|wrap)|<video\b",
                    block[:60000], re.I,
                ))
                if has_video_marker and not raw_social_urls:
                    # This is a directly hosted Telegram video post, not an
                    # Instagram relay. The hunter must still extract and gate media.
                    post_id = message_id.group(1).split("/")[-1]
                    post_url = f"https://t.me/{channel}/{post_id}"
                    if post_url not in seen:
                        seen.add(post_url)
                        item = {
                            "url": post_url,
                            "title": caption[:260],
                            "description": caption[:1800],
                            "raw": {
                                "source": "telegram_native_video",
                                "telegram_channel": channel,
                                "telegram_channels": [channel],
                                "telegram_post": message_id.group(1),
                                "telegram_post_views": relay_views,
                                "telegram_published_at": published_at,
                                "age_hours": age_hours,
                            },
                        }
                        out.append(item)
                        by_url[post_url] = item
                        channel_count += 1
                for raw_url in raw_social_urls:
                    raw_url = unescape(raw_url).replace("&amp;", "&")
                    m = re.search(r"/(reel|reels|p|tv)/([A-Za-z0-9_-]+)", raw_url, re.I)
                    if not m:
                        continue
                    kind, shortcode = m.group(1).lower(), m.group(2)
                    kind = "reel" if kind == "reels" else kind
                    url = f"https://www.instagram.com/{kind}/{shortcode}/"
                    if url in seen:
                        existing = by_url.get(url)
                        if existing:
                            raw = existing.get("raw") or {}
                            relay_channels = list(raw.get("telegram_channels") or [])
                            if channel not in relay_channels:
                                relay_channels.append(channel)
                                raw["telegram_repost_views"] = int(raw.get("telegram_repost_views") or 0) + relay_views
                                raw["telegram_channels"] = relay_channels
                                old_age = float(raw.get("age_hours") or 999999.0)
                                raw["age_hours"] = min(old_age, age_hours)
                                if not raw.get("telegram_published_at") or (published_at and published_at > raw["telegram_published_at"]):
                                    raw["telegram_published_at"] = published_at
                            existing["raw"] = raw
                        continue
                    seen.add(url)
                    item = {
                        "url": url,
                        "title": caption[:260],
                        "description": caption[:1800],
                        "raw": {
                            "source": "telegram_public_repost",
                            "telegram_channel": channel,
                            "telegram_channels": [channel],
                            "telegram_post": message_id.group(1),
                            "telegram_repost_views": relay_views,
                            "telegram_published_at": published_at,
                            "age_hours": age_hours,
                        },
                    }
                    out.append(item)
                    by_url[url] = item
                    channel_count += 1
            print("TELEGRAM_REPOST_CHANNEL", json.dumps({
                "channel": channel, "candidates": channel_count,
            }, ensure_ascii=False))
        except Exception as exc:
            print("TELEGRAM_REPOST_FAIL", channel, type(exc).__name__)
    return out


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
    now = datetime.now(timezone.utc)
    if not KEY:
        # Free fallback: Bing can expose public social URLs without an API key.
        # This is discovery-only; media acquisition still happens downstream.
        qidx = int(now.timestamp() // (30 * 60)) % len(QUERIES)
        query_variants = [
            'site:instagram.com/reel/ (خنده OR خنده‌دار OR طنز OR سوتی OR بامزه OR غافلگیرکننده)',
            'site:instagram.com/p/ (طنز OR خنده دار OR سوتی OR دابسمش OR ایرانی)',
            QUERIES[qidx],
            'site:youtube.com/shorts (فارسی OR ایرانی OR ایران) (خنده OR طنز OR بامزه OR پربازدید)',
            'site:aparat.com/v (ایران OR ایرانی) (بازدید OR پربازدید OR وایرال)',
        ]
        found = telegram_repost_candidates()
        for q in query_variants[:5]:
            try:
                batch = bing_search(q, limit=12)
                found.extend(batch)
                print("BING_QUERY_RESULTS", json.dumps({
                    "query": q,
                    "count": len(batch),
                    "sample": [{"url": x["url"], "title": x["title"][:100]} for x in batch[:5]],
                }, ensure_ascii=False))
            except Exception as exc:
                print("BING_SEARCH_FAIL", type(exc).__name__, q[:100])
        results = []
        seen_urls = set()
        for x in found:
            p = platform(x["url"])
            if p not in {"tiktok","instagram","youtube","x","aparat","telegram"}:
                continue
            if not is_video_post(x["url"]):
                continue
            if x["url"] in seen_urls:
                continue
            seen_urls.add(x["url"])
            source_tag = str((x.get("raw") or {}).get("source") or "")
            if source_tag == "telegram_public_repost":
                # Telegram relay views are not native Instagram counters.
                views, likes, comments, shares = 0, 0, 0, 0
            elif source_tag == "telegram_native_video":
                # View count belongs to the Telegram video post itself.
                views = int((x.get("raw") or {}).get("telegram_post_views") or 0)
                likes, comments, shares = 0, 0, 0
            else:
                views, likes, comments, shares = extract_metrics(x)
            text_blob = x["title"] + " " + x["description"]
            persian = len(re.findall(r"[؀-ۿ]", text_blob))
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
                "telegram_repost_views": int((x.get("raw") or {}).get("telegram_repost_views") or 0),
                "telegram_post_views": int((x.get("raw") or {}).get("telegram_post_views") or 0),
                "telegram_repost_channels": (
                    list((x.get("raw") or {}).get("telegram_channels") or [])
                    if source_tag == "telegram_public_repost" else []
                ),
                "telegram_channel": str((x.get("raw") or {}).get("telegram_channel") or ""),
                "telegram_post_views": int((x.get("raw") or {}).get("telegram_post_views") or 0),
                "published_at": str((x.get("raw") or {}).get("telegram_published_at") or ""),
                "age_hours": float((x.get("raw") or {}).get("age_hours") or 999999.0),
                "persian_signal": persian >= 3,
                "discovered_at": now.isoformat(),
                "source": str((x.get("raw") or {}).get("source") or "bing_search"),
                "query": qidx,
            })
        previous = []
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            pass
        # Do not carry legacy profile URLs forward as if they were video candidates.
        merged = {
            str(x.get("id")): x for x in previous
            if is_video_post(x.get("url"))
        }
        for x in results:
            merged[x["id"]] = x
        items = list(merged.values())[:500]
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "version": 2,
            "updated_at": now.isoformat(),
            "status": "public_telegram_repost_and_bing_fallback",
            "count": len(items),
            "items": items,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("SOCIAL_DISCOVERY_ITEMS", json.dumps({"new_results": len(results), "total_video_urls": len(items), "telegram_repost_candidates": sum(1 for x in results if x.get("source") == "telegram_public_repost"), "telegram_native_video_candidates": sum(1 for x in results if x.get("source") == "telegram_native_video")}, ensure_ascii=False))
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
