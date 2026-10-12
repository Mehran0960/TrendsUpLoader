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
VISUAL_IMPORT_ERROR = ""
try:
    from content_pipeline.attention_engine import visual_score, source_hook_score
except Exception as exc:
    visual_score = None
    source_hook_score = None
    VISUAL_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    print("VISUAL_ENGINE_IMPORT_FAIL", VISUAL_IMPORT_ERROR)

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
# When Telegram exposes only views, require stronger demand, a specific
# broad-interest cue, and a real visual hook; views and clickbait wording alone
# are not enough for general-audience distribution.
MIN_SINGLE_METRIC_TELEGRAM_DEMAND = 51.0
MIN_EVENT_ONLY_TELEGRAM_DEMAND = 58.0
MIN_SINGLE_METRIC_TELEGRAM_SHAREABILITY = 48.0
MIN_SINGLE_METRIC_TELEGRAM_VIEWS = 50000
MAX_SINGLE_METRIC_TELEGRAM_AGE_HOURS = 24.0
MIN_SINGLE_METRIC_TELEGRAM_ATTRACTION = 55.0
MIN_SINGLE_METRIC_TELEGRAM_FIRST_EVENT = 38.0
MIN_SINGLE_METRIC_TELEGRAM_EVENT = 48.0
MIN_SINGLE_METRIC_TELEGRAM_STRUCTURE = 54.0
# Small, fresh comedy clips get one exploratory download slot, but face a
# stricter visual attraction/hook bar before any delivery.
MIN_COMEDY_EXPLORATION_VIEWS = 10000
MIN_COMEDY_EXPLORATION_DEMAND = 43.0
MIN_COMEDY_EXPLORATION_ATTRACTION = 62.0
MAX_COMEDY_EXPLORATION_DURATION_SECONDS = 60.0
MIN_TECH_EXPLORATION_VIEWS = 5000
MIN_TECH_EXPLORATION_DEMAND = 43.0
MIN_TECH_EXPLORATION_ATTRACTION = 64.0
MAX_TECH_EXPLORATION_AGE_HOURS = 36.0
MAX_TECH_EXPLORATION_DURATION_SECONDS = 55.0
MIN_TECH_EXPLORATION_SHAREABILITY = 48.0
MAX_ROUTINE_SHORTFORM_SECONDS = 75.0
# Roundups can look visually busy while lacking one memorable payoff.
# They are allowed only when demand and the first/event/structure hooks are exceptional.
COMPILATION_TITLE_RE = re.compile(
    r"(?:\b(?:compilation|roundup|recap|best\s+of|top\s*\d+|"
    r"funniest\s+moments|multiple\s+stories|three\s+stories)\b|"
    r"(?:گلچین|گزیده|میکس|مروری\s*بر|مجموعه\s*(?:ای\s*از|از)|"
    r"(?:چند|سه|چهار|پنج)\s+(?:ماجرا|داستان|اتفاق|کلیپ|ویدیو)|"
    r"روایت\s+(?:چند|سه|چهار|پنج)\s+(?:ماجرا|داستان|اتفاق)|"
    r"در.{0,20}چه\s*می.?گذرد|چه\s*می.?گذرد.{0,20}))",
    re.I,
)
ACQUISITION_REVIEW_LIMIT = 8
MIRROR_SEARCH_CANDIDATE_LIMIT = 4
YOUTUBE_BOTWALL_DETECTED = False
YOUTUBE_COBALT_FALLBACK_ATTEMPTED = False

SOURCE_DOMAINS = [
    "hamshahrionline.ir","khabaronline.ir","mehrnews.com","isna.ir","irna.ir",
    "farsnews.ir","tabnak.ir","asriran.com","yjc.ir","bartarinha.ir",
    "varzesh3.com","khabarvarzeshi.com","entekhab.ir"
]
QUERY_TERMS = [
    "ویدئو","ویدیو","فیلم","لحظه","جنجالی","پربازدید","عجیب","باورنکردنی",
    "واکنش","مردم","فوتبال","سلبریتی","ایران",
    "خنده دار","بامزه","سوتی","فیل","گربه","سگ","شوخی","درگیری","عکس العمل",
    "عجیب ترین","غافلگیرکننده","اتفاق خنده دار","ویدئوی کوتاه",
    "هوش مصنوعی","AI","ربات","تکنولوژی","فناوری","ساخت ویدیو با هوش مصنوعی",
    "ترفند کاربردی","قبل و بعد","تغییر باورنکردنی","معما","خطای دید",
    "آزمایش علمی","تردستی","مهارت عجیب","ویدئوی رضایت بخش","حیوانات بامزه"
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
    rejections = s.get("quality_rejections", {})
    if isinstance(rejections, dict) and len(rejections) > 1000:
        s["quality_rejections"] = dict(sorted(
            rejections.items(),
            key=lambda kv: str((kv[1] or {}).get("at") or ""),
            reverse=True,
        )[:1000])
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def record_quality_rejection(state, key, reason, attraction=None):
    """Temporarily cool down a downloaded clip that already failed quality gates."""
    cooldown_hours = {
        "media_rejected": 8.0,
        "general_audience_gate_rejected": 10.0,
        "attraction_rejected": 18.0,
    }.get(str(reason), 12.0)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": str(reason),
        "cooldown_hours": cooldown_hours,
    }
    if attraction is not None:
        record["attraction_score"] = round(float(attraction), 2)
    state.setdefault("quality_rejections", {})[str(key)] = record


def active_quality_rejection(state, key):
    """Return true for a recent quality rejection; expire old records automatically."""
    rejections = state.setdefault("quality_rejections", {})
    record = rejections.get(str(key))
    if not isinstance(record, dict):
        return False
    try:
        rejected_at = datetime.fromisoformat(str(record.get("at") or "").replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - rejected_at).total_seconds() / 3600.0
        cooldown_hours = max(0.0, float(record.get("cooldown_hours") or 12.0))
    except (TypeError, ValueError):
        rejections.pop(str(key), None)
        return False
    if age_hours < cooldown_hours:
        return True
    rejections.pop(str(key), None)
    return False

def text_of(el):
    return " ".join((el.text or "").split()) if el is not None else ""

def rss_items(query, timeout=20):
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=fa&gl=IR&ceid=IR:fa"
    r = session.get(url, timeout=timeout)
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


def google_trends_ir(limit=12):
    """Fetch public Iran Trending Now topics through Google's RSS fallback (no API key)."""
    urls = [
        "https://trends.google.com/trending/rss?geo=IR&hl=fa",
        "https://trends.google.com/trending/rss?geo=IR",
    ]
    for url in urls:
        try:
            r = session.get(url, timeout=12)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            out, seen = [], set()
            for it in root.findall(".//item"):
                topic = html.unescape(text_of(it.find("title"))).strip()
                norm = re.sub(r"\s+", " ", topic.lower()).strip()
                if not topic or len(topic) > 100 or norm in seen or BLOCK.search(topic):
                    continue
                seen.add(norm)
                pub = text_of(it.find("pubDate"))
                age_h = 9999.0
                try:
                    age_h = max(0.0, (datetime.now(timezone.utc) -
                        parsedate_to_datetime(pub).astimezone(timezone.utc)).total_seconds()/3600.0)
                except Exception:
                    pass
                traffic_node = it.find("{http://www.google.com/trends/hottrends}approx_traffic")
                traffic_label = text_of(traffic_node)
                traffic = normalize_public_number(traffic_label)
                position = len(out) + 1
                rank_score = max(45.0, 100.0 - 7.0 * (position - 1))
                if traffic:
                    import math
                    volume_score = min(100.0, 28.0 + 8.0 * math.log10(max(1, traffic)))
                    rank_score = 0.75 * rank_score + 0.25 * volume_score
                out.append({
                    "query": topic,
                    "position": position,
                    "trend_score": round(rank_score, 2),
                    "search_volume": traffic,
                    "search_volume_label": traffic_label,
                    "published_at": pub,
                    "age_hours": round(age_h, 2),
                    "source": "Google Trends Iran RSS",
                })
                if len(out) >= max(1, int(limit)):
                    break
            print("GOOGLE_TRENDS_IR", json.dumps({
                "status": "ok" if out else "empty",
                "count": len(out),
                "topics": [{"query": x["query"], "score": x["trend_score"]} for x in out[:8]],
            }, ensure_ascii=False))
            if out:
                return out
        except Exception as exc:
            print("GOOGLE_TRENDS_IR_FAIL", type(exc).__name__)
    return []


def trend_match_ratio(text, topic):
    """Return a conservative title/topic overlap ratio; never treat every hot topic as a video."""
    norm_text = re.sub(r"[^\w\u0600-\u06ff ]", " ", str(text or "").lower())
    norm_text = re.sub(r"\s+", " ", norm_text).strip()
    norm_topic = re.sub(r"[^\w\u0600-\u06ff ]", " ", str(topic or "").lower())
    norm_topic = re.sub(r"\s+", " ", norm_topic).strip()
    if not norm_text or not norm_topic:
        return 0.0
    if norm_topic in norm_text:
        return 1.0
    tokens = [t for t in norm_topic.split() if len(t) > 2]
    if not tokens:
        return 0.0
    text_tokens = set(norm_text.split())
    hits = sum(1 for t in set(tokens) if t in text_tokens)
    return hits / max(1, len(set(tokens)))


def apply_trend_signals(items, topics):
    """Attach trend metadata only where the candidate title supports topic relevance."""
    matched = 0
    for item in items.values():
        title = str(item.get("title") or "")
        best_score, best_topic = 0.0, ""
        matching_topics = []
        for topic in topics:
            ratio = trend_match_ratio(title, topic.get("query"))
            if ratio < 0.50:
                continue
            matching_topics.append(topic.get("query"))
            score = float(topic.get("trend_score") or 0.0) * (0.65 + 0.35 * ratio)
            if score > best_score:
                best_score, best_topic = score, str(topic.get("query") or "")
        item["trend_score"] = round(best_score, 2)
        item["trend_topic"] = best_topic
        item["trend_topics"] = list(dict.fromkeys(matching_topics))[:4]
        if best_score > 0:
            item["discovery_lane"] = "trend_and_viral" if item.get("metric_source") else "trend_discovery"
            matched += 1
    print("TREND_MATCHED_CANDIDATES", matched)


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
    if "t.me" in host or "telegram.me" in host:
        return "telegram"
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

PROMOTIONAL_CONTENT = re.compile(
    r"(?:casino|betting|gambling|شرط\s*بندی|قمار|کازینو|بلک\s*جک|بونوس|کد\s*تخفیف|سفارش\s*تبلیغ|برای\s*تبلیغ|بازگشت\s*نقدی|برگشت\s*نقدی|پک\s*رایگان|لینک\s*خرید|ثبت\s*نام|عضو\s*شو|کسب\s*درآمد|میلیون\s*ریالی|ارزونترین\s*فیلترشکن|ارزانترین\s*فیلترشکن|فقط\s*یک\s*کانفیگ|خرید\s*اتوماتیک|فروش\s*کانفیگ|هر\s*گیگ.{0,25}تومان)",
    re.I,
)
HARD_NEWS_CONTENT = re.compile(
    r"(?:وزیر\s+خارجه|برنامه\s+هسته.?ای|توافق\s+هسته.?ای|سلاح\s+هسته.?ای|ترامپ.{0,80}(?:ایران|سلاح)|ایران.{0,60}(?:سلاح\s+هسته|برنامه\s+هسته)|نماینده\s+مجلس|واگذاری.{0,30}خاک|خبر\s+فوری|رییس\s+جمهور|رئیس\s+جمهور|محسن\s+زنگنه|اعلام\s+کرد.{0,40}(?:ایران|مجلس|دولت|آمریکا)|سپاه.{0,30}(?:آمریکا|اسرائیل|جزیره)|ویروس\s+ناشناخته|طاعون|قرنطینه|بیمارستان.{0,30}بسته|صد\s*برابر\s*کشنده)",
    re.I,
)
SENSITIVE_HARM_CONTENT = re.compile(
    r"(?:جسد|کشته\s*شد|جان\s*باخت|مرگبار|خونین|قطع\s*عضو|تصادف\s*شدید|برق\s*گرفتگی|برق\s*گرفت|زیر\s*آوار|آوار\s*روی|چاقوکشی|تیراندازی|شلیک|کودک.?آزاری|تجاوز|آزار\s*جنسی|دیوار.{0,30}(?:میوفته|می.?افته|افتاد|می.?ریزه|فرو\s*ریخت).{0,18}(?:روشون|روی|سر|خانم|زن|مردم|آدم))",
    re.I,
)
UNSAFE_BRAND_CONTENT = re.compile(r"(?<!\w)(?:گوه|کیر|کون|کس|کص|کصکش|کسکش|جنده|کثافت|حشری|سکس|پورن|زناشویی)(?!\w)|فحش\s*رکیک", re.I)
HUMOR_CUES = ("😂", "🤣", "😅", "😆", "خنده دار", "خنده‌دار", "طنز", "شوخی", "سوتی", "بامزه", "مستر بین", "میم", "فان")
RELATABLE_CUES = ("مامان", "مادر", "بابام", "بابا", "مدرسه", "معلم", "کلاس", "خانواده", "همسر", "شوهر", "رفیق", "دوست", "کار ", "پسرا", "دخترا", "زندگی روزمره", "دوسم", "عاشق", "عشق", "رابطه", "دلتنگ", "خواستگار", "مجرد", "ازدواج", "دوست دختر", "دوست‌دختر", "دوست پسر", "دوست‌پسر")
TECH_CUES = ("هوش مصنوعی", "هوش‌مصنوعی", "تکنولوژی", "فناوری", "ربات", "گجت", "چت جی پی تی", "چت‌جی‌پی‌تی", "sora", "veo", "gemini", "chatgpt", "robot")
ANIMAL_CUES = ("گربه", "سگ", "حیوان", "میمون", "پرنده", "گاو", "اسب", "cat", "dog", "animal", "pet")
PUZZLE_CUES = ("معما", "چیستان", "معمای تصویری", "خطای دید", "پیداش کن", "پیدا کن", "کدومش", "کدام یک", "spot the difference", "optical illusion", "puzzle", "brain teaser")
SKILL_CUES = ("تردستی", "آکروبات", "پشتک", "مهارت عجیب", "حرکت دیدنی", "رکورد", "شکست رکورد", "ترفند", "نجاری", "کاردستی", "life hack", "magic trick", "skill")
SATISFYING_CUES = ("قبل و بعد", "ترمیم", "بازسازی", "تمیزکاری", "رضایت بخش", "رضایت‌بخش", "برش دقیق", "آشپزی", "تبدیل", "restoration", "satisfying", "before and after")
SURPRISE_CUES = ("عجیب", "باورنکردنی", "غافلگیر", "غیرمنتظره", "آخرش", "ناگهان", "چطور ممکن", "چطوری", "راز", "قبل و بعد", "تغییر باورنکردنی", "این شکلی", "شوکه", "شوک", "انتظارشو نداشتم", "انتظار نداشتم")


def content_shareability_proxy(item):
    """Caption/title heuristic, not a semantic model or a measured share rate."""
    blob = " ".join([str(item.get("title") or ""), str(item.get("description") or "")]).lower()
    promotion = bool(PROMOTIONAL_CONTENT.search(blob))
    hard_news = bool(HARD_NEWS_CONTENT.search(blob))
    sensitive_harm = bool(SENSITIVE_HARM_CONTENT.search(blob) or UNSAFE_BRAND_CONTENT.search(blob))
    humor_hits = sum(1 for cue in HUMOR_CUES if cue.lower() in blob)
    relatable_hits = sum(1 for cue in RELATABLE_CUES if cue.lower() in blob)
    surprise_hits = sum(1 for cue in SURPRISE_CUES if cue.lower() in blob)
    tech_hits = sum(1 for cue in TECH_CUES if cue.lower() in blob)
    if re.search(r"AI", blob, re.I):
        tech_hits += 1
    animal_hits = sum(1 for cue in ANIMAL_CUES if cue.lower() in blob)
    puzzle_hits = sum(1 for cue in PUZZLE_CUES if cue.lower() in blob)
    skill_hits = sum(1 for cue in SKILL_CUES if cue.lower() in blob)
    satisfying_hits = sum(1 for cue in SATISFYING_CUES if cue.lower() in blob)
    score = (
        40.0
        + min(25.0, humor_hits * 8.0)
        + min(15.0, relatable_hits * 5.0)
        + min(20.0, surprise_hits * 6.0)
        + min(8.0, tech_hits * 8.0)
        + min(8.0, animal_hits * 4.0)
        + min(8.0, puzzle_hits * 6.0)
        + min(8.0, skill_hits * 4.0)
        + min(8.0, satisfying_hits * 5.0)
    )
    if re.search(r"[😂🤣😅😆😍🤯❤️🔥]", blob):
        score += 3.0
    if hard_news:
        score -= 14.0
    if promotion:
        score -= 50.0
    return round(max(0.0, min(100.0, score)), 2), {
        "promotional": promotion,
        "hard_news": hard_news,
        "sensitive_harm": sensitive_harm,
        "humor_cue_count": humor_hits,
        "relatable_cue_count": relatable_hits,
        "surprise_cue_count": surprise_hits,
        "technology_cue_count": tech_hits,
        "animal_cue_count": animal_hits,
        "puzzle_cue_count": puzzle_hits,
        "skill_cue_count": skill_hits,
        "satisfying_cue_count": satisfying_hits,
    }


def telegram_comedy_exploration_candidate(item):
    """Fresh, views-only native Telegram comedy lead; not proof of quality."""
    if str(item.get("source") or "") != "telegram_native_video":
        return False
    try:
        views = int(item.get("views") or 0)
        age_h = float(item.get("age_hours") or 999999.0)
    except (TypeError, ValueError):
        return False
    if views < MIN_COMEDY_EXPLORATION_VIEWS or age_h > 24.0:
        return False
    blob = " ".join([str(item.get("title") or ""), str(item.get("description") or "")]).lower()
    return bool(re.search(
        r"(😂|🤣|😅|😆|خنده.?دار|طنز|شوخی|سوتی|بامزه|مستر.?بین|میم|خوش.?شانس|بدشانس|شانس.?ترین|اوضاع.{0,12}خرابه)",
        blob, re.I,
    ))


def telegram_tech_exploration_candidate(item):
    """A narrow exploratory lane for native short demos about AI/technology."""
    if str(item.get("source") or "") != "telegram_native_video":
        return False
    try:
        views = int(item.get("views") or 0)
        age_h = float(item.get("age_hours") or 999999.0)
    except (TypeError, ValueError):
        return False
    if views < MIN_TECH_EXPLORATION_VIEWS or age_h > MAX_TECH_EXPLORATION_AGE_HOURS:
        return False
    blob = " ".join([
        str(item.get("title") or ""),
        str(item.get("description") or ""),
    ]).lower()
    has_tech = bool(re.search(
        r"(هوش.?مصنوعی|\bAI\b|chatgpt|gemini|sora|veo|ربات|گجت|تکنولوژی|فناوری|claude|runway|heygen)",
        blob, re.I,
    ))
    has_demo = bool(re.search(
        r"(ساخت|تبدیل|تولید|تصویر|ویدیو|ویدئو|صدا|دوبله|واقعی|انیمیشن|"
        r"مقایسه|تست|آزمایش|باورنکردنی|عجیب|قبل.{0,10}بعد|build|turns?.{0,12}into|"
        r"generated|ai video|robot runs|robot walks)",
        blob, re.I,
    ))
    return has_tech and has_demo


def public_demand_score(item, corroboration=1):
    """Demand-first score. Duration/topic/visual aesthetics do not influence rank."""
    import math
    views = max(0, int(item.get("views") or 0))
    likes = max(0, int(item.get("likes") or 0))
    comments = max(0, int(item.get("comments") or 0))
    shares = max(0, int(item.get("shares") or 0))
    age_h = max(0.25, float(item.get("age_hours") or 999999.0))
    source_tag = str(item.get("source") or "")
    telegram_comedy_exploration = telegram_comedy_exploration_candidate(item)
    telegram_tech_exploration = telegram_tech_exploration_candidate(item)
    telegram_native_video_evidence = (
        source_tag == "telegram_native_video"
        and age_h <= 36.0
        and (views >= 30_000 or telegram_comedy_exploration or telegram_tech_exploration)
    )
    telegram_repost_views = max(0, int(item.get("telegram_repost_views") or 0))
    raw_repost_channels = item.get("telegram_repost_channels") or []
    if isinstance(raw_repost_channels, str):
        raw_repost_channels = [raw_repost_channels]
    telegram_repost_channels = len(set(str(x) for x in raw_repost_channels if str(x).strip()))

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

    # Telegram repost reach is a separate discovery signal, not Instagram views.
    # It adds a small bonus and can corroborate a post only when multiple public
    # channels relay it and at least one native platform metric is also available.
    telegram_repost_score = (
        min(3.0, 0.75 * math.log10(max(1, telegram_repost_views)))
        + min(4.0, 2.0 * math.log10(max(1, telegram_repost_channels)))
    )
    total = min(
        100.0,
        volume + velocity_score + like_volume + share_volume +
        comment_volume + engagement_quality + cross_score + freshness
        + telegram_repost_score
    )

    metric_fields = sum(1 for v in (views, likes, comments, shares) if v > 0)
    strong_absolute = (
        views >= 10_000 or likes >= 1_000 or shares >= 300 or comments >= 500
    )
    native_evidence = metric_fields >= 2 and (strong_absolute or views >= 2_000)
    relay_evidence = metric_fields >= 1 and telegram_repost_views >= 10_000 and telegram_repost_channels >= 2
    evidence = native_evidence or relay_evidence or telegram_native_video_evidence
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
        "telegram_repost_views": telegram_repost_views,
        "telegram_repost_channels": telegram_repost_channels,
        "telegram_repost_score": round(telegram_repost_score, 2),
        "relay_evidence": relay_evidence,
        "telegram_native_video_evidence": telegram_native_video_evidence,
        "telegram_comedy_exploration": telegram_comedy_exploration,
        "telegram_tech_exploration": telegram_tech_exploration,
        "score": round(total, 2),
    }

def article_candidates(item, timeout=12):
    host = urlparse(item["link"]).netloc.lower()
    # Social/video platform URLs can be handed directly to yt-dlp.
    if any(x in host for x in [
        "youtube.com","youtu.be","tiktok.com","instagram.com",
        "x.com","twitter.com","aparat.com","t.me","telegram.me"
    ]):
        return [item["link"]]
    try:
        r = session.get(item["link"], timeout=timeout, allow_redirects=True)
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
            print("COBALT_HTTP_FAIL", r.status_code, re.sub(r"\\s+", " ", r.text[:800]))
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
        f'"{title[:140]}"',
        f'"{compact}" ویدئو' if compact else "",
    ]
    routes = []
    seen = set()

    for q in [x for x in queries if x]:
        try:
            for item in rss_items(q, timeout=8):
                link = str(item.get("link") or "").strip()
                if not link or platform_of(link) != "web":
                    continue

                page_title = str(item.get("title") or "")
                page_text = f"{page_title} {q}"
                # Require at least one distinctive Persian title token on the
                # mirror page. This is discovery matching, not demand ranking.
                if tokens and not any(t in page_text for t in tokens[:3]):
                    try:
                        rr = session.get(link, timeout=6, allow_redirects=True)
                        if rr.ok:
                            probe = rr.text[:1_500_000]
                            if not any(t in probe for t in tokens[:3]):
                                continue
                    except Exception:
                        continue

                for route in article_candidates(item, timeout=8):
                    if route not in seen:
                        seen.add(route)
                        routes.append(route)
                        if len(routes) >= limit:
                            return routes
        except Exception as exc:
            print("WEB_MIRROR_SEARCH_FAIL", type(exc).__name__)

    return routes

def download_youtube_via_ejs(url):
    """Try a TV/web-Safari player mix before slower mirror fallbacks."""
    global YOUTUBE_BOTWALL_DETECTED
    outdir = tempfile.mkdtemp(prefix="ytdlp-pot-")
    try:
        p = subprocess.run(
            [
                "yt-dlp", "--no-playlist", "--no-warnings",
                "--js-runtimes", "deno",
                "--extractor-args", f"youtubepot-bgutilhttp:base_url={BGUTIL_POT_URL}",
                "--extractor-args", "youtube:player_client=tv,web_safari",
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
        error_text = str(p.stderr or "")
        if re.search(r"sign in to confirm|login_required|confirm you.?re not a bot", error_text, re.I):
            YOUTUBE_BOTWALL_DETECTED = True
            print("YOUTUBE_BOTWALL_DETECTED", youtube_video_id(url) or "")
        print("YOUTUBE_POT_FAIL", error_text[-900:])
    except Exception as exc:
        print("YOUTUBE_POT_ERROR", type(exc).__name__)
    return None

def local_download(url):
    global YOUTUBE_COBALT_FALLBACK_ATTEMPTED
    if M3U8.search(url):
        return None

    host = urlparse(url).netloc.lower()
    social = any(x in host for x in [
        "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
        "x.com", "twitter.com", "aparat.com", "t.me", "telegram.me"
    ])

    if social:
        is_youtube = "youtube.com" in host or "youtu.be" in host

        # Use one current yt-dlp client strategy; repeated identical anti-bot
        # failures are not worth several more minutes per candidate.
        if is_youtube and not YOUTUBE_BOTWALL_DETECTED:
            ejs_path = download_youtube_via_ejs(url)
            if ejs_path:
                return ejs_path
        if is_youtube and YOUTUBE_BOTWALL_DETECTED:
            # yt-dlp has hit YouTube's logged-out bot wall. Make one bounded
            # attempt through the locally hosted Cobalt instance, which has its
            # own yt-session-generator token source; don't repeat this fallback
            # for every subsequent blocked YouTube candidate in the same scan.
            if not YOUTUBE_COBALT_FALLBACK_ATTEMPTED:
                YOUTUBE_COBALT_FALLBACK_ATTEMPTED = True
                cobalt_path = download_via_cobalt(url)
                if cobalt_path:
                    print("YOUTUBE_COBALT_FALLBACK_ACQUIRED", youtube_video_id(url) or "")
                    return cobalt_path
                print("YOUTUBE_COBALT_FALLBACK_FAILED", youtube_video_id(url) or "")
            else:
                print("YOUTUBE_BOTWALL_SKIP_REPEAT", youtube_video_id(url) or "")
            return None

        # Do not send Telegram URLs to Cobalt: it rejects those links.
        # Use Cobalt only for the social platforms it can actually resolve.
        if platform_of(url) in {"tiktok", "instagram", "x", "aparat"}:
            cobalt_path = download_via_cobalt(url)
            if cobalt_path:
                return cobalt_path

        if platform_of(url) == "instagram":
            # parth-dl uses multiple current logged-out Instagram paths and is
            # currently a better CI fallback than scraper mirrors.
            parth_path = download_instagram_via_parth_dl(url)
            if parth_path:
                return parth_path

            # Final public-mirror fallback: useful when Imginn is reachable.
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
        # Fail closed: without the real visual model, there is no valid
        # attraction result and this candidate must not be published.
        return 0.0, {
            "model_version": ATTRACTION_MODEL_VERSION,
            "available": False,
            "reason": "visual_dependencies_unavailable",
            "import_error": VISUAL_IMPORT_ERROR,
            "retention_proxy_score": 0.0,
            "retention_proxy_is_actual_watch_time": False,
            "passed": False,
        }

    try:
        visual = visual_score(path) or {}
    except Exception as exc:
        print("VISUAL_SCORE_FAIL", type(exc).__name__)
        return 45.0, {
            "model_version": ATTRACTION_MODEL_VERSION,
            "available": False,
            "reason": type(exc).__name__,
            "retention_proxy_score": 0.0,
            "retention_proxy_is_actual_watch_time": False,
            "passed": False,
        }

    overall = float(visual.get("score") or 0.0)
    first_event = float(visual.get("hook_first_event_score") or 0.0)
    event = float(visual.get("hook_event_score") or 0.0)
    structure = float(visual.get("hook_structure_score") or 0.0)
    novelty = float(visual.get("visual_novelty_score") or 0.0)
    retention_proxy = float(visual.get("retention_proxy_score") or (
        0.30 * first_event + 0.30 * event + 0.20 * structure + 0.20 * novelty
    ))

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
        "retention_proxy_score": round(min(max(retention_proxy, 0.0), 99.0), 2),
        "retention_proxy_is_actual_watch_time": False,
        "sustained_activity_score": round(float(visual.get("sustained_activity_score") or 0.0), 2),
        "motion_coverage": float(visual.get("motion_coverage") or 0.0),
        "novelty_coverage": float(visual.get("novelty_coverage") or 0.0),
        "static_penalty": static_penalty,
        "duration_seconds": round(float(mi.get("duration") or 0.0), 2),
        "passed": passed,
    }
    return round(attraction, 2), metrics

def single_payoff_gate(item, mi, cq, demand, attraction):
    """Prefer a short, coherent clip with one memorable hook/payoff.

    This is a conservative format heuristic; it does not claim to understand
    the full story. Roundup-title and long-duration exceptions require strong
    measured demand plus strong first/event/structure metrics.
    """
    title = str(item.get("title") or "")
    duration = max(0.0, float(mi.get("duration") or 0.0))
    first_event = float(cq.get("hook_first_event_score") or 0.0)
    event = float(cq.get("hook_event_score") or 0.0)
    structure = float(cq.get("hook_structure_score") or 0.0)
    is_roundup = bool(COMPILATION_TITLE_RE.search(title))
    exceptional = (
        float(demand) >= 88.0
        and float(attraction) >= 77.0
        and first_event >= 68.0
        and event >= 70.0
        and structure >= 58.0
    )
    reasons = []
    if is_roundup and not exceptional:
        reasons.append("multi_story_or_roundup_title_without_exceptional_hook")
    if duration > MAX_ROUTINE_SHORTFORM_SECONDS and not exceptional:
        reasons.append("longer_than_shareable_short_form")
    if duration > 55.0 and structure < 35.0 and not exceptional:
        reasons.append("weak_narrative_structure_for_clip_length")
    return {
        "applied": True,
        "passed": not reasons,
        "reasons": reasons,
        "roundup_or_multi_story_title": is_roundup,
        "duration_seconds": round(duration, 2),
        "maximum_routine_duration_seconds": MAX_ROUTINE_SHORTFORM_SECONDS,
        "exceptional_hook_exception": exceptional,
        "first_event_score": round(first_event, 2),
        "event_score": round(event, 2),
        "structure_score": round(structure, 2),
    }


def clean_display_title(title):
    """Remove repost handles/CTA clutter from the delivered caption, not from scoring."""
    value = str(title or "")
    value = re.sub(r"Join\s*➪\s*(?:🆔\s*)?@[A-Za-z0-9_]+", "", value, flags=re.I)
    value = re.sub(r"🆔\s*@[A-Za-z0-9_]+", "", value)
    value = re.sub(r"@[A-Za-z0-9_]{3,}", "", value)
    value = value.replace("💯", "")
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s+([،,:؛.!؟])", r"\1", value)
    value = re.sub(r"([،,:؛])\s*([،,:؛])+", r"\1", value)
    return value.strip(" \t\n-|•")


def send_video(path, title, source):
    display_title = clean_display_title(title)[:700]
    cap = ("🎬 ویدئوی منتخب امروز\n\n" + display_title + "\n\n" +
           "منبع: " + source[:120]).strip()
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
    raise SystemExit(
        "LEGACY_VIDEO_HUNTER_DISABLED: use the supported discovery and native-transfer "
        "workflows; this legacy runner is disabled and must not work around source access controls."
    )


if __name__ == "__main__":
    sys.exit(main())
