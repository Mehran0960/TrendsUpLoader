#!/usr/bin/env python3
"""Broad, rotating multi-platform video discovery.

Combines public Telegram feeds, multi-domain Firecrawl/Bing search and the official YouTube API.
This is discovery intelligence only: it does not publish or download media.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from content_pipeline.follower_growth import LANES, expected_platform_for_query, rank_candidates
except ModuleNotFoundError:
    from follower_growth import LANES, expected_platform_for_query, rank_candidates

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "attention_state" / "social_discovery.json"
KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()
RANKING_VERSION = "follower_growth_v3"
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
YOUTUBE_API_ENABLED = os.environ.get("YOUTUBE_API_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off",
}
PAID_SEARCH_ENABLED = os.environ.get("DISCOVERY_PAID_SEARCH_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off",
}
# These limits intentionally operate below provider maxima to keep a large reserve.
FIRECRAWL_MONTHLY_CREDIT_BUDGET = 800
FIRECRAWL_CREDITS_PER_SEARCH = 2
FIRECRAWL_SEARCHES_PER_RUN = 2
BING_FALLBACK_SEARCHES_PER_RUN = 3
YOUTUBE_SEARCHES_PER_RUN = 3
YOUTUBE_DAILY_SEARCH_BUDGET = 30
# One-time conservative estimate for quota already spent before the ledger existed.
# The saved ledger carries forward afterward; the estimate is only used if no ledger exists.
QUOTA_BOOTSTRAP_MONTH = "2026-10"
QUOTA_BOOTSTRAP_FIRECRAWL_CREDITS = 400
QUOTA_BOOTSTRAP_YOUTUBE_DAY = "2026-10-11"
# A quota-exhaustion error was observed earlier today, so reserve the entire internal budget until UTC midnight.
QUOTA_BOOTSTRAP_YOUTUBE_SEARCHES = 30
YOUTUBE_QUERIES = [
    # Balanced, rotating discovery sweep: each run consumes two diverse queries.
    # Search indexing varies by platform; every result remains a lead, not proof of quality.
    'site:instagram.com/reel/ (طنز OR سوتی OR حیوانات OR واکنش OR لحظه غیرمنتظره OR مهارت)',
    'site:instagram.com/reel/ (funny OR unexpected OR animal OR satisfying OR skill OR transformation) short video',
    'site:tiktok.com/@*/video/ (ایرانی OR فارسی OR طنز OR حیوانات OR ترفند OR مهارت OR لحظه عجیب)',
    'site:tiktok.com/@*/video/ (funny OR unexpected OR animals OR science OR satisfying OR skill) viral',
    'site:youtube.com/shorts/ (ایرانی OR فارسی OR طنز OR حیوانات OR آزمایش OR ترفند OR ورزش OR آشپزی)',
    'site:youtube.com/shorts/ (funny OR unexpected OR animals OR satisfying OR science OR trick shot OR wildlife)',
    'site:x.com/*/status/ (ویدیو OR ویدئو) (طنز OR حیوانات OR مهارت OR اتفاق عجیب OR علم OR ورزش)',
    'site:x.com/*/status/ (video OR clip) (funny OR animal OR unexpected OR skill OR science OR satisfying)',
    'site:aparat.com/v/ (طنز OR اتفاق عجیب OR ترفند کاربردی OR حیوانات OR مهارت OR ورزش OR علم)',
    'site:aparat.com/v/ (قبل و بعد OR آشپزی OR تمیزکاری OR کاردستی OR موسیقی OR حیات وحش OR معما)',
    'site:facebook.com/reel/ (funny OR animals OR amazing skill OR satisfying OR unexpected) video',
    'site:reddit.com/r/videos/comments/ (viral OR amazing OR funny OR unexpected OR skill OR science) video',
    'site:vimeo.com/ (short film OR amazing skill OR nature OR satisfying process) video',
    'site:dailymotion.com/video/ (funny OR animals OR amazing skill OR science OR satisfying)',
    'viral short videos trending in the last 24 hours funny unexpected animal skill science satisfying',
    'ویدئوهای وایرال و پربازدید ۲۴ ساعت اخیر طنز حیوانات مهارت اتفاق غیرمنتظره علم ترفند',
]


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

DISCOVERABLE_PLATFORMS = {
    "instagram", "tiktok", "youtube", "x", "aparat", "telegram",
    "facebook", "reddit", "vimeo", "dailymotion", "twitch", "rumble",
}


def normalize_digits(s):
    return str(s or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def safe_quota_count(value):
    """Parse persisted counter values defensively; corrupted state must not crash discovery."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def quota_ledger_for_run(previous_payload, now):
    """Load persisted request counts, resetting only the provider's actual time window."""
    prior = (previous_payload or {}).get("quota_ledger", {})
    if not isinstance(prior, dict):
        prior = {}
    month = now.strftime("%Y-%m")
    day = now.strftime("%Y-%m-%d")

    if prior.get("firecrawl_month") == month:
        firecrawl_used = safe_quota_count(prior.get("firecrawl_credits_used"))
    elif not prior.get("firecrawl_month") and month == QUOTA_BOOTSTRAP_MONTH:
        firecrawl_used = QUOTA_BOOTSTRAP_FIRECRAWL_CREDITS
    else:
        firecrawl_used = 0

    if prior.get("youtube_day") == day:
        youtube_used = safe_quota_count(prior.get("youtube_search_calls_used"))
    elif not prior.get("youtube_day") and day == QUOTA_BOOTSTRAP_YOUTUBE_DAY:
        youtube_used = QUOTA_BOOTSTRAP_YOUTUBE_SEARCHES
    else:
        youtube_used = 0

    return {
        "firecrawl_month": month,
        "firecrawl_credits_used": firecrawl_used,
        "firecrawl_monthly_credit_budget": FIRECRAWL_MONTHLY_CREDIT_BUDGET,
        "firecrawl_reserved_credits": 1000 - FIRECRAWL_MONTHLY_CREDIT_BUDGET,
        "youtube_day": day,
        "youtube_search_calls_used": youtube_used,
        "youtube_daily_search_budget": YOUTUBE_DAILY_SEARCH_BUDGET,
        "youtube_searches_per_run": YOUTUBE_SEARCHES_PER_RUN,
        "updated_at": now.isoformat(),
    }


def discover_youtube_with_budget(now, quota_ledger):
    """Spend only a reserved, persisted number of YouTube search.list calls."""
    if not YOUTUBE_KEY or not YOUTUBE_API_ENABLED:
        return discover_youtube_public_candidates(now)
    used = safe_quota_count(quota_ledger.get("youtube_search_calls_used"))
    remaining = max(0, YOUTUBE_DAILY_SEARCH_BUDGET - used)
    count = min(YOUTUBE_SEARCHES_PER_RUN, remaining)
    if count <= 0:
        print("YOUTUBE_PUBLIC_API_SKIPPED_DAILY_BUDGET", json.dumps({
            "used": used,
            "daily_budget": YOUTUBE_DAILY_SEARCH_BUDGET,
        }, ensure_ascii=False))
        return []
    # Charge attempts before the network call; failed responses may still consume quota.
    quota_ledger["youtube_search_calls_used"] = used + count
    quota_ledger["updated_at"] = now.isoformat()
    return discover_youtube_public_candidates(now, max_search_calls=count)


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

def discovery_timestamp(item):
    """Parse when a candidate was observed; unknown timestamps sort last."""
    raw = str((item or {}).get("discovered_at") or "").strip()
    if not raw:
        return 0.0
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except Exception:
        return 0.0


def sort_discovery_items(items, limit=500):
    """Keep the newest observed candidates so stale history cannot crowd out fresh leads."""
    def engagement(item):
        total = 0
        for key in ("views", "likes", "comments", "shares", "telegram_repost_views", "telegram_post_views"):
            try:
                total += max(0, int(item.get(key) or 0))
            except Exception:
                pass
        return total

    rows = [x for x in items if isinstance(x, dict) and str(x.get("url") or "").strip()]
    rows.sort(key=lambda x: (discovery_timestamp(x), engagement(x)), reverse=True)
    return rows[:max(0, int(limit))]


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
    if "facebook.com" in h or h == "fb.watch": return "facebook"
    if "reddit.com" in h or h == "redd.it": return "reddit"
    if "vimeo.com" in h: return "vimeo"
    if "dailymotion.com" in h or h == "dai.ly": return "dailymotion"
    if "twitch.tv" in h: return "twitch"
    if "rumble.com" in h: return "rumble"
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
    if p == "facebook":
        return bool(re.match(r"^/(?:reel|reels|watch|share/v)/", path) or
                    (path == "/watch" and bool(parsed.query)) or
                    bool(re.search(r"/videos?/\d+", path)))
    if p == "reddit":
        return bool(re.match(r"^/r/[^/]+/comments/[a-z0-9]+", path, re.I))
    if p == "vimeo":
        return bool(re.match(r"^/(?:video/)?\d+/?$", path))
    if p == "dailymotion":
        return bool(re.match(r"^/(?:video|embed/video)/[A-Za-z0-9]+", path))
    if p == "twitch":
        return bool(re.search(r"/(?:clip|videos?)/[A-Za-z0-9_-]+", path) or
                    parsed.netloc.lower() == "clips.twitch.tv")
    if p == "rumble":
        return bool(re.match(r"^/v[A-Za-z0-9]+", path))
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

def telegram_caption_has_discovery_signal(caption):
    """Keep Persian captions, or English captions that match a known target topic."""
    text = re.sub(r"\s+", " ", str(caption or "")).strip()
    if len(re.findall(r"[\u0600-\u06ff]", text)) >= 3:
        return True
    return any(pattern.search(text) for pattern in LANES.values())


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
        # Additional active public Persian humour/viral-video feeds. Captions and
        # videos still pass the same topic, freshness, safety and duplicate gates.
        "keereker",
        "ersalihatun",
        "clipsamii",
        "naroo_somaye",
        "winstnchannel",
        # Verified public Persian technology/AI feeds; candidates still pass
        # the same demand, visual-hook, safety and single-payoff gates.
        "thezoomit",
        "farda_ai",
        "AIpersianChannel",
        "simorghai",
        "funnyttt",
        "MyFavCats",
        "Pishiha",
        "ashpaziroghi",
        "Lezath_Ashpazi",
        "behtarinhahameshi",
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
                if not telegram_caption_has_discovery_signal(caption):
                    continue
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


def normalize_native_telegram_candidates(found, now):
    """Normalize directly hosted public Telegram videos into reviewable discovery rows."""
    normalized = []
    seen_urls = set()
    scanned = len(found) if isinstance(found, list) else 0
    for candidate in found if isinstance(found, list) else []:
        if not isinstance(candidate, dict):
            continue
        url = str(candidate.get("url") or "").strip()
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
        if str(raw.get("source") or "") != "telegram_native_video":
            continue
        if platform(url) != "telegram" or not is_video_post(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        title = re.sub(r"\s+", " ", str(candidate.get("title") or "").strip())
        description = re.sub(r"\s+", " ", str(candidate.get("description") or "").strip())
        published_at = str(raw.get("telegram_published_at") or "").strip()
        try:
            age_hours = max(0.0, float(raw.get("age_hours", 999999.0)))
        except (TypeError, ValueError):
            age_hours = 999999.0
        try:
            views = max(0, int(raw.get("telegram_post_views") or 0))
        except (TypeError, ValueError):
            views = 0
        text_blob = title + " " + description
        normalized.append({
            "id": hashlib.sha256(url.encode("utf-8")).hexdigest(),
            "url": url,
            "title": title[:260],
            "description": description[:1500],
            "platform": "telegram",
            "views": views,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "telegram_repost_views": 0,
            "telegram_post_views": views,
            "telegram_repost_channels": [],
            "telegram_channel": str(raw.get("telegram_channel") or ""),
            "published_at": published_at,
            "age_hours": age_hours,
            "persian_signal": len(re.findall(r"[\u0600-\u06ff]", text_blob)) >= 3,
            "discovered_at": now.isoformat(),
            "source": "telegram_native_video",
            "query": "telegram_public_channel",
        })
    print("TELEGRAM_NATIVE_DISCOVERY_SUMMARY", json.dumps({
        "channel_posts_scanned": scanned,
        "native_video_candidates": len(normalized),
        "sample": [{"title": x["title"][:90], "url": x["url"], "age_hours": x["age_hours"], "views": x["views"]}
                   for x in normalized[:5]],
    }, ensure_ascii=False))
    return normalized

def search_result_freshness(item, now):
    """Return source-backed publication age or a conservative 24h search-window bound."""
    raw = item.get("raw") if isinstance(item, dict) and isinstance(item.get("raw"), dict) else {}
    containers = [raw]
    for key in ("metadata", "meta", "videoDetails"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    date_keys = (
        "publishedAt", "published_at", "publishedDate", "published_date",
        "datePublished", "date_published", "uploadDate", "upload_date",
    )
    for container in containers:
        for key in date_keys:
            value = str(container.get(key) or "").strip()
            if not value:
                continue
            try:
                published = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                age = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600.0)
                return round(age, 2), published.isoformat(), "published_date"
            except (TypeError, ValueError):
                continue
    # This is an upper bound from the explicit search filter, not an asserted
    # exact post age. Downstream permits it only for private review.
    return 24.0, "", "firecrawl_tbs_qdr_d_upper_bound"


def firecrawl_search(query, limit=10):
    headers = {"Content-Type":"application/json", "User-Agent":UA}
    if KEY:
        headers["Authorization"] = f"Bearer {KEY}"
    payload = {
        "query": query,
        "limit": limit,
        "sources": [{"type":"web"}],
        # Firecrawl's qdr:d filter restricts keyless-search results to the past day.
        "tbs": "qdr:d",
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


def parse_youtube_duration(value):
    """Convert ISO 8601 YouTube duration to seconds; return None if invalid."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def youtube_queries_for_time(now, count=2):
    """Rotate a small batch of distinct queries while staying within the default daily search-call budget."""
    start = int(now.timestamp() // (30 * 60)) % len(YOUTUBE_QUERIES)
    amount = min(max(1, int(count)), len(YOUTUBE_QUERIES))
    return [YOUTUBE_QUERIES[(start + offset) % len(YOUTUBE_QUERIES)] for offset in range(amount)]


def youtube_query_for_time(now):
    """Compatibility helper used by tests and diagnostics."""
    return youtube_queries_for_time(now, count=1)[0]


def youtube_api_error_context(exc):
    """Expose actionable Google API failure details without logging the API key."""
    response = getattr(exc, "response", None)
    context = {"exception": type(exc).__name__}
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        context["http_status"] = status_code
    payload = {}
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    nested_errors = error.get("errors") or []
    reason = nested_errors[0].get("reason") if nested_errors and isinstance(nested_errors[0], dict) else ""
    reason = reason or error.get("status")
    message = str(error.get("message") or getattr(exc, "reason", "") or "").strip()
    if reason:
        context["api_reason"] = str(reason)[:100]
    if message:
        if YOUTUBE_KEY:
            message = message.replace(YOUTUBE_KEY, "[redacted]")
        context["api_message"] = message[:220]
    return context


def discover_youtube_public_candidates(now=None, limit=50, max_search_calls=None):
    """Use the official YouTube Data API for fresh short videos and native engagement counters."""
    if not YOUTUBE_KEY:
        print("YOUTUBE_PUBLIC_API_SKIPPED_NO_KEY")
        return []
    if not YOUTUBE_API_ENABLED:
        print("YOUTUBE_PUBLIC_API_SKIPPED_NON_SCHEDULED_RUN")
        return []
    now = now or datetime.now(timezone.utc)
    search_count = YOUTUBE_SEARCHES_PER_RUN if max_search_calls is None else max(0, int(max_search_calls))
    if search_count <= 0:
        print("YOUTUBE_PUBLIC_API_SKIPPED_DAILY_BUDGET")
        return []
    selected_queries = youtube_queries_for_time(now, count=search_count)
    published_after = (now - timedelta(hours=36)).isoformat().replace("+00:00", "Z")
    video_ids = []
    query_for_video = {}
    query_counts = []
    for query, language in selected_queries:
        try:
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": YOUTUBE_KEY,
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "videoDuration": "short",
                    "order": "viewCount",
                    "publishedAfter": published_after,
                    "regionCode": "IR",
                    "relevanceLanguage": language,
                    "maxResults": 18,
                },
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            details = youtube_api_error_context(exc)
            print("YOUTUBE_PUBLIC_SEARCH_FAIL", json.dumps({"query": query, **details}, ensure_ascii=False))
            query_counts.append({"query": query, "returned": 0, **details})
            continue
        found_ids = []
        for row in (data.get("items") or []):
            video_id = str((row.get("id") or {}).get("videoId") or "").strip()
            if not video_id:
                continue
            found_ids.append(video_id)
            if video_id not in query_for_video:
                query_for_video[video_id] = query
        video_ids.extend(found_ids)
        query_counts.append({"query": query, "returned": len(found_ids), "language": language})

    video_ids = list(dict.fromkeys(video_ids))[:50]
    if not video_ids:
        print("YOUTUBE_PUBLIC_API_EMPTY", json.dumps({"queries": query_counts}, ensure_ascii=False))
        return []

    try:
        details = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key": YOUTUBE_KEY,
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(video_ids),
                "maxResults": 50,
            },
            timeout=25,
        )
        details.raise_for_status()
        detail_data = details.json()
    except Exception as exc:
        details = youtube_api_error_context(exc)
        print("YOUTUBE_PUBLIC_DETAILS_FAIL", json.dumps({"query_count": len(selected_queries), **details}, ensure_ascii=False))
        return []

    results = []
    raw_video_count = len(detail_data.get("items") or [])
    rejected_duration = 0
    rejected_reach = 0
    rejected_age = 0
    for video in (detail_data.get("items") or []):
        video_id = str(video.get("id") or "").strip()
        if not video_id:
            continue
        snippet = video.get("snippet") or {}
        stats = video.get("statistics") or {}
        content = video.get("contentDetails") or {}
        duration = parse_youtube_duration(content.get("duration"))
        if duration is None or duration > 180:
            rejected_duration += 1
            continue
        published_at = str(snippet.get("publishedAt") or "")
        age_hours = 999999.0
        if published_at:
            try:
                published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                age_hours = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600.0)
            except (TypeError, ValueError):
                pass
        views = int(stats.get("viewCount") or 0)
        if age_hours > 36:
            rejected_age += 1
            continue
        if views < 2000:
            rejected_reach += 1
            continue
        url = f"https://www.youtube.com/watch?v={video_id}"
        title = str(snippet.get("title") or "").strip()
        description = str(snippet.get("description") or "").strip()
        text_blob = title + " " + description
        results.append({
            "id": hashlib.sha256(url.encode()).hexdigest(),
            "url": url,
            "title": title,
            "description": description[:1500],
            "platform": "youtube",
            "views": views,
            "likes": int(stats.get("likeCount") or 0),
            "comments": int(stats.get("commentCount") or 0),
            "shares": 0,
            "persian_signal": len(re.findall(r"[\u0600-\u06ff]", text_blob)) >= 3,
            "published_at": published_at,
            "age_hours": age_hours,
            "discovered_at": now.isoformat(),
            "source": "youtube_public_api",
            "metric_evidence": "native_youtube_api_statistics",
            "duration_seconds": duration,
            "youtube_query": query_for_video.get(video_id, ""),
        })
    print("YOUTUBE_PUBLIC_API_CANDIDATES", json.dumps({
        "queries": query_counts,
        "returned": len(results),
        "raw_details": raw_video_count,
        "rejected_duration": rejected_duration,
        "rejected_reach": rejected_reach,
        "rejected_age": rejected_age,
        "metrics": "native_youtube_api_statistics",
        "search_calls_this_run": len(selected_queries),
    }, ensure_ascii=False))
    return results

def log_growth_summary(items):
    """Expose ranking quality and review queues in CI logs for iteration."""
    rows = items if isinstance(items, list) else []
    actions = {}
    for item in rows:
        action = str(item.get("candidate_action") or "unclassified")
        actions[action] = actions.get(action, 0) + 1
    print("FOLLOWER_GROWTH_TRIAGE", json.dumps({
        "scored_candidates": len(rows),
        "actions": actions,
        "top": [{
            "platform": item.get("platform"),
            "title": str(item.get("title") or "")[:90],
            "score": item.get("follow_growth_score"),
            "confidence": item.get("score_confidence"),
            "lane": item.get("content_lane"),
            "action": item.get("candidate_action"),
            "safety_review": item.get("safety_review_signal"),
        } for item in rows[:5]],
    }, ensure_ascii=False))


def main(force_fallback=False, _quota_ledger=None, _previous_payload=None):
    now = datetime.now(timezone.utc)
    previous_payload = _previous_payload
    if previous_payload is None:
        try:
            previous_payload = json.loads(OUT.read_text(encoding="utf-8"))
            if not isinstance(previous_payload, dict):
                previous_payload = {}
        except Exception:
            previous_payload = {}
    quota_ledger = _quota_ledger or quota_ledger_for_run(previous_payload, now)
    print("DISCOVERY_QUOTA_LEDGER", json.dumps({
        "firecrawl_month": quota_ledger.get("firecrawl_month"),
        "firecrawl_credits_used": quota_ledger.get("firecrawl_credits_used"),
        "firecrawl_credits_remaining_in_internal_budget": max(
            0, FIRECRAWL_MONTHLY_CREDIT_BUDGET - safe_quota_count(quota_ledger.get("firecrawl_credits_used"))
        ),
        "firecrawl_internal_monthly_budget": FIRECRAWL_MONTHLY_CREDIT_BUDGET,
        "youtube_day": quota_ledger.get("youtube_day"),
        "youtube_search_calls_used": quota_ledger.get("youtube_search_calls_used"),
        "youtube_search_calls_remaining_in_internal_budget": max(
            0, YOUTUBE_DAILY_SEARCH_BUDGET - safe_quota_count(quota_ledger.get("youtube_search_calls_used"))
        ),
        "youtube_internal_daily_budget": YOUTUBE_DAILY_SEARCH_BUDGET,
        "paid_public_search_enabled": PAID_SEARCH_ENABLED,
        "youtube_api_enabled": YOUTUBE_API_ENABLED,
    }, ensure_ascii=False))
    if force_fallback:
        # Use Bing only if Firecrawl's keyless/API request actually fails.
        # Firecrawl keyless currently provides a free monthly search allowance.
        # This is discovery-only; media acquisition still happens downstream.
        # Rotate by scheduled four-hour windows. A half-hour index combined
        # with a four-hour schedule would repeat the same query every run.
        qidx = (int(now.timestamp() // (4 * 3600)) * FIRECRAWL_SEARCHES_PER_RUN) % len(QUERIES)
        query_variants = [
            'site:instagram.com/reel/ (خنده OR طنز OR سوتی OR واکنش انسانی OR بامزه OR غافلگیر OR احساسی OR مهربانی)',
            'site:instagram.com/reel/ (آشپزی OR دستور پخت OR ترفند کاربردی OR تمیزکاری OR قبل و بعد OR دست ساز)',
            'site:instagram.com/reel/ (حیوانات OR گربه OR سگ OR حیات وحش OR طبیعت OR ورزش OR بسکتبال OR شوت تماشایی)',
            QUERIES[qidx],
            'site:youtube.com/shorts (فارسی OR ایرانی OR ایران) ("هوش مصنوعی" OR تکنولوژی OR "آزمایش علمی" OR علم OR DIY OR کاردستی)',
            'site:youtube.com/shorts (فارسی OR ایرانی OR ایران) (حیوانات OR طنز OR ورزش OR آشپزی OR طبیعت OR ترفند OR رقص OR نوازندگی)',
            'site:aparat.com/v (ایران OR ایرانی OR فارسی) (غذا OR آشپزی OR ترفند کاربردی OR معما OR آزمایش OR حیات وحش OR ورزش OR کاردستی OR قبل و بعد) (بازدید OR وایرال OR پربازدید)',
        ]
        found = telegram_repost_candidates()
        # Rotate a small free-search batch too, to reduce rate-limit risk when
        # Firecrawl is unavailable or its monthly budget is reached.
        fallback_start = qidx % len(query_variants)
        fallback_queries = [
            query_variants[(fallback_start + offset) % len(query_variants)]
            for offset in range(min(BING_FALLBACK_SEARCHES_PER_RUN, len(query_variants)))
        ]
        for q in fallback_queries:
            try:
                batch = bing_search(q, limit=12)
                for candidate in batch:
                    raw = candidate.setdefault("raw", {})
                    if isinstance(raw, dict):
                        raw["search_query"] = q
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
            if p not in DISCOVERABLE_PLATFORMS:
                continue
            if not is_video_post(x["url"]):
                continue
            source_tag = str((x.get("raw") or {}).get("source") or "")
            if source_tag in {"bing_search", "firecrawl_search"}:
                raw = x.get("raw") if isinstance(x.get("raw"), dict) else {}
                expected = expected_platform_for_query(raw.get("search_query") or x.get("discovery_query") or "")
                if expected and p != expected:
                    print("SOCIAL_DISCOVERY_DOMAIN_MISMATCH_REJECT", json.dumps({
                        "expected": expected, "actual": p, "url": x["url"],
                    }, ensure_ascii=False))
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
        results.extend(discover_youtube_with_budget(now, quota_ledger))
        previous = []
        try:
            previous = list(previous_payload.get("items") or [])
        except Exception:
            pass
        # Do not carry legacy profile URLs forward as if they were video candidates.
        merged = {
            str(x.get("id")): x for x in previous
            if is_video_post(x.get("url"))
        }
        for x in results:
            merged[x["id"]] = x
        # Refresh Telegram-post age for retained candidates instead of trusting stale
        # ages saved by an earlier run. This is the relay/native Telegram post age,
        # not a claim about the original Instagram Reel's publication date.
        for item in merged.values():
            source_tag = str(item.get("source") or "")
            published_at = str(item.get("published_at") or "").strip()
            if source_tag in {"telegram_native_video", "telegram_public_repost", "youtube_public_api"} and published_at:
                try:
                    published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    item["age_hours"] = max(0.0, (now - published).total_seconds() / 3600.0)
                except Exception:
                    pass
        items = rank_candidates(merged.values(), limit=500)
        log_growth_summary(items)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "version": 2,
            "ranking_version": RANKING_VERSION,
            "updated_at": now.isoformat(),
            "status": "public_telegram_repost_and_bing_fallback",
            "count": len(items),
            "quota_ledger": quota_ledger,
            "items": items,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("SOCIAL_DISCOVERY_ITEMS", json.dumps({"new_results": len(results), "total_video_urls": len(items), "telegram_repost_candidates": sum(1 for x in results if x.get("source") == "telegram_public_repost"), "telegram_native_video_candidates": sum(1 for x in results if x.get("source") == "telegram_native_video")}, ensure_ascii=False))
        return 0

    now = datetime.now(timezone.utc)
    # One keyless Firecrawl search per 4-hour run is about 360 credits/month
    # (6 runs/day × 30 days × 2 credits/search), leaving a large reserve.
    start_index = (int(now.timestamp() // (4 * 3600)) * FIRECRAWL_SEARCHES_PER_RUN) % len(QUERIES)
    selected_queries = []
    if not PAID_SEARCH_ENABLED:
        print("PAID_PUBLIC_SEARCH_SKIPPED_NON_SCHEDULED_RUN")
    else:
        used = safe_quota_count(quota_ledger.get("firecrawl_credits_used"))
        remaining = max(0, FIRECRAWL_MONTHLY_CREDIT_BUDGET - used)
        allowed = min(FIRECRAWL_SEARCHES_PER_RUN, remaining // FIRECRAWL_CREDITS_PER_SEARCH)
        if allowed > 0:
            selected_queries = [
                QUERIES[(start_index + offset) % len(QUERIES)]
                for offset in range(allowed)
            ]
        else:
            print("FIRECRAWL_SEARCH_SKIPPED_MONTHLY_BUDGET", json.dumps({
                "credits_used": used,
                "monthly_budget": FIRECRAWL_MONTHLY_CREDIT_BUDGET,
            }, ensure_ascii=False))
    results = []
    successful_searches = 0
    for q in selected_queries:
        # Charge attempts conservatively before the request, including failed calls.
        quota_ledger["firecrawl_credits_used"] = (
            safe_quota_count(quota_ledger.get("firecrawl_credits_used"))
            + FIRECRAWL_CREDITS_PER_SEARCH
        )
        quota_ledger["updated_at"] = now.isoformat()
        try:
            data = firecrawl_search(q, limit=10)
            successful_searches += 1
        except Exception as exc:
            print("FIRECRAWL_SEARCH_FAIL", type(exc).__name__, q[:100])
            continue

        raw_candidates = flatten_results(data)
        print("FIRECRAWL_QUERY_RESULTS", json.dumps({
            "query": q,
            "raw_count": len(raw_candidates),
            "sample": [{
                "url": str(x.get("url") or "")[:180],
                "title": str(x.get("title") or "")[:100],
            } for x in raw_candidates[:5]],
        }, ensure_ascii=False))
        expected_platform = expected_platform_for_query(q)
        for x in raw_candidates:
            x["discovery_query"] = q
            p = platform(x["url"])
            if p not in DISCOVERABLE_PLATFORMS - {"telegram"}:
                continue
            if not is_video_post(x["url"]):
                print("SOCIAL_DISCOVERY_NON_VIDEO_URL_REJECT", json.dumps({
                    "platform": p, "url": x["url"],
                }, ensure_ascii=False))
                continue
            if expected_platform and p != expected_platform:
                print("SOCIAL_DISCOVERY_DOMAIN_MISMATCH_REJECT", json.dumps({
                    "expected": expected_platform, "actual": p, "url": x["url"],
                }, ensure_ascii=False))
                continue
            views, likes, comments, shares = extract_metrics(x)
            age_hours, published_at, freshness_basis = search_result_freshness(x, now)
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
                "published_at": published_at,
                "age_hours": age_hours,
                "freshness_basis": freshness_basis,
                "discovered_at": now.isoformat(),
                "source": "firecrawl_search",
                "query": q,
            })

    if successful_searches == 0 and PAID_SEARCH_ENABLED:
        print("FIRECRAWL_FALLBACK_TO_PUBLIC_BING_SEARCH")
        return main(force_fallback=True, _quota_ledger=quota_ledger, _previous_payload=previous_payload)

    # Public Telegram video feeds are a first-class source. Previously they were
    # crawled only when Firecrawl failed entirely, so a successful-but-irrelevant
    # Firecrawl response silently disabled the most reliable downloadable source.
    telegram_found = telegram_repost_candidates()
    results.extend(normalize_native_telegram_candidates(telegram_found, now))
    results.extend(discover_youtube_with_budget(now, quota_ledger))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    previous = []
    try:
        previous = list(previous_payload.get("items") or [])
    except Exception:
        pass
    # Do not carry legacy profile URLs forward as if they were video candidates.
    merged = {
        str(x.get("id")): x for x in previous
        if is_video_post(x.get("url"))
    }
    for x in results:
        merged[x["id"]] = x
    # Refresh the age of retained public Telegram posts. Without this, an old
    # post's original age was frozen at discovery time and could appear fresh
    # indefinitely while crowding genuine new candidates out of the shortlist.
    for item in merged.values():
        source_tag = str(item.get("source") or "")
        published_at = str(item.get("published_at") or "").strip()
        if source_tag in {"telegram_native_video", "telegram_public_repost", "youtube_public_api"} and published_at:
            try:
                published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                item["age_hours"] = max(
                    0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600.0
                )
            except (TypeError, ValueError):
                pass
    items = rank_candidates(merged.values(), limit=500)
    log_growth_summary(items)

    OUT.write_text(json.dumps({
        "version": 2,
        "ranking_version": RANKING_VERSION,
        "updated_at": now.isoformat(),
        "queries": selected_queries,
        "count": len(items),
        "quota_ledger": quota_ledger,
        "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SOCIAL_DISCOVERY_ITEMS", json.dumps({
        "new_results": len(results),
        "new_firecrawl_results": sum(1 for x in results if x.get("source") == "firecrawl_search"),
        "new_native_telegram_videos": sum(1 for x in results if x.get("source") == "telegram_native_video"),
        "total_video_urls": len(items),
    }, ensure_ascii=False))
    print("SOCIAL_DISCOVERY_STATE", str(OUT))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
