# Viral demand engine
#!/usr/bin/env python3
"""Iran viral video hunter: discover fresh Persian web videos, rank, dedupe, send native MP4 to Telegram."""
import hashlib, html, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import requests
import xml.etree.ElementTree as ET

# Reuse the mature visual/hook scorer from the existing pipeline.
try:
    from content_pipeline.attention_engine import visual_score, source_hook_score
except Exception:
    visual_score = None
    source_hook_score = None

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "attention_state" / "iran_video_hunter_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
PIPED_FALLBACKS = ["https://pipedapi.kavin.rocks","https://pipedapi.leptons.xyz","https://pipedapi.nosebs.ru","https://pipedapi.adminforge.de","https://api.piped.yt"]
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
COBALT_API_URL = os.environ.get("COBALT_API_URL", "").strip().rstrip("/")
BGUTIL_POT_URL = os.environ.get("BGUTIL_POT_URL", "http://127.0.0.1:4416").strip().rstrip("/")
ATTRACTION_MODEL_VERSION = "visual_hook_v2"
ATTRACTION_FLOOR = 48.0
ATTRACTION_EMERGENCY_FLOOR = 40.0
DEMAND_EMERGENCY_THRESHOLD = 88.0
ACQUISITION_REVIEW_LIMIT = 8

SOURCE_DOMAINS = [
    "hamshahrionline.ir","khabaronline.ir","mehrnews.com","isna.ir","irna.ir",
    "farsnews.ir","tabnak.ir","asriran.com","yjc.ir","bartarinha.ir",
    "varzesh3.com","khabarvarzeshi.com","entekhab.ir"
]
QUERY_TERMS = [
    "ویدئو","ویدیو","فیلم","لحظه","جنجالی","پربازدید","عجیب","باورنکردنی",
    "واکنش","مردم","فوتبال","سلبریتی","ایران",
    "خنده دار","بامزه","سوتی","فیل","گربه","سگ","شوخی","درگیری","عکس العمل",
    "عجیب ترین","غافلگیرکننده","اتفاق خنده دار","ویدئوی کوتاه"
]
BLOCK = re.compile(r"\b(porn|sex|sexual|nsfw|gore|self-harm)\b", re.I)
VIDEO_EXT = re.compile(r"\.(?:mp4|webm|mov)(?:\?|#|$)", re.I)
M3U8 = re.compile(r"\.m3u8(?:\?|#|$)", re.I)
OGVIDEO = re.compile(r'<meta[^>]+(?:property|name)=["\']og:video(?::url)?["\'][^>]+content=["\']([^"\']+)', re.I)
VIDEO_SRC = re.compile(r'<(?:source|video)[^>]+(?:src|data-src)=["\']([^"\']+)["\']', re.I)
ABS_VIDEO = re.compile(r'https?://[^"\'<>\s]+\.(?:mp4|webm|mov)(?:\?[^"\'<>\s]*)?', re.I)

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6"})

def load():
    try:
        x = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(x, dict):
            return x
    except Exception:
        pass
    return {"version": 1, "seen_keys": [], "seen_hashes": [], "history": []}

def save(s):
    STATE_PATH.parent.mkdir(exist_ok=True)
    s["seen_keys"] = list(dict.fromkeys(s.get("seen_keys", [])))[-2000:]
    s["seen_hashes"] = list(dict.fromkeys(s.get("seen_hashes", [])))[-1000:]
    s["history"] = s.get("history", [])[-200:]
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def text_of(el):
    return " ".join((el.text or "").split()) if el is not None else ""

def rss_items(query):
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=fa&gl=IR&ceid=IR:fa"
    r = session.get(url, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.findall(".//item"):
        title = html.unescape(text_of(it.find("title")))
        link = text_of(it.find("link"))
        pub = text_of(it.find("pubDate"))
        source = text_of(it.find("source"))
        if not link or BLOCK.search(title):
            continue
        age_h = 999.0
        try:
            age_h = max(
                0.0,
                (datetime.now(timezone.utc) -
                 parsedate_to_datetime(pub).astimezone(timezone.utc)).total_seconds()/3600.0
            )
        except Exception:
            pass
        out.append({"title": title, "link": link, "source": source, "pub": pub, "age_hours": age_h})
    return out

def youtube_video_id(url):
    host = urlparse(str(url or "")).netloc.lower()
    path = urlparse(str(url or "")).path
    query = urlparse(str(url or "")).query
    if "youtu.be" in host:
        return path.strip("/").split("/")[0] or None
    if "youtube.com" in host:
        m = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{6,})", query)
        if m:
            return m.group(1)
        m = re.search(r"/shorts/([A-Za-z0-9_-]{6,})", path)
        if m:
            return m.group(1)
    return None

def enrich_youtube_metrics(items):
    """Attach public YouTube demand metrics before any media download."""
    if not YOUTUBE_KEY:
        return
    ids = []
    for item in items.values():
        vid = str(item.get("video_id") or youtube_video_id(item.get("link")) or "").strip()
        if vid:
            item["video_id"] = vid
            ids.append(vid)
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        try:
            r = session.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"key": YOUTUBE_KEY, "part": "snippet,contentDetails,statistics",
                        "id": ",".join(batch), "maxResults": 50},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print("YOUTUBE_METRICS_FAIL", type(exc).__name__)
            continue
        by_id = {str(x.get("id")): x for x in data.get("items", []) or []}
        for item in items.values():
            raw = by_id.get(str(item.get("video_id") or ""))
            if not raw:
                continue
            stats = raw.get("statistics") or {}
            snippet = raw.get("snippet") or {}
            views = int(stats.get("viewCount") or 0)
            likes = int(stats.get("likeCount") or 0)
            comments = int(stats.get("commentCount") or 0)
            published = str(snippet.get("publishedAt") or item.get("published_at") or "")
            age_h = float(item.get("age_hours") or 999999.0)
            try:
                age_h = max(
                    0.0,
                    (datetime.now(timezone.utc) -
                     datetime.fromisoformat(published.replace("Z", "+00:00"))).total_seconds()/3600.0
                )
            except Exception:
                pass
            item.update({
                "views": views, "likes": likes, "comments": comments,
                "published_at": published,
                "age_hours": age_h,
                "like_rate": likes/max(1, views),
                "comment_rate": comments/max(1, views),
            })



def discover_youtube_public_candidates(limit=25):
    """Direct YouTube demand lane focused on globally distributed Persian/Iranian videos."""
    if not YOUTUBE_KEY:
        return []
    out = {}
    now = datetime.now(timezone.utc)

    def add_video(raw, search_profile=""):
        vid = str(raw.get("id") or "").strip()
        if not vid:
            return
        snippet = raw.get("snippet") or {}
        stats = raw.get("statistics") or {}
        published = str(snippet.get("publishedAt") or "")
        age_h = 999999.0
        if published:
            try:
                age_h = max(0.0, (now - datetime.fromisoformat(published.replace("Z", "+00:00"))).total_seconds()/3600.0)
            except Exception:
                pass
        item = {
            "title": str(snippet.get("title") or "").strip(),
            "description": str(snippet.get("description") or "").strip(),
            "channel_id": str(snippet.get("channelId") or "").strip(),
            "channel_title": str(snippet.get("channelTitle") or "").strip(),
            "link": f"https://www.youtube.com/watch?v={vid}",
            "source": "YouTube public API",
            "video_id": vid,
            "views": int(stats.get("viewCount") or 0),
            "likes": int(stats.get("likeCount") or 0),
            "comments": int(stats.get("commentCount") or 0),
            "published_at": published,
            "age_hours": age_h,
            "metric_source": "youtube_api",
            "youtube_search_profile": search_profile,
        }
        item["key"] = hashlib.sha256(("youtube:" + vid).encode()).hexdigest()
        out[item["key"]] = item

    profiles = [
        ("فارسی", "fa"), ("ایرانی", "fa"), ("ایران", "fa"),
        ("ویدیو ایرانی", "fa"), ("ویدئوی ایرانی", "fa"), ("پرشین", "fa"),
        ("Farsi", "fa"), ("Persian", "fa"), ("Iranian", "fa"), ("Iran", "fa"),
    ]
    q, lang = profiles[int(now.timestamp() // 1800) % len(profiles)]

    try:
        published_after = (now - __import__("datetime").timedelta(hours=168)).isoformat().replace("+00:00", "Z")
        r = session.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": YOUTUBE_KEY, "part": "snippet", "q": q, "type": "video",
                "relevanceLanguage": lang, "order": "viewCount",
                "publishedAfter": published_after, "maxResults": min(50, max(25, limit)),
            },
            timeout=25,
        )
        r.raise_for_status()
        ids = [
            str(x.get("id", {}).get("videoId") or "").strip()
            for x in (r.json().get("items") or [])
            if x.get("id", {}).get("videoId")
        ]
        if ids:
            rr = session.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"key": YOUTUBE_KEY, "part": "snippet,statistics,contentDetails",
                        "id": ",".join(ids[:50]), "maxResults": 50},
                timeout=25,
            )
            rr.raise_for_status()
            for raw in (rr.json().get("items") or []):
                add_video(raw, q)
    except Exception as exc:
        print("YOUTUBE_SEARCH_FAIL", type(exc).__name__, q)

    channel_ids = list(dict.fromkeys(
        str(x.get("channel_id") or "").strip() for x in out.values()
        if str(x.get("channel_id") or "").strip()
    ))
    if channel_ids:
        try:
            cr = session.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"key": YOUTUBE_KEY, "part": "snippet",
                        "id": ",".join(channel_ids[:50]), "maxResults": 50},
                timeout=25,
            )
            cr.raise_for_status()
            by_id = {str(x.get("id")): x for x in (cr.json().get("items") or [])}
            for item in out.values():
                raw = by_id.get(item.get("channel_id"))
                if raw:
                    sn = raw.get("snippet") or {}
                    item["channel_country"] = str(sn.get("country") or "").upper()
                    item["channel_description"] = str(sn.get("description") or "")[:4000]
                    if not item.get("channel_title"):
                        item["channel_title"] = str(sn.get("title") or "")
        except Exception as exc:
            print("YOUTUBE_CHANNEL_META_FAIL", type(exc).__name__)

    return list(out.values())

def normalize_public_number(raw):
    """Parse Persian/English public counters such as 1.2M, ۳٬۲۰۰, 2 میلیون."""
    if raw is None:
        return 0
    s = str(raw).strip().replace("٫", ".").replace("٬", ",").replace("،", ",")
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    s = s.translate(trans)
    m = re.search(r"([0-9][0-9,.\s]*)\s*(تریلیون|میلیارد|بیلیون|میلیون|هزار\s*میلیون|هزار|[KkMmBb])?", s)
    if not m:
        return 0
    try:
        num = float(m.group(1).replace(",", "").replace(" ", ""))
    except Exception:
        return 0
    unit = (m.group(2) or "").replace(" ", "").lower()
    mult = {
        "k": 1e3, "m": 1e6, "b": 1e9,
        "هزار": 1e3, "میلیون": 1e6, "هزارمیلیون": 1e6,
        "میلیارد": 1e9, "بیلیون": 1e9, "تریلیون": 1e12,
    }.get(unit, 1.0)
    return int(max(0, num * mult))

def first_metric(html_text, patterns):
    for pat in patterns:
        m = re.search(pat, html_text, re.I | re.S)
        if not m:
            continue
        value = normalize_public_number(m.group(1))
        if value > 0:
            return value
    return 0

def platform_of(url):
    host = urlparse(str(url or "")).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "x.com" in host or "twitter.com" in host:
        return "x"
    if "aparat.com" in host:
        return "aparat"
    return "web"

def extract_public_platform_metrics(item):
    """Best-effort extraction of public counters from a public social page.
    This is discovery intelligence only; failure is normal and never crashes the hunt.
    """
    url = str(item.get("link") or "")
    platform = platform_of(url)
    if platform not in {"tiktok", "instagram", "x", "aparat"}:
        return {}

    try:
        r = session.get(url, timeout=12, allow_redirects=True)
        if r.status_code >= 400 or "text/html" not in (r.headers.get("content-type") or "").lower():
            return {}
        html_text = r.text[:5_000_000]
    except Exception:
        return {}

    metrics = {}
    if platform == "tiktok":
        metrics["views"] = first_metric(html_text, [
            r'"(?:playCount|play_count|viewCount|views)"\s*:\s*"?(.*?)"?[,}]',
            r'"(?:playCount|play_count|viewCount|views)"\s*[:=]\s*([0-9.,KkMmBb]+)',
        ])
        metrics["likes"] = first_metric(html_text, [
            r'"(?:diggCount|likeCount|like_count|likes)"\s*:\s*"?(.*?)"?[,}]',
            r'"(?:diggCount|likeCount|like_count|likes)"\s*[:=]\s*([0-9.,KkMmBb]+)',
        ])
        metrics["comments"] = first_metric(html_text, [
            r'"(?:commentCount|comment_count|comments)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["shares"] = first_metric(html_text, [
            r'"(?:shareCount|share_count|shares)"\s*:\s*"?(.*?)"?[,}]',
        ])
    elif platform == "instagram":
        metrics["views"] = first_metric(html_text, [
            r'"(?:video_view_count|play_count|videoPlayCount|view_count)"\s*:\s*"?(.*?)"?[,}]',
            r'"(?:play_count|video_view_count)"\s*[:=]\s*([0-9.,KkMmBb]+)',
        ])
        metrics["likes"] = first_metric(html_text, [
            r'"(?:edge_media_preview_like|like_count|likes)"[^{}]{0,120}?"count"\s*:\s*([0-9.,KkMmBb]+)',
            r'"(?:like_count|likes)"\s*:\s*([0-9.,KkMmBb]+)',
        ])
        metrics["comments"] = first_metric(html_text, [
            r'"(?:edge_media_to_comment|comment_count|comments)"[^{}]{0,120}?"count"\s*:\s*([0-9.,KkMmBb]+)',
            r'"(?:comment_count|comments)"\s*:\s*([0-9.,KkMmBb]+)',
        ])
    elif platform == "x":
        metrics["views"] = first_metric(html_text, [
            r'"(?:view_count|views)"\s*:\s*"?(.*?)"?[,}]',
            r'"(?:viewCount)"\s*:\s*([0-9.,KkMmBb]+)',
        ])
        metrics["likes"] = first_metric(html_text, [
            r'"(?:favorite_count|like_count|likes)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["comments"] = first_metric(html_text, [
            r'"(?:reply_count|comment_count|replies)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["shares"] = first_metric(html_text, [
            r'"(?:retweet_count|repost_count|share_count)"\s*:\s*"?(.*?)"?[,}]',
        ])
    elif platform == "aparat":
        metrics["views"] = first_metric(html_text, [
            r'"(?:viewCount|visitCount|view_cnt|views)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["likes"] = first_metric(html_text, [
            r'"(?:likeCount|like_cnt|likes)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["comments"] = first_metric(html_text, [
            r'"(?:commentCount|comment_cnt|comments)"\s*:\s*"?(.*?)"?[,}]',
        ])
        metrics["shares"] = first_metric(html_text, [
            r'"(?:shareCount|share_cnt|shares)"\s*:\s*"?(.*?)"?[,}]',
        ])

    return {k: int(v) for k, v in metrics.items() if int(v or 0) > 0}

def enrich_public_platform_metrics(items, limit=90):
    """Enrich only the most promising social URLs so discovery stays within CI limits."""
    candidates = [
        x for x in items.values()
        if platform_of(x.get("link")) in {"tiktok", "instagram", "x", "aparat"}
    ]
    candidates.sort(
        key=lambda x: (
            float(x.get("age_hours") or 999999),
            0 if platform_of(x.get("link")) in {"tiktok", "instagram"} else 1,
        )
    )
    for item in candidates[:limit]:
        metrics = extract_public_platform_metrics(item)
        if not metrics:
            continue
        item.update(metrics)
        item["metric_source"] = "public_page"
        item["metric_platform"] = platform_of(item.get("link"))
        if item.get("views"):
            age_h = max(0.25, float(item.get("age_hours") or 999999))
            item["velocity_views_per_hour"] = round(item["views"] / age_h, 2)
        item["like_rate"] = item.get("likes", 0) / max(1, item.get("views", 0))
        item["comment_rate"] = item.get("comments", 0) / max(1, item.get("views", 0))
        item["share_rate"] = item.get("shares", 0) / max(1, item.get("views", 0))


def public_identity(item):
    """Stable identity for repeated public-metric observations across scans."""
    vid = str(item.get("video_id") or youtube_video_id(item.get("link")) or "").strip()
    if vid:
        return "youtube:" + vid
    url = str(item.get("link") or "").split("?")[0].rstrip("/")
    plat = platform_of(url)
    if plat in {"tiktok", "instagram", "x", "aparat"}:
        return plat + ":" + url
    title = re.sub(r"\s+", " ", str(item.get("title") or "").lower()).strip()
    return hashlib.sha256((plat + ":" + title).encode("utf-8")).hexdigest()

def apply_observed_momentum(items, state):
    """Use changes between scans to detect breakouts that lifetime counters hide."""
    observations = state.setdefault("observations", {})
    now = datetime.now(timezone.utc)
    for item in items.values():
        metrics = {k: int(item.get(k) or 0) for k in ("views", "likes", "comments", "shares")}
        if not any(metrics.values()):
            continue
        ident = public_identity(item)
        previous = observations.get(ident)
        if previous:
            try:
                prev_at = datetime.fromisoformat(str(previous.get("at")).replace("Z", "+00:00"))
                hours = max(0.01, (now - prev_at).total_seconds() / 3600.0)
            except Exception:
                hours = 0.0
            if hours > 0:
                dv = max(0, metrics["views"] - int(previous.get("views") or 0))
                dl = max(0, metrics["likes"] - int(previous.get("likes") or 0))
                ds = max(0, metrics["shares"] - int(previous.get("shares") or 0))
                dc = max(0, metrics["comments"] - int(previous.get("comments") or 0))
                if dv > 0:
                    item["observed_views_per_hour"] = round(dv / hours, 2)
                if dl > 0:
                    item["observed_likes_per_hour"] = round(dl / hours, 2)
                if ds > 0:
                    item["observed_shares_per_hour"] = round(ds / hours, 2)
                if dc > 0:
                    item["observed_comments_per_hour"] = round(dc / hours, 2)
        item["public_identity"] = ident
        observations[ident] = {"at": now.isoformat(), **metrics}

    if len(observations) > 3000:
        newest = sorted(
            observations.items(),
            key=lambda kv: str(kv[1].get("at") or ""),
            reverse=True,
        )[:3000]
        state["observations"] = dict(newest)

def persian_identity_score(item):
    """Estimate Persian/Iranian identity without using topic or duration."""
    title = str(item.get("title") or "")
    desc = str(item.get("description") or "") + " " + str(item.get("channel_description") or "")
    channel = str(item.get("channel_title") or "")
    source = str(item.get("source") or "")
    profile = str(item.get("youtube_search_profile") or "")
    domain = urlparse(str(item.get("link") or "")).netloc.lower()
    text = " ".join([title, desc, channel, source, profile])

    persian_chars = len(PERSIAN_RE.findall(text))
    score = 0.0
    if persian_chars >= 8:
        score += 0.62
    elif persian_chars >= 3:
        score += 0.42
    if re.search(r"(?i)(persian|farsi|iranian|iran|پرشین|فارسی|ایرانی|ایران)", text):
        score += 0.32
    if str(item.get("channel_country") or "").upper() == "IR":
        score += 0.55
    if any(domain == d or domain.endswith("." + d) for d in SOURCE_DOMAINS):
        score += 0.60
    if profile:
        score += 0.10
    return round(min(1.0, score), 3)

def public_demand_score(item, corroboration=1):
    """Demand-first score. Duration/topic/visual aesthetics do not influence rank."""
    import math
    views = max(0, int(item.get("views") or 0))
    likes = max(0, int(item.get("likes") or 0))
    comments = max(0, int(item.get("comments") or 0))
    shares = max(0, int(item.get("shares") or 0))
    age_h = max(0.25, float(item.get("age_hours") or 999999.0))

    # Public counters are the core evidence. We score absolute scale and growth
    # separately so a fresh breakout can beat an old video with a larger lifetime total.
    volume = min(28.0, 4.7 * math.log10(max(1, views)) if views else 0.0)
    lifetime_velocity = views / age_h if views else 0.0
    observed_velocity = float(item.get("observed_views_per_hour") or 0.0)
    velocity = max(lifetime_velocity, observed_velocity)
    velocity_score = min(30.0, 6.2 * math.log10(max(1, velocity)) if velocity else 0.0)

    like_volume = min(12.0, 2.0 * math.log10(max(1, likes)) if likes else 0.0)
    share_volume = min(12.0, 2.1 * math.log10(max(1, shares)) if shares else 0.0)
    comment_volume = min(6.0, 1.5 * math.log10(max(1, comments)) if comments else 0.0)

    like_rate = likes / max(1, views)
    share_rate = shares / max(1, views)
    comment_rate = comments / max(1, views)
    engagement_quality = (
        min(5.0, 250.0 * like_rate)
        + min(5.0, 1500.0 * share_rate)
        + min(4.0, 1200.0 * comment_rate)
    )

    cross_score = min(10.0, 2.0 * max(0, corroboration - 1))
    freshness = 5.0 * max(0.0, 1.0 - min(age_h / 96.0, 1.0))

    total = min(
        100.0,
        volume + velocity_score + like_volume + share_volume +
        comment_volume + engagement_quality + cross_score + freshness
    )

    metric_fields = sum(1 for v in (views, likes, comments, shares) if v > 0)
    strong_absolute = (
        views >= 10_000 or likes >= 1_000 or shares >= 300 or comments >= 500
    )
    evidence = metric_fields >= 2 and (strong_absolute or views >= 2_000)
    return round(total, 2), {
        "evidence": evidence,
        "metric_fields": metric_fields,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "velocity_views_per_hour": round(velocity, 2),
        "observed_views_per_hour": round(float(item.get("observed_views_per_hour") or 0.0), 2),
        "observed_likes_per_hour": round(float(item.get("observed_likes_per_hour") or 0.0), 2),
        "observed_shares_per_hour": round(float(item.get("observed_shares_per_hour") or 0.0), 2),
        "like_rate": round(like_rate, 6),
        "share_rate": round(share_rate, 6),
        "comment_rate": round(comment_rate, 6),
        "age_hours": round(age_h, 2),
        "metric_source": item.get("metric_source", "youtube_api" if item.get("video_id") else "unknown"),
        "score": round(total, 2),
    }

def article_candidates(item):
    host = urlparse(item["link"]).netloc.lower()
    # Social/video platform URLs can be handed directly to yt-dlp.
    if any(x in host for x in [
        "youtube.com","youtu.be","tiktok.com","instagram.com",
        "x.com","twitter.com","aparat.com"
    ]):
        return [item["link"]]
    try:
        r = session.get(item["link"], timeout=20, allow_redirects=True)
        if r.status_code >= 400:
            return []
        text = r.text
        base = r.url
    except Exception:
        return []
    vals = []
    for pat in (OGVIDEO, VIDEO_SRC, ABS_VIDEO):
        for u in pat.findall(text):
            u = html.unescape(u).replace("\\", "").strip()
            u = __import__("urllib.parse", fromlist=["urljoin"]).urljoin(base, u)
            if VIDEO_EXT.search(u) or M3U8.search(u):
                vals.append(u)
    for u in re.findall(r'https?:\\?/\\?/[^"\\s]+\\?\.(?:mp4|webm)(?:\\?\?[^"\\s]*)?', text, re.I):
        u = u.replace("\\/", "/").replace("\\", "")
        if VIDEO_EXT.search(u):
            vals.append(u)
    seen = set()
    out = []
    for u in vals:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:6]

def download_instagram_via_parth_dl(url):
    """Acquire public Instagram media with parth-dl's multi-path anonymous extractor."""
    outdir = Path(tempfile.mkdtemp(prefix="parth-ig-"))
    try:
        p = subprocess.run(
            [
                "parth-dl", "--no-banner", "-P", str(outdir),
                url,
            ],
            capture_output=True, text=True, timeout=100,
        )
        videos = sorted(
            [x for x in outdir.glob("*.mp4") if x.is_file()],
            key=lambda x: x.stat().st_size,
            reverse=True,
        )
        if p.returncode == 0 and videos:
            print("INSTAGRAM_PARTH_ACQUIRED", videos[0].name)
            return str(videos[0])
        print("INSTAGRAM_PARTH_FAIL", p.returncode, p.stderr[-900:] or p.stdout[-900:])
    except Exception as exc:
        print("INSTAGRAM_PARTH_ERROR", type(exc).__name__)
    return None

def instagram_imginn_routes(url, limit=6):
    """Resolve a public Instagram post through Imginn and return direct CDN media URLs."""
    path = urlparse(str(url or "")).path
    m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)/?", path)
    if not m:
        return []
    shortcode = m.group(1)
    bases = ["https://imginn.com/p/"]
    routes = []
    seen = set()
    for base in bases:
        try:
            page_url = base + shortcode + "/"
            r = session.get(page_url, timeout=18, allow_redirects=True)
            if not r.ok or "text/html" not in (r.headers.get("content-type") or "").lower():
                continue
            text = html.unescape(r.text[:8_000_000]).replace("\\/", "/")
            found = []
            for pat in (
                r'href=["\\\'](https?://[^"\\\']+cdninstagram\\.com/[^"\\\']+(?:\\.mp4|\\?[^"\\\']*))["\\\']',
                r'(https://scontent[^"\\\'<>\\s]+\\.mp4(?:\\?[^"\\\'<>\\s]*)?)',
                r'(https://[^"\\\'<>\\s]*cdninstagram[^"\\\'<>\\s]+)',
                r'<meta[^>]+(?:property|name)=["\\\']og:video(?::url)?["\\\'][^>]+content=["\\\']([^"\\\']+)',
            ):
                found.extend(re.findall(pat, text, re.I))
            for u in found:
                u = html.unescape(u).replace("\\", "").strip()
                if u.startswith("http") and "cdninstagram.com" in u:
                    if u not in seen:
                        seen.add(u)
                        routes.append(u)
                        if len(routes) >= limit:
                            return routes
        except Exception as exc:
            print("INSTAGRAM_IMGINN_FAIL", type(exc).__name__)
    return routes

def download_via_cobalt(url):
    """Use a locally running Cobalt API as the primary social-media acquisition path."""
    if not COBALT_API_URL:
        return None
    try:
        r = session.post(
            COBALT_API_URL + "/",
            json={
                "url": url,
                "videoQuality": "720",
                "downloadMode": "auto",
                "youtubeVideoCodec": "h264",
                "youtubeVideoContainer": "mp4",
                "alwaysProxy": True,
                "disableMetadata": True,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=35,
        )
        if not r.ok:
            print("COBALT_HTTP_FAIL", r.status_code)
            return None
        data = r.json()
        status = str(data.get("status") or "")
        if status in {"redirect", "tunnel"} and str(data.get("url") or "").startswith("http"):
            path = download_direct_file(str(data["url"]), ".mp4")
            if path:
                print("COBALT_ACQUIRED", platform_of(url), data.get("filename", ""))
                return path
        if status == "picker":
            for item in data.get("picker") or []:
                if str(item.get("type") or "") != "video":
                    continue
                u = str(item.get("url") or "")
                if u.startswith("http"):
                    path = download_direct_file(u, ".mp4")
                    if path:
                        print("COBALT_PICKER_ACQUIRED", platform_of(url))
                        return path
        print("COBALT_FAIL", platform_of(url), status, data.get("code", ""))
    except Exception as exc:
        print("COBALT_ERROR", type(exc).__name__)
    return None

def piped_api_instances():
    """Load current public Piped APIs, with static fallbacks."""
    urls = []
    try:
        r = session.get(
            "https://raw.githubusercontent.com/TeamPiped/documentation/main/content/docs/public-instances/index.md",
            timeout=12,
        )
        if r.ok:
            urls.extend(re.findall(r"https://(?:pipedapi[^\s|]+|api\.piped\.yt)", r.text))
    except Exception:
        pass
    urls.extend(PIPED_FALLBACKS)
    out = []
    seen = set()
    for u in urls:
        u = str(u).strip().rstrip(").,")
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:12]

def download_direct_file(url, suffix=".mp4", max_bytes=52 * 1024 * 1024):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        with session.get(url, stream=True, timeout=40, allow_redirects=True) as r:
            r.raise_for_status()
            if "text/html" in (r.headers.get("content-type") or "").lower():
                return None
            total = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(1024 * 128):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return None
                    f.write(chunk)
        if total < 100_000:
            return None
        return path
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        return None

def download_youtube_via_piped(url):
    """Use unauthenticated Piped streams before falling back to yt-dlp."""
    vid = youtube_video_id(url)
    if not vid:
        return None
    for api in piped_api_instances():
        try:
            r = session.get(f"{api}/streams/{vid}", timeout=25)
            if not r.ok:
                continue
            data = r.json()
            streams = [
                s for s in (data.get("videoStreams") or [])
                if str(s.get("mimeType") or "").startswith("video/mp4")
                and not bool(s.get("videoOnly"))
                and str(s.get("url") or "").startswith("http")
                and int(s.get("height") or 0) <= 720
            ]
            streams.sort(key=lambda s: int(s.get("height") or 0), reverse=True)
            for stream in streams:
                path = download_direct_file(str(stream["url"]), ".mp4")
                if path:
                    print("YOUTUBE_PIPED_ACQUIRED", api, vid, stream.get("quality"))
                    return path
        except Exception as exc:
            print("YOUTUBE_PIPED_FAIL", api, type(exc).__name__)
    return None

def search_web_mirror_routes(title, limit=5):
    """Find Persian article copies of the same viral item with direct media URLs."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title:
        return []

    raw_tokens = re.findall(r"[\u0600-\u06ff]{3,}", title)
    stop = {
        "ایرانی", "ایران", "خواننده", "ویدئو", "ویدیو", "فیلم",
        "جنجالی", "پربازدید", "جدید", "این", "آن", "برای",
    }
    tokens = [t for t in raw_tokens if t not in stop]
    compact = " ".join(tokens[:5])

    queries = [
        f'"{title[:180]}"',
        f'"{compact}" ویدئو' if compact else "",
        f'{compact} ویدیو' if compact else "",
    ]
    routes = []
    seen = set()

    for q in [x for x in queries if x]:
        try:
            for item in rss_items(q):
                link = str(item.get("link") or "").strip()
                if not link or platform_of(link) != "web":
                    continue

                page_title = str(item.get("title") or "")
                page_text = f"{page_title} {q}"
                # Require at least one distinctive Persian title token on the
                # mirror page. This is discovery matching, not demand ranking.
                if tokens and not any(t in page_text for t in tokens[:3]):
                    try:
                        rr = session.get(link, timeout=12, allow_redirects=True)
                        if rr.ok:
                            probe = rr.text[:1_500_000]
                            if not any(t in probe for t in tokens[:3]):
                                continue
                    except Exception:
                        continue

                for route in article_candidates(item):
                    if route not in seen:
                        seen.add(route)
                        routes.append(route)
                        if len(routes) >= limit:
                            return routes
        except Exception as exc:
            print("WEB_MIRROR_SEARCH_FAIL", type(exc).__name__)

    return routes

def download_youtube_via_ejs(url):
    """Primary YouTube acquisition via bgutil PO tokens + Deno/EJS."""
    outdir = tempfile.mkdtemp(prefix="ytdlp-pot-")
    try:
        p = subprocess.run(
            [
                "yt-dlp", "--no-playlist", "--no-warnings",
                "--js-runtimes", "deno",
                "--extractor-args", f"youtubepot-bgutilhttp:base_url={BGUTIL_POT_URL}",
                "--extractor-args", "youtube:player-client=mweb",
                "--max-filesize", "50M",
                "-f", "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4]/b",
                "--merge-output-format", "mp4",
                "-o", str(Path(outdir) / "video.%(ext)s"),
                url,
            ],
            capture_output=True, text=True, timeout=75,
        )
        if p.returncode == 0:
            candidates = sorted(Path(outdir).glob("video.*"))
            if candidates:
                print("YOUTUBE_POT_ACQUIRED", youtube_video_id(url) or "")
                return str(candidates[0])
        print("YOUTUBE_POT_FAIL", p.stderr[-1200:])
    except Exception as exc:
        print("YOUTUBE_POT_ERROR", type(exc).__name__)
    return None

def local_download(url):
    if M3U8.search(url):
        return None

    host = urlparse(url).netloc.lower()
    social = any(x in host for x in [
        "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
        "x.com", "twitter.com", "aparat.com"
    ])

    if social:
        is_youtube = "youtube.com" in host or "youtu.be" in host

        # YouTube gets the current official yt-dlp EJS path first; this is
        # faster and avoids depending on third-party mirror availability.
        if is_youtube:
            ejs_path = download_youtube_via_ejs(url)
            if ejs_path:
                return ejs_path

        # Cobalt is especially useful for TikTok/Instagram/X/Aparat.
        cobalt_path = download_via_cobalt(url)
        if cobalt_path:
            return cobalt_path

        # Public Instagram fallback: Imginn exposes the original public media
        # as a CDN URL even when direct Instagram acquisition fails on CI.
        if platform_of(url) == "instagram":
            for media_url in instagram_imginn_routes(url, limit=6):
                path = download_direct_file(media_url, ".mp4")
                if path:
                    print("INSTAGRAM_IMGINN_ACQUIRED", media_url[:140])
                    return path

        if is_youtube:
            piped_path = download_youtube_via_piped(url)
            if piped_path:
                return piped_path

            outdir = tempfile.mkdtemp(prefix="ytdlp-hls-")
            try:
                p = subprocess.run(
                    [
                        "yt-dlp", "--no-playlist", "--no-warnings",
                        "--extractor-args", "youtube:player_client=web_safari",
                        "--max-filesize", "50M",
                        "-f", "best[protocol*=m3u8]/best[ext=mp4]/best",
                        "--hls-prefer-native",
                        "--merge-output-format", "mp4",
                        "-o", str(Path(outdir) / "video.%(ext)s"),
                        url,
                    ],
                    capture_output=True, text=True, timeout=100,
                )
                if p.returncode == 0:
                    candidates = sorted(Path(outdir).glob("video.*"))
                    if candidates:
                        print("YOUTUBE_HLS_ACQUIRED", youtube_video_id(url) or "")
                        return str(candidates[0])
                print("YOUTUBE_HLS_FAIL", p.stderr[-500:])
            except Exception as exc:
                print("YOUTUBE_HLS_ERROR", type(exc).__name__)

        outdir = tempfile.mkdtemp(prefix="ytdlp-")
        try:
            p = subprocess.run(
                [
                    "yt-dlp", "--no-playlist", "--no-warnings",
                    "--max-filesize", "50M",
                    "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b",
                    "--merge-output-format", "mp4",
                    "-o", str(Path(outdir) / "video.%(ext)s"),
                    url,
                ],
                capture_output=True, text=True, timeout=100,
            )
            if p.returncode != 0:
                print("YTDLP_FAIL", host, p.stderr[-500:])
                return None
            candidates = sorted(Path(outdir).glob("video.*"))
            if not candidates:
                return None
            return str(candidates[0])
        except Exception as exc:
            print("YTDLP_ERROR", host, type(exc).__name__)
            return None

    return download_direct_file(url)

def media_info(path):
    try:
        p = subprocess.run(
            ["ffprobe","-v","error","-show_entries","format=duration:stream=width,height,codec_name","-of","json",path],
            capture_output=True, text=True, timeout=15
        )
        if p.returncode != 0:
            return None
        x = json.loads(p.stdout)
        streams = x.get("streams", [])
        v = next((s for s in streams if s.get("width") or s.get("height")), streams[0] if streams else {})
        d = float((x.get("format") or {}).get("duration") or 0)
        return {"duration": d, "width": int(v.get("width") or 0), "height": int(v.get("height") or 0)}
    except Exception:
        return None

def telegram(method, payload):
    r = session.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=payload, timeout=45)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data.get("result")

def content_quality_gate(path, item, mi):
    """Estimate viewer-attraction separately from proven demand.

    Demand answers: "Did people actually watch/interact with this?"
    Attraction answers: "Does the file itself contain a visible hook/event worth
    stopping the scroll for?" This score is intentionally topic/duration agnostic.
    """
    if visual_score is None:
        # Production should install the visual dependencies. Keep a neutral
        # fallback so discovery does not silently become a hard failure if a
        # future runner loses them.
        return 50.0, {
            "model_version": ATTRACTION_MODEL_VERSION,
            "available": False,
            "reason": "visual_dependencies_unavailable",
            "passed": True,
        }

    try:
        visual = visual_score(path) or {}
    except Exception as exc:
        print("VISUAL_SCORE_FAIL", type(exc).__name__)
        return 45.0, {
            "model_version": ATTRACTION_MODEL_VERSION,
            "available": False,
            "reason": type(exc).__name__,
            "passed": False,
        }

    overall = float(visual.get("score") or 0.0)
    first_event = float(visual.get("hook_first_event_score") or 0.0)
    event = float(visual.get("hook_event_score") or 0.0)
    structure = float(visual.get("hook_structure_score") or 0.0)
    novelty = float(visual.get("visual_novelty_score") or 0.0)

    # The first seconds and a real event arc matter more than generic visual
    # polish. This is a proxy for scroll-stop potential, not a beauty score.
    attraction = (
        0.40 * first_event
        + 0.30 * event
        + 0.18 * structure
        + 0.07 * novelty
        + 0.05 * overall
    )

    # Detect the exact failure mode seen in the Helen proof: a clip can be
    # genuinely viral while being too static / weak as a standalone post.
    static_penalty = 0.0
    if first_event < 28.0 and event < 38.0 and structure < 45.0:
        static_penalty = 8.0
    attraction = max(0.0, attraction - static_penalty)

    # A candidate passes when it has a credible early/event hook. Exception:
    # an exceptionally strong demand signal may survive a weaker visual score,
    # but only down to a conservative emergency floor.
    passed = (
        attraction >= ATTRACTION_FLOOR
        and (
            first_event >= 32.0
            or event >= 42.0
            or structure >= 52.0
        )
    )

    metrics = {
        "model_version": ATTRACTION_MODEL_VERSION,
        "available": True,
        "attraction_score": round(attraction, 2),
        "visual_score": round(overall, 2),
        "hook_event_score": round(event, 2),
        "hook_first_event_score": round(first_event, 2),
        "hook_structure_score": round(structure, 2),
        "visual_novelty_score": round(novelty, 2),
        "static_penalty": static_penalty,
        "duration_seconds": round(float(mi.get("duration") or 0.0), 2),
        "passed": passed,
    }
    return round(attraction, 2), metrics

def send_video(path, title, source):
    cap = ("🔥 ویدئوی داغ ایران\n\n" + title[:700] + "\n\n" +
           "منبع کشف: " + source[:120]).strip()
    with open(path, "rb") as f:
        r = session.post(
            f"https://api.telegram.org/bot{TOKEN}/sendVideo",
            data={"chat_id": TARGET, "caption": cap, "supports_streaming": "true"},
            files={"video": ("viral.mp4", f, "video/mp4")},
            timeout=90,
        )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]

def score(item, corroboration):
    return public_demand_score(item, corroboration)[0]

def main():
    if not TOKEN or not TARGET:
        raise RuntimeError("Telegram secrets missing")
    state = load()
    seen_keys = set(state.get("seen_keys", []))
    seen_hashes = set(state.get("seen_hashes", []))

    one_shot_url = os.environ.get("ONE_SHOT_VIDEO_URL", "").strip()
    if one_shot_url:
        path = local_download(one_shot_url)
        if not path:
            raise RuntimeError("One-shot video download failed")
        try:
            h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            mi = media_info(path)
            if not mi or mi["duration"] < 2 or mi["duration"] > 600 or mi["width"] < 240 or mi["height"] < 240:
                raise RuntimeError("One-shot video failed media quality gate")
            if Path(path).stat().st_size > 50 * 1024 * 1024:
                raise RuntimeError("One-shot video exceeds Telegram upload limit")
            sent = send_video(path, "تست خروجی — ویدئوی داغ ایران", "Hamshahri Online")
            entry = {
                "at": datetime.now(timezone.utc).isoformat(),
                "title": "تست خروجی — ویدئوی داغ ایران",
                "article": "https://www.hamshahrionline.ir/",
                "video": one_shot_url,
                "sha256": h,
                "telegram_message_id": sent.get("message_id"),
                "score": 100,
                "cross_sources": 1,
                "duration": mi["duration"],
                "width": mi["width"],
                "height": mi["height"],
                "test_mode": True,
            }
            state["seen_hashes"].append(h)
            state["last_copy"] = entry
            state["history"].append(entry)
            save(state)
            print(json.dumps({"ONE_SHOT_SENT": entry}, ensure_ascii=False, indent=2))
            return 0
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass

    platform_queries = [
        'site:youtube.com/shorts ("وایرال" OR "پربازدید" OR "میلیون بازدید" OR "ترند")',
        'site:instagram.com/reel ("وایرال" OR "پربازدید" OR "میلیون بازدید" OR "ترند")',
        'site:tiktok.com ("وایرال" OR "پربازدید" OR "میلیون بازدید" OR "ترند")',
        'site:aparat.com/v ("وایرال" OR "پربازدید" OR "میلیون بازدید" OR "ترند")',
        'site:x.com ("ویدئو" OR "ویدیو") ("وایرال" OR "پربازدید" OR "ترند")',
        'site:twitter.com ("ویدئو" OR "ویدیو") ("وایرال" OR "پربازدید" OR "ترند")',
    ]
    queries = [f"site:{d} (ویدئو OR ویدیو OR فیلم) (وایرال OR پربازدید OR ترند)" for d in SOURCE_DOMAINS]
    queries.extend([
        '("ویدئو" OR "ویدیو") ("میلیون بازدید" OR "صدها هزار بازدید" OR "وایرال") ایران',
        '("ویدئو" OR "ویدیو") ("پربازدیدترین" OR "ترند") ایران',
        '("ویدئو" OR "ویدیو") ("بازدید بالا" OR "بازدید میلیونی") ایران',
    ])
    queries.extend(platform_queries)
    items = {}

    # Feed fresh YouTube chart discoveries from the global radar into the
    # same candidate queue, so a strong video can move straight to testing.
    radar_path = ROOT / "attention_state" / "public_radar.json"
    try:
        radar = json.loads(radar_path.read_text(encoding="utf-8"))
        for s in radar.get("signals", []):
            link = str(s.get("url") or s.get("video_url") or "").strip()
            title = str(s.get("title") or "").strip()
            if not link or not title:
                continue
            x = {
                "title": title,
                "link": link,
                "source": "YouTube radar",
                "pub": str(s.get("published_at") or ""),
                "age_hours": max(0.0, float(s.get("age_hours") or 0.0)),
            }
            key_text = re.sub(r"\\s+", " ", title.lower()) + "|" + link.split("?")[0]
            x["key"] = hashlib.sha256(key_text.encode()).hexdigest()
            items[x["key"]] = x
    except Exception as e:
        print("RADAR_FEED_FAIL", type(e).__name__)
    for q in queries:
        try:
            for x in rss_items(q):
                key_text = re.sub(r"\s+", " ", x["title"].lower()) + "|" + x["link"].split("?")[0]
                x["key"] = hashlib.sha256(key_text.encode()).hexdigest()
                items[x["key"]] = x
        except Exception as e:
            print("RSS_FAIL", q, type(e).__name__)

    for yt_item in discover_youtube_public_candidates(limit=25):
        items[yt_item["key"]] = yt_item

    # Merge asynchronous multi-platform discoveries (TikTok/Instagram/X/Aparat)
    # produced by the dedicated discovery workflow.
    social_path = ROOT / "attention_state" / "social_discovery.json"
    try:
        social = json.loads(social_path.read_text(encoding="utf-8"))
        for s in social.get("items", []) or []:
            link = str(s.get("url") or "").strip()
            title = str(s.get("title") or "").strip()
            if not link or not title:
                continue
            key_text = re.sub(r"\s+", " ", title.lower()) + "|" + link.split("?")[0]
            x = {
                "title": title,
                "link": link,
                "source": "social discovery",
                "platform": s.get("platform"),
                "pub": "",
                "age_hours": 999999.0,
                "views": int(s.get("views") or 0),
                "likes": int(s.get("likes") or 0),
                "comments": int(s.get("comments") or 0),
                "shares": int(s.get("shares") or 0),
                "metric_source": "social_discovery",
            }
            x["key"] = hashlib.sha256(key_text.encode()).hexdigest()
            items[x["key"]] = x
    except Exception as exc:
        print("SOCIAL_DISCOVERY_FEED_FAIL", type(exc).__name__)

    enrich_youtube_metrics(items)
    enrich_public_platform_metrics(items, limit=35)
    apply_observed_momentum(items, state)

    # Demand-first: only public engagement evidence can qualify a candidate.
    # Acquisition is deliberately separated from selection: a hard-to-download
    # viral video remains a top candidate while alternate URLs for the same
    # underlying item are collected from corroborating web/social sources.
    ranked = []
    title_buckets = {}
    signature_by_key = {}
    for x in items.values():
        norm = re.sub(r"[^\w\u0600-\u06ff ]", " ", str(x.get("title") or "").lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        tokens = [t for t in norm.split() if len(t) > 2]
        key = " ".join(tokens[:12])
        short_key = " ".join(tokens[:8])
        title_buckets.setdefault(key, 0)
        title_buckets[key] += 1
        signature_by_key[x["key"]] = (short_key, set(tokens[:18]))

    media_route_cache = {}
    def cached_routes(item):
        link = str(item.get("link") or "")
        if link in media_route_cache:
            return list(media_route_cache[link])
        routes = article_candidates(item)
        media_route_cache[link] = list(routes)
        return list(routes)

    all_items = list(items.values())
    identity_rejections = 0

    for x in all_items:
        if x["key"] in seen_keys:
            continue

        norm = re.sub(r"[^\w\u0600-\u06ff ]", " ", str(x.get("title") or "").lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        tokens = [t for t in norm.split() if len(t) > 2]
        stem = " ".join(tokens[:12])
        short_key = " ".join(tokens[:8])
        corroboration = max(1, title_buckets.get(stem, 1))

        # Start with the source that supplied the demand evidence.
        vids = cached_routes(x)

        # Then search for the same story/video in corroborating sources. This
        # lets a high-demand YouTube/TikTok/etc. item acquire through an article
        # page that exposes a direct MP4 even when the social URL is blocked.
        related = []
        for y in all_items:
            if y["key"] == x["key"]:
                continue
            ysig = signature_by_key.get(y["key"], ("", set()))[1]
            if not ysig:
                continue
            overlap = len(set(tokens[:18]) & ysig) / max(1, len(set(tokens[:18]) | ysig))
            yshort = signature_by_key.get(y["key"], ("", set()))[0]
            if (short_key and short_key == yshort) or overlap >= 0.62:
                related.append(y)

        for y in sorted(related, key=lambda z: float(z.get("age_hours") or 999999))[:8]:
            for route in cached_routes(y):
                if route not in vids:
                    vids.append(route)

        identity_score = persian_identity_score(x)
        x["persian_identity_score"] = identity_score
        if identity_score < 0.45:
            identity_rejections += 1
            continue

        demand, dm = public_demand_score(x, corroboration)
        if not dm.get("evidence"):
            continue
        if demand < 60.0:
            continue
        for u in vids:
            ranked.append((x, u, corroboration, demand, dm))

    ranked.sort(
        key=lambda z: (
            -z[3],
            -int(z[0].get("shares") or 0),
            -int(z[0].get("likes") or 0),
            -int(z[0].get("views") or 0),
            float(z[0].get("age_hours") or 999999),
        )
    )

    # Before acquisition, enrich only the top demand candidates with article
    # mirrors. This preserves demand-first ranking while adding alternate MP4 routes.
    expanded = list(ranked)
    mirror_seen = set()
    for base in ranked[:20]:
        x, original_url, cross, demand, dm = base
        if platform_of(original_url) not in {"youtube", "tiktok", "instagram", "x"}:
            continue
        ident = public_identity(x)
        if ident in mirror_seen:
            continue
        mirror_seen.add(ident)
        for route in search_web_mirror_routes(x.get("title"), limit=5):
            if route != original_url:
                expanded.append((x, route, cross, demand, dm))
    expanded.sort(
        key=lambda z: (
            -z[3],
            -int(z[0].get("shares") or 0),
            -int(z[0].get("likes") or 0),
            -int(z[0].get("views") or 0),
            float(z[0].get("age_hours") or 999999),
        )
    )
    ranked = expanded

    print("DISCOVERED_ARTICLES", len(items),
          "PERSIAN_IDENTITY_REJECTIONS", identity_rejections,
          "MEASURABLE_HIGH_DEMAND_CANDIDATES", len(ranked))

    chosen = None
    acquisition_attempts = []
    reviewed = []

    # Review several of the strongest demand candidates instead of publishing
    # the first file that happens to download. This preserves "viral first"
    # while allowing the file itself to prove that it is watchable.
    for item, url, cross, demand, dm in ranked[:ACQUISITION_REVIEW_LIMIT]:
        path = local_download(url)
        if not path:
            acquisition_attempts.append({
                "title": item["title"], "platform": platform_of(url),
                "demand_score": demand, "result": "download_failed"
            })
            continue
        try:
            h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if h in seen_hashes or item["key"] in seen_keys:
                acquisition_attempts.append({
                    "title": item["title"], "platform": platform_of(url),
                    "demand_score": demand, "result": "duplicate"
                })
                os.unlink(path)
                continue

            mi = media_info(path)
            if (
                not mi
                or mi["duration"] < 2
                or mi["duration"] > 900
                or mi["width"] < 240
                or mi["height"] < 240
                or Path(path).stat().st_size > 50 * 1024 * 1024
            ):
                acquisition_attempts.append({
                    "title": item["title"], "platform": platform_of(url),
                    "demand_score": demand, "result": "media_rejected"
                })
                os.unlink(path)
                continue

            attraction, cq = content_quality_gate(path, item, mi)
            emergency_ok = (
                demand >= DEMAND_EMERGENCY_THRESHOLD
                and attraction >= ATTRACTION_EMERGENCY_FLOOR
            )
            publishable = bool(cq.get("passed")) or emergency_ok

            publish_score = round(
                0.72 * float(demand)
                + 0.28 * float(attraction),
                2,
            )

            print("DEMAND_METRICS", json.dumps(dm, ensure_ascii=False))
            print("ATTRACTION_METRICS", json.dumps(cq, ensure_ascii=False))

            reviewed.append({
                "title": item["title"],
                "platform": platform_of(url),
                "demand_score": round(float(demand), 2),
                "attraction_score": round(float(attraction), 2),
                "publish_score": publish_score,
                "publishable": publishable,
            })

            if not publishable:
                acquisition_attempts.append({
                    "title": item["title"],
                    "platform": platform_of(url),
                    "demand_score": demand,
                    "attraction_score": attraction,
                    "publish_score": publish_score,
                    "result": "attraction_rejected",
                    "attraction_metrics": cq,
                })
                os.unlink(path)
                continue

            item["demand_metrics"] = dm
            item["content_quality_score"] = attraction
            item["content_quality_metrics"] = cq
            acquisition_attempts.append({
                "title": item["title"], "platform": platform_of(url),
                "demand_score": demand, "attraction_score": attraction,
                "publish_score": publish_score, "result": "acquired_reviewed"
            })
            # Keep the best publishable candidate found so far; demand remains
            # the dominant component, attraction is the anti-boring tie-breaker.
            reviewed_entry = (
                publish_score, demand, item, url, path, h, mi, cross, attraction, cq
            )
            if chosen is None or reviewed_entry[:2] > chosen[:2]:
                if chosen is not None:
                    try:
                        os.unlink(chosen[4])
                    except Exception:
                        pass
                chosen = reviewed_entry
            else:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        except Exception as exc:
            acquisition_attempts.append({
                "title": item["title"], "platform": platform_of(url),
                "demand_score": demand, "result": "processing_error",
                "error": type(exc).__name__
            })
            try:
                os.unlink(path)
            except Exception:
                pass

    reviewed.sort(
        key=lambda x: (
            -float(x.get("publish_score") or 0),
            -float(x.get("demand_score") or 0),
            -float(x.get("attraction_score") or 0),
        )
    )
    state["last_reviewed_candidates"] = reviewed[:ACQUISITION_REVIEW_LIMIT]

    if not chosen:
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        state["last_result"] = "no_high_demand_video_acquired"
        state["last_ranked_candidates"] = [
            {
                "title": z[0].get("title"),
                "platform": platform_of(z[1]),
                "demand_score": z[3],
                "demand_metrics": z[4],
                "persian_identity_score": z[0].get("persian_identity_score"),
                "link": z[0].get("link"),
            }
            for z in ranked[:20]
        ]
        state["last_acquisition_attempts"] = acquisition_attempts[-40:]
        state["last_reviewed_candidates"] = reviewed[:ACQUISITION_REVIEW_LIMIT]
        save(state)
        print("NO_HIGH_DEMAND_VIDEO_ACQUIRED")
        return 0

    _, _, item, url, path, h, mi, cross, attraction, cq = chosen
    demand_score = float(item.get("demand_metrics", {}).get("score", 0) or 0)
    if demand_score <= 0:
        demand_score = next(
            (float(z[3]) for z in ranked if z[0].get("key") == item.get("key") and z[1] == url),
            0.0,
        )
    sent = send_video(path, item["title"], item["source"] or urlparse(item["link"]).netloc)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "title": item["title"], "article": item["link"], "video": url,
        "sha256": h, "telegram_message_id": sent.get("message_id"),
        "score": demand_score, "demand_score": demand_score, "demand_metrics": item.get("demand_metrics", {}),
        "attraction_score": round(float(attraction), 2),
        "content_quality_score": round(float(attraction), 2),
        "content_quality_metrics": cq,
        "publish_score": round(0.72 * float(demand_score) + 0.28 * float(attraction), 2),
        "cross_sources": cross, "platform": platform_of(url), "duration": mi["duration"],
        "width": mi["width"], "height": mi["height"]
    }
    state["seen_keys"].append(item["key"])
    state["seen_hashes"].append(h)
    state["last_copy"] = entry
    state["history"].append(entry)
    save(state)
    print(json.dumps({"SELECTED": entry}, ensure_ascii=False, indent=2))
    try: os.unlink(path)
    except Exception: pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
