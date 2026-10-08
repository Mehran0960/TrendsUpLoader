#!/usr/bin/env python3
"""Iran viral video hunter: discover fresh Persian web videos, rank, dedupe, send native MP4 to Telegram."""
import hashlib, html, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import requests
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "attention_state" / "iran_video_hunter_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

SOURCE_DOMAINS = [
    "hamshahrionline.ir","khabaronline.ir","mehrnews.com","isna.ir","irna.ir",
    "farsnews.ir","tabnak.ir","asriran.com","yjc.ir","bartarinha.ir",
    "varzesh3.com","khabarvarzeshi.com","entekhab.ir"
]
QUERY_TERMS = [
    "ویدئو","ویدیو","فیلم","لحظه","جنجالی","پربازدید","عجیب","باورنکردنی",
    "واکنش","مردم","فوتبال","سلبریتی","ایران"
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

def article_candidates(item):
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

def score(item, duration, cross_count):
    title = item["title"]
    cues = sum(1 for w in ["جنجالی","باورنکردنی","عجیب","لحظه","پربازدید","واکنش","فوری","افشا","غافلگیر","وایرال"] if w in title)
    freshness = max(0.0, 1.0 - item["age_hours"] / 48.0)
    source_boost = 1 if any(d in item["link"] for d in SOURCE_DOMAINS) else 0
    duration_score = 1.0 if 4 <= duration <= 60 else (0.5 if duration <= 120 else 0.0)
    return round(45*freshness + 12*min(cues,4) + 8*min(cross_count,4) + 5*source_boost + 10*duration_score, 2)

def main():
    if not TOKEN or not TARGET:
        raise RuntimeError("Telegram secrets missing")
    state = load()
    seen_keys = set(state.get("seen_keys", []))
    seen_hashes = set(state.get("seen_hashes", []))

    queries = [f"site:{d} ({term})" for d in SOURCE_DOMAINS for term in QUERY_TERMS[:4]]
    items = {}
    for q in queries:
        try:
            for x in rss_items(q):
                key_text = re.sub(r"\s+", " ", x["title"].lower()) + "|" + x["link"].split("?")[0]
                x["key"] = hashlib.sha256(key_text.encode()).hexdigest()
                items[x["key"]] = x
        except Exception as e:
            print("RSS_FAIL", q, type(e).__name__)

    groups = {}
    for x in items.values():
        norm = re.sub(r"[^\w\u0600-\u06ff ]", " ", x["title"].lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        stem = " ".join(norm.split()[:10])
        groups.setdefault(stem, []).append(x)

    ranked = []
    for x in items.values():
        if x["key"] in seen_keys or x["age_hours"] > 72:
            continue
        vids = article_candidates(x)
        norm = re.sub(r"[^\w\u0600-\u06ff ]", " ", x["title"].lower())
        stem = " ".join(re.sub(r"\s+", " ", norm).strip().split()[:10])
        for u in vids:
            ranked.append((x, u, len(groups.get(stem, []))))
    ranked.sort(key=lambda z: (-score(z[0], 20.0, z[2]), z[0]["age_hours"]))

    print("DISCOVERED_ARTICLES", len(items), "VIDEO_CANDIDATES", len(ranked))
    chosen = None
    for item, url, cross in ranked:
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
            sc = score(item, mi["duration"], cross)
            if sc < 55:
                os.unlink(path)
                continue
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
        "score": sc, "cross_sources": cross, "duration": mi["duration"],
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
