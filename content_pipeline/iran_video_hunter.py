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

def public_demand_score(item, corroboration=1):
    """Rank by measurable public demand; never by topic, title cues, or duration."""
    views = max(0, int(item.get("views") or 0))
    likes = max(0, int(item.get("likes") or 0))
    comments = max(0, int(item.get("comments") or 0))
    age_h = max(0.25, float(item.get("age_hours") or 999999.0))
    if views <= 0:
        return 0.0, {"evidence": False}
    velocity = views/age_h
    like_rate = likes/max(1, views)
    comment_rate = comments/max(1, views)
    volume = min(30.0, 5.0*__import__("math").log10(views+1))
    velocity_score = min(35.0, 6.0*__import__("math").log10(velocity+1))
    engagement = min(20.0, 400.0*like_rate) + min(10.0, 500.0*comment_rate)
    corroboration_score = min(5.0, 1.5*max(0, corroboration-1))
    freshness = 5.0*max(0.0, 1.0-min(age_h/72.0, 1.0))
    total = min(100.0, volume + velocity_score + engagement + corroboration_score + freshness)
    return round(total, 2), {
        "evidence": True, "views": views, "likes": likes, "comments": comments,
        "velocity_views_per_hour": round(velocity, 2),
        "like_rate": round(like_rate, 6), "comment_rate": round(comment_rate, 6),
        "age_hours": round(age_h, 2),
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

def local_download(url):
    if M3U8.search(url):
        return None
    host = urlparse(url).netloc.lower()
    social = any(x in host for x in [
        "youtube.com","youtu.be","tiktok.com","instagram.com",
        "x.com","twitter.com","aparat.com"
    ])
    if social:
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
        except Exception as e:
            print("YTDLP_ERROR", host, type(e).__name__)
            return None

    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        with session.get(url, stream=True, timeout=35, allow_redirects=True) as r:
            r.raise_for_status()
            if "text/html" in (r.headers.get("content-type") or "").lower():
                return None
            total = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(1024*128):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 52 * 1024 * 1024:
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
    """Reject clips that are merely short; require a real visual event/hook."""
    if visual_score is None or source_hook_score is None:
        # Fail open only for the discovery engine; direct tests remain possible.
        return 0.0, {}

    try:
        visual = visual_score(path)
    except Exception as exc:
        print("VISUAL_SCORE_FAIL", type(exc).__name__)
        return 0.0, {}

    src = {
        "title": item.get("title", ""),
        "description": item.get("title", ""),
        "category": "human_funny",
        "provider": "social_or_web",
        "attention_score": 78.0,
    }
    try:
        hook = float(source_hook_score(src, visual) or 0.0)
    except Exception:
        hook = 0.0

    first_event = float(visual.get("hook_first_event_score") or 0.0)
    event = float(visual.get("hook_event_score") or 0.0)
    structure = float(visual.get("hook_structure_score") or 0.0)
    novelty = float(visual.get("visual_novelty_score") or 0.0)
    visual_total = float(visual.get("score") or 0.0)

    # The key anti-boring gate: a clip needs an actual early event or a
    # clearly developing/payoff structure. Static close-ups and generic pet
    # footage usually fail here even when they are short and visually clean.
    passed = (
        hook >= 58.0
        and visual_total >= 52.0
        and (
            (first_event >= 48.0 and event >= 50.0)
            or (structure >= 58.0 and novelty >= 32.0)
        )
    )

    quality = round(
        0.30*hook
        + 0.25*event
        + 0.20*first_event
        + 0.15*structure
        + 0.10*novelty,
        2,
    )
    metrics = {
        "hook_score": round(hook,2),
        "visual_score": round(visual_total,2),
        "hook_event_score": round(event,2),
        "hook_first_event_score": round(first_event,2),
        "hook_structure_score": round(structure,2),
        "visual_novelty_score": round(novelty,2),
        "content_quality_score": quality,
        "passed": passed,
    }
    return quality, metrics

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
        'site:youtube.com/shorts ("خنده دار" OR "بامزه" OR "عجیب" OR "واکنش") ایران',
        'site:instagram.com/reel ("خنده دار" OR "بامزه" OR "عجیب" OR "واکنش") ایران',
        'site:tiktok.com ("خنده دار" OR "بامزه" OR "عجیب" OR "واکنش") ایران',
        'site:aparat.com/v ("خنده دار" OR "بامزه" OR "عجیب" OR "واکنش")',
        'site:x.com ("ویدئو" OR "ویدیو") ("خنده دار" OR "عجیب" OR "واکنش") ایران',
    ]
    queries = [f"site:{d} ({term})" for d in SOURCE_DOMAINS for term in QUERY_TERMS[:8]]
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

    enrich_youtube_metrics(items)

    # Demand-first: only candidates with measurable public engagement enter
    # the acquisition queue. Topic, title keywords, and duration are ignored.
    ranked = []
    for x in items.values():
        if x["key"] in seen_keys:
            continue
        vids = article_candidates(x)
        norm = re.sub(r"[^\w\u0600-\u06ff ]", " ", x["title"].lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        stem = " ".join(norm.split()[:10])

        corroboration = 1
        for y in items.values():
            yn = re.sub(r"[^\w\u0600-\u06ff ]", " ", y["title"].lower())
            yn = re.sub(r"\s+", " ", yn).strip()
            if yn and stem and yn[:80] == stem[:80]:
                corroboration += 1

        for u in vids:
            demand, dm = public_demand_score(x, corroboration)
            if not dm.get("evidence"):
                continue
            ranked.append((x, u, corroboration, demand, dm))

    ranked.sort(
        key=lambda z: (
            -z[3],
            -int(z[0].get("views") or 0),
            -float(z[0].get("likes") or 0),
            float(z[0].get("age_hours") or 999999),
        )
    )

    print("DISCOVERED_ARTICLES", len(items), "MEASURABLE_VIDEO_CANDIDATES", len(ranked))
    chosen = None
    for item, url, cross, demand, dm in ranked:
        path = local_download(url)
        if not path:
            continue
        try:
            h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if h in seen_hashes or item["key"] in seen_keys:
                os.unlink(path)
                continue
            mi = media_info(path)
            if not mi or mi["duration"] < 2 or mi["duration"] > 600 or mi["width"] < 240 or mi["height"] < 240:
                os.unlink(path)
                continue
            if Path(path).stat().st_size > 50 * 1024 * 1024:
                os.unlink(path)
                continue
            sc = demand
            # Visual/hook analysis is diagnostic only. It never overrides
            # measured public demand and therefore cannot discard a highly
            # watched video merely because our subjective model dislikes it.
            content_quality, cq = content_quality_gate(path, item, mi)
            print("DEMAND_METRICS", json.dumps(dm, ensure_ascii=False))
            print("CONTENT_DIAGNOSTIC", item["title"], cq)
            item["demand_metrics"] = dm
            item["content_quality_score"] = content_quality
            item["content_quality_metrics"] = cq
            chosen = (item, url, path, h, mi, sc, cross)
            break

        except Exception:
            try: os.unlink(path)
            except Exception: pass

    if not chosen:
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        state["last_result"] = "no_new_qualified_video"
        save(state)
        print("NO_NEW_QUALIFIED_VIDEO")
        return 0

    item, url, path, h, mi, sc, cross = chosen
    sent = send_video(path, item["title"], item["source"] or urlparse(item["link"]).netloc)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "title": item["title"], "article": item["link"], "video": url,
        "sha256": h, "telegram_message_id": sent.get("message_id"),
        "score": sc, "demand_score": sc, "demand_metrics": item.get("demand_metrics", {}), "content_quality_score": item.get("content_quality_score"),
        "content_quality_metrics": item.get("content_quality_metrics", {}),
        "cross_sources": cross, "duration": mi["duration"],
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
