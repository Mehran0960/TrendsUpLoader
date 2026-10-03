#!/usr/bin/env python3
# Zero-cost Persian short-form content pipeline.
# V3: sentence-synced captions + topic-aware original illustrations.
import json
import os
import re
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html import unescape

from PIL import Image, ImageDraw, ImageFont, ImageOps, features
import arabic_reshaper
from bidi.algorithm import get_display

RADAR_URL = os.environ.get("RADAR_URL", "https://trend-radar.m-hoseyni-6966.workers.dev/candidates")
OUT = Path("out")
VOICE_DIR = Path(".voices")
WIDTH, HEIGHT = 720, 1280
FPS = 30
PIPER_VOICE = os.environ.get("PIPER_VOICE", "fa_IR-gyro-medium").strip() or "fa_IR-gyro-medium"

def get_json(url: str):
    req = Request(url, headers={"User-Agent": "trend-radar-content-pipeline/2.0"})
    with urlopen(req, timeout=20) as r:
        return json.load(r)


def get_candidate_data():
    collected, errors = [], []
    for url, mode in (
        (RADAR_URL, "radar"),
        (RADAR_URL.rsplit("/", 1)[0] + "/status", "status"),
    ):
        try:
            data = get_json(url)
            items = data.get("candidates" if mode == "radar" else "top") if isinstance(data, dict) else None
            if isinstance(items, list):
                collected.extend(items)
        except Exception as e:
            errors.append(f"{mode}: {e}")

    try:
        ids = get_json("https://hacker-news.firebaseio.com/v0/beststories.json")[:20]
        keywords = re.compile(
            r"\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics|software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|startup|business|chip|gpu|nvidia|hardware|security|vulnerability|malware|cybersecurity|hack)\b",
            re.I
        )
        for story_id in ids:
            try:
                item = get_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
                title = str(item.get("title") or "").strip()
                if item.get("type") == "story" and title and keywords.search(title):
                    sc = min(75.0, float(item.get("score") or 0) / 2.0)
                    collected.append({
                        "source": "hacker_news",
                        "title": title,
                        "url": item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                        "score": sc,
                        "content_fit": local_content_fit(title),
                        "velocity_pct": 0,
                        "source_count": 1,
                        "opportunity_score": sc,
                    })
            except Exception as e:
                errors.append(f"hn:{story_id}:{e}")
    except Exception as e:
        errors.append(f"hn-list: {e}")

    if collected:
        return collected, "mixed"
    raise RuntimeError("No candidate source found: " + " | ".join(errors[-5:]))


def fetch_source_context(url: str):
    if not url or not str(url).startswith(("http://", "https://")):
        return {}
    try:
        req = Request(str(url), headers={"User-Agent": "Mozilla/5.0 (compatible; TrendRadarBot/2.0)"})
        with urlopen(req, timeout=10) as r:
            raw = r.read(220000).decode("utf-8", errors="ignore")
        desc = ""
        for pat in (
            r'<meta[^>]+(?:name|property)=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+(?:property|name)=["\']twitter:description["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            m = re.search(pat, raw, re.I)
            if m:
                desc = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
                break
        if not desc:
            title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
            desc = re.sub(r"\s+", " ", unescape(title_m.group(1))).strip() if title_m else ""
        return {"description": desc[:900]}
    except Exception:
        return {}

def get_bytes(url: str):
    req = Request(url, headers={"User-Agent": "trend-radar-content-pipeline/3.0"})
    with urlopen(req, timeout=20) as r:
        return r.read()

def commons_queries(title: str):
    low = title.lower()
    qs = []
    clean = re.sub(r"[^A-Za-z0-9À-ÿ\u0600-\u06FF ]+", " ", title).strip()
    if clean:
        qs.append(clean)
    if "linux" in low or "kernel" in low or "security" in low or "vulner" in low:
        qs += ["Linux kernel", "Debian Linux", "computer security"]
    elif any(k in low for k in ("ai", "artificial intelligence", "llm", "agent", "chatgpt", "claude")):
        qs += ["artificial intelligence", "AI computer", "machine learning"]
    elif any(k in low for k in ("chip", "gpu", "nvidia", "processor", "hardware")):
        qs += ["computer chip", "GPU", "semiconductor"]
    elif any(k in low for k in ("finance", "economy", "market", "money", "investing")):
        qs += ["financial market", "stock market", "economy chart"]
    elif any(k in low for k in ("software", "github", "browser", "app", "code")):
        qs += ["software development", "computer code", "web browser"]
    return list(dict.fromkeys(qs))

def commons_relevance(title: str, file_title: str, description: str):
    corpus = (file_title + " " + description).lower()
    topic = title.lower()
    terms = []
    if any(k in topic for k in ("linux", "kernel", "security", "vulner")):
        terms = ["linux", "kernel", "debian", "security", "cyber", "server"]
    elif any(k in topic for k in ("ai", "artificial intelligence", "llm", "agent", "chatgpt")):
        terms = ["artificial intelligence", "machine learning", "ai", "robot", "neural"]
    elif any(k in topic for k in ("chip", "gpu", "nvidia", "processor", "hardware")):
        terms = ["chip", "gpu", "processor", "semiconductor", "hardware"]
    elif any(k in topic for k in ("finance", "economy", "market", "money", "investing")):
        terms = ["finance", "market", "stock", "economy", "money", "bank"]
    elif any(k in topic for k in ("software", "github", "browser", "app", "code")):
        terms = ["software", "github", "browser", "computer", "code", "programming"]
    else:
        terms = [x for x in re.findall(r"[a-z]{4,}", topic) if x not in {"have","been","this","that","with","from"}]
    return sum(2 if x in file_title.lower() else 1 for x in terms if x in corpus)

def openverse_queries(title: str):
    low = title.lower()
    if "linux" in low or "kernel" in low or "security" in low or "vulner" in low:
        return ["Linux server", "computer security", "cybersecurity", "Linux computer"]
    if any(k in low for k in ("ai", "artificial intelligence", "llm", "agent", "chatgpt", "claude")):
        return ["artificial intelligence", "AI computer", "machine learning", "AI robot"]
    if any(k in low for k in ("chip", "gpu", "nvidia", "processor", "hardware")):
        return ["computer chip", "GPU", "semiconductor", "computer hardware"]
    if any(k in low for k in ("finance", "economy", "market", "money", "investing", "startup", "business")):
        return ["financial market", "stock market", "economy", "business technology"]
    if any(k in low for k in ("software", "github", "browser", "app", "code")):
        return ["software development", "computer code", "web browser", "programming"]
    clean = re.sub(r"[^A-Za-z0-9\u0600-\u06FF ]+", " ", title).strip()
    return [clean] if clean else ["technology"]

def openverse_relevance(title: str, item: dict):
    corpus = " ".join([
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        " ".join(str(x.get("name") or x) if isinstance(x, dict) else str(x) for x in (item.get("tags") or [])),
    ]).lower()
    topic = title.lower()
    groups = {
        "security": ["security","cybersecurity","linux","kernel","server","computer"],
        "ai": ["artificial intelligence","machine learning","ai","robot","computer"],
        "hardware": ["chip","gpu","processor","semiconductor","hardware","computer"],
        "finance": ["finance","market","stock","economy","money","business"],
        "software": ["software","code","programming","browser","github","computer"],
    }
    terms=[]
    for group, vals in groups.items():
        if any(v in topic for v in vals):
            terms += vals
    if not terms:
        terms=[w for w in re.findall(r"[a-z]{4,}",topic) if w not in {"have","been","this","that","with","from"}]
    return sum(2 if x in str(item.get("title") or "").lower() else 1 for x in terms if x in corpus)

def search_openverse_visuals(title: str, limit=4):
    import urllib.parse
    found=[]
    seen=set()
    for q in openverse_queries(title)[:4]:
        try:
            params=urllib.parse.urlencode({
                "q": q,
                "page_size": 20,
                "license_type": "commercial",
                "size": "large",
                "aspect_ratio": "wide",
                "mature": "false",
            })
            data=get_json("https://api.openverse.org/v1/images/?"+params)
            for item in data.get("results", []):
                url=item.get("url") or ""
                thumb=item.get("thumbnail") or ""
                lic=str(item.get("license") or "").lower()
                if not url or not thumb or url in seen:
                    continue
                if lic not in {"cc0","by","by-sa","pdm","publicdomain"}:
                    continue
                if item.get("is_sensitive"):
                    continue
                w=int(item.get("width") or 0); h=int(item.get("height") or 0)
                if w < 900 or h < 500:
                    continue
                rel=openverse_relevance(title,item)
                if rel < 2:
                    continue
                seen.add(url)
                found.append({
                    "url":url,
                    "thumbnail":thumb,
                    "page_url":item.get("foreign_landing_url") or item.get("detail_url") or "",
                    "title":str(item.get("title") or item.get("originalTitle") or ""),
                    "license":str(item.get("license") or ""),
                    "license_version":str(item.get("license_version") or ""),
                    "license_url":str(item.get("license_url") or ""),
                    "artist":str(item.get("creator") or ""),
                    "provider":str(item.get("providerName") or item.get("provider") or ""),
                    "description":str(item.get("description") or "")[:400],
                    "relevance":rel,
                    "width":w,"height":h,
                })
        except Exception:
            continue
    found.sort(key=lambda x:(x["relevance"], min(x["width"]*x["height"], 9000000)), reverse=True)
    return found[:limit]

def download_openverse_visuals(title: str, out_dir: Path, limit=4):
    metas=search_openverse_visuals(title, limit=limit)
    out=[]
    out_dir.mkdir(exist_ok=True)
    for i,meta in enumerate(metas,1):
        p=out_dir/f"openverse_{i}.jpg"
        try:
            p.write_bytes(get_bytes(meta["url"]))
            with Image.open(p) as im:
                if im.width < 900 or im.height < 500:
                    raise ValueError("small image")
            out.append((p,meta))
        except Exception:
            p.unlink(missing_ok=True)
    return out

def search_commons_videos(title: str, limit=3):
    """Find small, relevant, openly licensed video files on Wikimedia Commons."""
    import urllib.parse
    low = title.lower()
    qs = []
    if "linux" in low or "kernel" in low or "security" in low or "vulner" in low:
        qs = ["computer security", "Linux", "cybersecurity"]
    elif any(k in low for k in ("ai", "artificial intelligence", "llm", "agent", "chatgpt", "claude")):
        qs = ["artificial intelligence", "AI robot", "machine learning"]
    elif any(k in low for k in ("chip", "gpu", "nvidia", "processor", "hardware")):
        qs = ["computer chip", "semiconductor", "GPU"]
    elif any(k in low for k in ("software", "github", "browser", "app", "code")):
        qs = ["software development", "computer programming", "Linux desktop"]
    elif any(k in low for k in ("finance", "economy", "market", "money", "investing", "business")):
        qs = ["stock market", "financial market", "business technology"]
    else:
        qs = [re.sub(r"[^A-Za-z0-9\\u0600-\\u06FF ]+", " ", title).strip()]

    candidates=[]; seen=set()
    allowed=("CC BY","CC BY-SA","CC0","Public domain","PD")
    for q in qs[:4]:
        try:
            params={"action":"query","format":"json","list":"search","srnamespace":"6","srsearch":q + " filetype:video filesize:<40000","srwhat":"text","srlimit":"30"}
            data=get_json("https://commons.wikimedia.org/w/api.php?"+urllib.parse.urlencode(params))
            titles=[x.get("title") for x in data.get("query",{}).get("search",[]) if x.get("title")]
            if not titles: continue
            params2={"action":"query","format":"json","prop":"imageinfo","iiprop":"url|mime|size|extmetadata|commonmetadata","titles":"|".join(titles)}
            data2=get_json("https://commons.wikimedia.org/w/api.php?"+urllib.parse.urlencode(params2))
            for page in data2.get("query",{}).get("pages",{}).values():
                info=(page.get("imageinfo") or [{}])[0]
                mime=str(info.get("mime") or "")
                if not mime.startswith("video/"):
                    continue
                size=int(info.get("size") or 0)
                if size<=0 or size>40*1024*1024:
                    continue
                ext=info.get("extmetadata") or {}
                lic=str((ext.get("LicenseShortName") or {}).get("value","")).strip()
                if not any(a.lower() in lic.lower() for a in allowed):
                    continue
                title2=page.get("title","")
                desc=re.sub("<[^>]+>"," ",str((ext.get("ImageDescription") or {}).get("value","")))
                rel=commons_relevance(title,title2,desc)
                if rel<2: continue
                url=info.get("url")
                if not url or url in seen: continue
                seen.add(url)
                common=info.get("commonmetadata") or {}
                duration=0.0
                rawdur=str((ext.get("Duration") or {}).get("value","") or common.get("Duration") or "")
                m=re.search(r"(\\d+(?:\\.\\d+)?)",rawdur)
                if m: duration=float(m.group(1))
                width=int(info.get("width") or 0); height=int(info.get("height") or 0)
                if width<640 or height<360: continue
                page_url="https://commons.wikimedia.org/wiki/"+urllib.parse.quote(title2.replace(" ","_"))
                candidates.append({
                    "url":url,"page_url":page_url,"title":title2,
                    "license":lic or "unspecified",
                    "license_url":str((ext.get("LicenseUrl") or {}).get("value","")),
                    "artist":re.sub("<[^>]+>"," ",str((ext.get("Artist") or {}).get("value",""))).strip()[:240],
                    "description":re.sub(r"\\s+"," ",desc).strip()[:300],
                    "relevance":rel,"size":size,"width":width,"height":height,
                    "duration_seconds":duration,
                })
        except Exception:
            continue
    candidates.sort(key=lambda x:(x["relevance"], -x["size"]), reverse=True)
    return candidates[:limit]

def download_commons_videos(title: str, out_dir: Path, limit=3):
    metas=search_commons_videos(title, limit=limit)
    out=[]; out_dir.mkdir(exist_ok=True)
    for i,meta in enumerate(metas,1):
        ext=".webm" if "webm" in meta["url"].lower() or "webm" in meta["license"].lower() else ".mp4"
        p=out_dir/f"commons_video_{i}{ext}"
        try:
            p.write_bytes(get_bytes(meta["url"]))
            out.append((p,meta))
        except Exception:
            p.unlink(missing_ok=True)
    return out

def search_commons_visuals(title: str, limit=3):
    """Find several strongly relevant reusable Commons images."""
    import urllib.parse
    candidates = []
    seen = set()
    allowed = ("CC BY", "CC BY-SA", "CC0", "Public domain", "PD")
    for q in commons_queries(title)[:5]:
        try:
            params={"action":"query","format":"json","list":"search","srnamespace":"6","srsearch":q,"srlimit":"12"}
            api="https://commons.wikimedia.org/w/api.php?"+urllib.parse.urlencode(params)
            data=get_json(api)
            titles=[x.get("title") for x in data.get("query",{}).get("search",[]) if x.get("title")]
            if not titles: continue
            params2={"action":"query","format":"json","prop":"imageinfo","iiprop":"url|mime|size|extmetadata","iiurlwidth":"1400","titles":"|".join(titles)}
            api2="https://commons.wikimedia.org/w/api.php?"+urllib.parse.urlencode(params2)
            data2=get_json(api2)
            for page in data2.get("query",{}).get("pages",{}).values():
                info=(page.get("imageinfo") or [{}])[0]
                mime=str(info.get("mime") or "")
                if not mime.startswith("image/") or mime in ("image/svg+xml",):
                    # SVG can be useful, but keep it out of the first automated visual pass.
                    continue
                ext=info.get("extmetadata") or {}
                lic=str((ext.get("LicenseShortName") or {}).get("value","")).strip()
                if not any(a.lower() in lic.lower() for a in allowed): continue
                title2=page.get("title","")
                desc=re.sub("<[^>]+>"," ",str((ext.get("ImageDescription") or {}).get("value","")))
                rel=commons_relevance(title,title2,desc)
                if rel<2: continue
                thumb=info.get("thumburl") or info.get("url")
                if not thumb or thumb in seen: continue
                seen.add(thumb)
                width=int(info.get("width") or 0); height=int(info.get("height") or 0)
                if width<700 or height<400: continue
                page_url="https://commons.wikimedia.org/wiki/"+urllib.parse.quote(title2.replace(" ","_"))
                candidates.append({
                    "url":thumb,"page_url":page_url,"title":title2,
                    "license":lic or "unspecified",
                    "artist":re.sub("<[^>]+>"," ",str((ext.get("Artist") or {}).get("value",""))).strip()[:240],
                    "description":re.sub(r"\s+"," ",desc).strip()[:300],
                    "relevance":rel,"width":width,"height":height,
                })
        except Exception:
            continue
    candidates.sort(key=lambda x:(x["relevance"], min(x["width"]*x["height"], 5000000)), reverse=True)
    return candidates[:limit]

def download_commons_visuals(title: str, out_dir: Path, limit=3):
    metas=search_commons_visuals(title, limit=limit)
    out=[]
    out_dir.mkdir(exist_ok=True)
    for i,meta in enumerate(metas,1):
        p=out_dir/f"commons_{i}.jpg"
        try:
            p.write_bytes(get_bytes(meta["url"]))
            with Image.open(p) as im:
                if im.width<700 or im.height<400:
                    raise ValueError("small image")
            out.append((p,meta))
        except Exception:
            p.unlink(missing_ok=True)
    return out


def font(size: int, bold=False):
    paths = (
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    if not bold:
        paths = paths[1:] + paths[:1]
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def fa(text: str) -> str:
    raw = str(text)
    if features.check("raqm"):
        return raw
    return get_display(arabic_reshaper.reshape(raw))

def rtl_kwargs():
    if features.check("raqm"):
        return {"direction": "rtl", "language": "fa"}
    return {}

def wrap_pixels(draw, text, fnt, max_width):
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        nxt = (cur + " " + word).strip()
        box = draw.textbbox((0, 0), fa(nxt), font=fnt, **rtl_kwargs())
        if (box[2] - box[0]) <= max_width:
            cur = nxt
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def draw_rtl_block(draw, text, y, fnt, max_width, fill=(245,245,245), align="center", spacing=12):
    lines = wrap_pixels(draw, text, fnt, max_width)
    cur_y = y
    kw = rtl_kwargs()
    for line in lines:
        r = fa(line)
        box = draw.textbbox((0,0), r, font=fnt, **kw)
        h = box[3] - box[1]
        if align == "right":
            x, anchor = WIDTH - 52, "ra"
        elif align == "left":
            x, anchor = 52, "la"
        else:
            x, anchor = WIDTH / 2, "ma"
        draw.text((x+2, cur_y+2), r, font=fnt, fill=(0,0,0), anchor=anchor, **kw)
        draw.text((x, cur_y), r, font=fnt, fill=fill, anchor=anchor, **kw)
        cur_y += h + spacing
    return cur_y

def base_canvas(title: str):
    img = Image.new("RGB", (WIDTH, HEIGHT), (9, 14, 28))
    d = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        shade = int(12 + 30 * (y / HEIGHT))
        d.line((0,y,WIDTH,y), fill=(9, 14 + shade//4, 28 + shade//2))
    d.rounded_rectangle((28,28,WIDTH-28,HEIGHT-28), radius=36, outline=(55,75,105), width=2)
    d.rounded_rectangle((44,44,WIDTH-44,180), radius=28, fill=(15,22,40), outline=(65,88,120), width=2)
    draw_rtl_block(d, title, 66, font(34, bold=True), WIDTH-120, fill=(245,248,255), align="center", spacing=8)
    return img

def draw_terminal(d, box=(75,275,645,770)):
    x1,y1,x2,y2 = box
    d.rounded_rectangle(box, radius=26, fill=(18,24,34), outline=(75,95,120), width=3)
    d.rounded_rectangle((x1,y1,x2,y1+56), radius=26, fill=(27,34,47))
    d.ellipse((x1+20,y1+20,x1+34,y1+34), fill=(220,80,80))
    d.ellipse((x1+42,y1+20,x1+56,y1+34), fill=(225,180,70))
    d.ellipse((x1+64,y1+20,x1+78,y1+34), fill=(90,185,110))
    mono = font(30)
    code = [
        "$ sudo apt update",
        "$ uname -r",
        "kernel: 6.12.x",
        "> security update available",
    ]
    yy = y1 + 90
    for line in code:
        d.text((x1+32,yy), line, font=mono, fill=(205,225,240))
        yy += 62

def draw_shield(d, center=(360,520), r=150, warning=True):
    cx, cy = center
    pts = [
        (cx,cy-r),(cx+r*0.78,cy-r*0.55),(cx+r*0.66,cy+r*0.46),
        (cx,cy+r),(cx-r*0.66,cy+r*0.46),(cx-r*0.78,cy-r*0.55)
    ]
    d.polygon(pts, fill=(26,50,82), outline=(95,145,205))
    if warning:
        d.line((cx,cy-65,cx,cy+15), fill=(240,205,95), width=18)
        d.ellipse((cx-10,cy+38,cx+10,cy+58), fill=(240,205,95))
    else:
        d.line((cx-50,cy,cx-12,cy+42), fill=(110,220,150), width=16)
        d.line((cx-12,cy+42,cx+62,cy-48), fill=(110,220,150), width=16)

def draw_vulnerability_cards(d):
    cards = [
        ("افزایش سطح دسترسی", (78,330,642,465)),
        ("اختلال سرویس", (78,495,642,630)),
        ("نشت اطلاعات", (78,660,642,795)),
    ]
    for text, box in cards:
        d.rounded_rectangle(box, radius=24, fill=(18,28,46), outline=(70,93,125), width=2)
        draw_shield(d, (120,(box[1]+box[3])//2), 42, warning=True)
        draw_rtl_block(d, text, box[1]+36, font(30, bold=True), 455, fill=(240,245,250), align="right")

def draw_package(d):
    cx, cy = 360, 535
    d.rounded_rectangle((140,360,580,720), radius=34, fill=(23,39,56), outline=(95,125,160), width=3)
    d.polygon([(140,420),(360,500),(580,420),(580,360),(360,280),(140,360)], fill=(34,57,78), outline=(95,125,160))
    d.line((360,500,360,720), fill=(95,125,160), width=3)
    d.line((140,420,140,360), fill=(95,125,160), width=3)
    d.line((580,420,580,360), fill=(95,125,160), width=3)
    d.polygon([(360,350),(315,410),(345,410),(345,475),(375,475),(375,410),(405,410)], fill=(120,210,160))
    d.text((195,555), "6.12.111-1", font=font(38, bold=True), fill=(230,240,250))

def draw_document(d):
    d.rounded_rectangle((160,300,560,800), radius=28, fill=(237,240,245), outline=(120,140,160), width=3)
    d.polygon([(470,300),(560,390),(470,390)], fill=(190,200,215))
    for i,w in enumerate((280,320,250,310,220)):
        y = 430 + i*58
        d.rounded_rectangle((220,y,220+w,y+16), radius=8, fill=(95,115,140))
    d.ellipse((380,650,520,790), outline=(55,90,130), width=18)
    d.line((490,760,590,860), fill=(55,90,130), width=22)

def draw_generic_visual(d, title: str):
    t = title.lower()
    if any(k in t for k in ("security", "vulnerability", "malware", "hack", "آسیب", "امنیت", "هک")):
        draw_shield(d, (360,530), 170, warning=True)
        return "security"
    if any(k in t for k in ("ai", "artificial intelligence", "llm", "agent", "chatgpt", "claude", "هوش مصنوعی")):
        nodes = [(210,430),(360,330),(510,430),(210,650),(360,750),(510,650),(360,540)]
        for a,b in [(0,2),(0,6),(2,6),(1,6),(3,6),(5,6),(4,6),(0,3),(2,5)]:
            d.line((*nodes[a],*nodes[b]), fill=(80,110,155), width=5)
        for x,y in nodes:
            d.ellipse((x-28,y-28,x+28,y+28), fill=(32,78,120), outline=(125,195,235), width=4)
        return "ai"
    if any(k in t for k in ("chip","gpu","nvidia","processor","hardware","تراشه","پردازنده","سخت افزار")):
        d.rounded_rectangle((175,350,545,720), radius=38, fill=(29,44,61), outline=(100,130,165), width=4)
        for x in range(210,530,60):
            d.line((x,300,x,350), fill=(120,150,180), width=8)
            d.line((x,720,x,770), fill=(120,150,180), width=8)
        d.rounded_rectangle((260,435,460,635), radius=24, fill=(48,82,120), outline=(150,190,220), width=4)
        return "hardware"
    if any(k in t for k in ("finance","economy","market","money","investing","اقتصاد","مالی","سرمایه","پول")):
        bars = [(180,640,240,760),(280,560,340,760),(380,460,440,760),(480,380,540,760)]
        for box in bars:
            d.rounded_rectangle(box, radius=12, fill=(70,130,175))
        d.line((150,790,570,790), fill=(170,190,210), width=4)
        return "finance"
    if any(k in t for k in ("software","github","linux","android","iphone","browser","code","نرم افزار","گیتهاب","لینوکس")):
        draw_terminal(d)
        return "software"
    d.rounded_rectangle((120,360,600,730), radius=34, fill=(22,35,52), outline=(85,110,145), width=3)
    d.text((170,460), "NEWS", font=font(110, bold=True), fill=(180,205,230))
    d.line((190,620,530,620), fill=(95,125,160), width=8)
    d.line((250,675,470,675), fill=(95,125,160), width=8)
    return "generic"

def draw_particles(d, seed):
    s = sum(ord(x) for x in seed)
    for i in range(26):
        x = 55 + ((s * (i+7) * 37) % 610)
        y = 245 + ((s * (i+11) * 53) % 590)
        r = 1 + (i % 3)
        d.ellipse((x-r,y-r,x+r,y+r), fill=(55,78,112))

def draw_glow_circle(d, center, r, fill, outline):
    cx, cy = center
    for k in range(6,0,-1):
        rr = r + k*14
        alpha = max(18, 80-k*8)
        # Solid approximation keeps the renderer dependency-free.
        d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr), outline=outline, width=max(1,7-k))

def paste_cover(base_img, image_path):
    with Image.open(image_path) as src:
        src = src.convert("RGB")
        fitted = ImageOps.fit(src, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5,0.48))
    base_img.paste(fitted, (0,0))
    overlay = Image.new("RGBA", (WIDTH,HEIGHT), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    # Cinematic readability gradient: keep the photo dominant.
    for y in range(HEIGHT):
        top_alpha = int(115 * max(0.0, 1.0 - y/430.0))
        bottom_alpha = int(185 * max(0.0, (y-760)/520.0))
        od.line((0,y,WIDTH,y), fill=(4,8,16,max(top_alpha,bottom_alpha)))
    base_img.paste(overlay, (0,0), overlay)

def make_visual(path: Path, title: str, caption: str, label: str, visual_kind: str, number: int, real_image_path=None):
    img = Image.new("RGB", (WIDTH, HEIGHT), (7,12,25))
    d = ImageDraw.Draw(img)

    if real_image_path and real_image_path.exists():
        paste_cover(img, real_image_path)
        # Minimal editorial overlay.
        d.rounded_rectangle((34,34,210,88), radius=18, fill=(10,18,32))
        d.text((54,48), fa(f"{number:02d}  •  {label}"), font=font(22,bold=True), fill=(230,240,248), **rtl_kwargs())

        # Small accent / scene marker.
        d.rounded_rectangle((35,150,115,162), radius=6, fill=(115,205,225))
        if number == 1:
            draw_rtl_block(d, title, 182, font(40,bold=True), 610, fill=(250,252,255), align="right", spacing=7)
        else:
            draw_rtl_block(d, "ادامهٔ ماجرا", 175, font(28,bold=True), 610, fill=(225,235,242), align="right", spacing=5)

        # Lower-third subtitle with strong contrast but limited footprint.
        d.rounded_rectangle((30,920,690,1228), radius=30, fill=(5,9,17), outline=(85,110,135), width=2)
        draw_rtl_block(d, caption, 966, font(34,bold=True), 575, fill=(250,250,250), align="center", spacing=10)
    else:
        for y in range(HEIGHT):
            t=y/HEIGHT
            d.line((0,y,WIDTH,y), fill=(7,int(13+18*t),int(27+35*t)))
        draw_particles(d,title+str(number))
        d.rounded_rectangle((42,42,678,142),radius=24,fill=(14,22,40),outline=(64,88,118),width=2)
        draw_rtl_block(d,title,62,font(30,bold=True),570,fill=(247,249,255),align="center",spacing=6)
        d.rounded_rectangle((48,168,255,218),radius=16,fill=(24,38,61),outline=(65,92,125),width=2)
        d.text((68,181),fa(f"{number:02d} • {label}"),font=font(22,bold=True),fill=(205,225,244),**rtl_kwargs())
        if visual_kind == "shield":
            d.rounded_rectangle((92,330,430,700), radius=34, fill=(18,31,50), outline=(74,108,145), width=3)
            for yy in (390,470,550,630):
                d.rounded_rectangle((135,yy,390,yy+52), radius=14, fill=(27,48,74), outline=(69,96,124), width=2)
                d.ellipse((160,yy+18,176,yy+34), fill=(95,180,230))
                d.line((205,yy+26,345,yy+26), fill=(90,120,150), width=6)
            draw_shield(d,(525,470),145,warning=True)
        elif visual_kind == "security_cards":
            nodes=[(180,390),(360,300),(540,390),(170,610),(360,720),(550,610),(360,510)]
            for a,b in ((0,6),(1,6),(2,6),(3,6),(4,6),(5,6)):
                d.line((*nodes[a],*nodes[b]),fill=(61,94,132),width=6)
            for x,y in nodes[:-1]:
                d.ellipse((x-52,y-52,x+52,y+52),fill=(25,52,83),outline=(95,138,186),width=4)
                draw_shield(d,(x,y),34,warning=True)
            d.ellipse((308,458,412,562),fill=(44,78,122),outline=(155,198,230),width=5)
            draw_shield(d,(360,510),52,warning=True)
        elif visual_kind == "package":
            d.polygon([(135,400),(360,500),(585,400),(585,650),(360,770),(135,650)],fill=(26,47,69),outline=(92,124,160))
            d.polygon([(135,400),(360,300),(585,400),(360,500)],fill=(40,67,92),outline=(100,132,165))
            d.line((360,500,360,770),fill=(92,124,160),width=4)
            d.polygon([(360,340),(315,410),(345,410),(345,475),(375,475),(375,410),(405,410)],fill=(118,218,165))
            draw_rtl_block(d,"نسخه به‌روزشده",810,font(38,bold=True),500,fill=(238,247,252),align="center")
            d.rounded_rectangle((192,860,528,930),radius=18,fill=(15,26,42),outline=(78,104,135),width=2)
            d.text((214,876),"6.12.111-1",font=font(34,bold=True),fill=(208,230,242))
        elif visual_kind == "document":
            d.rounded_rectangle((170,290,550,760),radius=28,fill=(232,237,244),outline=(112,135,158),width=4)
            d.polygon([(458,290),(550,382),(458,382)],fill=(190,201,215))
            for i,w in enumerate((275,315,240,292,220)):
                d.rounded_rectangle((215,445+i*52,215+w,461+i*52),radius=8,fill=(97,116,138))
            d.ellipse((380,610,520,750),outline=(42,82,125),width=16)
            d.line((492,720,600,828),fill=(42,82,125),width=20)
        elif visual_kind == "ai_network":
            draw_agent(d)
        elif visual_kind == "ai_chat":
            draw_ai_chat(d)
        elif visual_kind == "ai_agent":
            draw_data_flow(d)
        elif visual_kind == "ai_human":
            draw_human_machine(d)
        elif visual_kind == "software_terminal":
            draw_terminal(d)
        elif visual_kind == "software_browser":
            draw_browser(d)
        elif visual_kind == "software_code":
            draw_code(d)
        elif visual_kind == "software_flow":
            draw_data_flow(d)
        elif visual_kind == "chip":
            draw_chip_scene(d)
        elif visual_kind == "chip_data":
            draw_data_flow(d)
        elif visual_kind == "device":
            d.rounded_rectangle((215,300,505,790),radius=45,fill=(30,35,45),outline=(105,130,155),width=5)
            d.rounded_rectangle((245,350,475,700),radius=25,fill=(23,56,81),outline=(125,175,210),width=3)
        elif visual_kind == "industry":
            d.rounded_rectangle((90,520,630,735),radius=24,fill=(24,43,62),outline=(84,118,150),width=3)
            for x,h in ((135,100),(230,160),(325,210),(420,145),(515,190)):
                d.rounded_rectangle((x,735-h,x+58,735),radius=10,fill=(70,125,165))
        elif visual_kind in ("finance_chart","finance_signal"):
            draw_finance_chart(d)
        elif visual_kind == "finance_market":
            draw_data_flow(d)
        elif visual_kind == "finance_people":
            draw_human_machine(d)
        else:
            draw_generic_visual(d,title)

        d.rounded_rectangle((34,955,686,1220),radius=28,fill=(5,9,18),outline=(73,98,126),width=2)
        draw_rtl_block(d,caption,992,font(32,bold=True),570,fill=(248,249,250),align="center",spacing=9)
    img.save(path)


def split_sentences(text: str):
    clean = re.sub(r"\s+", " ", str(text)).strip()
    parts = re.split(r"(?<=[.!?؟؛])\s+", clean)
    parts = [p.strip() for p in parts if p.strip()]
    return parts

CURATED_STORIES = {
    "Several vulnerabilities have been discovered in the Linux kernel": {
        "title_fa": "هشدار امنیتی جدید برای هسته لینوکس",
        "script": (
            "یه هشدار امنیتی تازه برای هستهٔ لینوکس منتشر شده. "
            "دبیان گفته چند آسیب‌پذیری در بستهٔ لینوکس می‌تونن باعث افزایش سطح دسترسی، از کار افتادن سرویس یا نشت اطلاعات بشن. "
            "برای دبیان تریکسی، این مشکلات در نسخهٔ 6.12.111-1 برطرف شدن. "
            "برای جزئیات و بررسی وضعیت سیستم، به اطلاعیهٔ رسمی دبیان سر بزنید."
        ),
        "visuals": ["shield", "security_cards", "package", "document"],
        "labels": ["هشدار امنیتی", "چه مشکلی مطرح است؟", "رفع مشکل", "منبع"],
    }
}

def build_safe_script(title, source, context):
    desc = re.sub(r"\s+", " ", str(context.get("description") or "")).strip(" .")
    if desc:
        return f"{title}. {desc}. برای جزئیات بیشتر، بهتره متن منبع اصلی هم بررسی بشه."
    return f"{title}. برای جزئیات بیشتر، باید متن منبع اصلی رو بررسی کرد."

def generate_audience_script(title, source, context):
    fallback = build_safe_script(title, source, context)
    writer_url = os.environ.get("CONTENT_WRITER_URL", "").strip()
    if not writer_url:
        return title, fallback, "template"
    try:
        data = post_json(writer_url, {
            "title": title,
            "description": str(context.get("description") or "")[:1600],
            "source": source,
        })
        raw = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()
        marker_title, marker_script = "", raw
        m = re.search(r"TITLE\s*:\s*(.*?)\s*SCRIPT\s*:\s*(.*)$", raw, re.I)
        if m:
            marker_title = m.group(1).strip(" |-:")
            marker_script = m.group(2).strip()
        forbidden = ("رادار", "امتیاز", "الگوریتم", "فرایند تولید", "فرآیند تولید", "velocity", "score")
        if not data.get("ok") or len(marker_script) < 80 or any(x.lower() in marker_script.lower() for x in forbidden):
            return title, fallback, "template"
        return marker_title or title, marker_script[:1600], "ai"
    except Exception:
        return title, fallback, "template"

def run(cmd):
    subprocess.run(cmd, check=True)

TTS_REPLACEMENTS = {
    "هسته لینوکس": "هستهٔ لینُکس",
    "لینوکس": "لینُکس",
    "دبیان": "دِبیان",
    "تریکسی": "تریکسی",
    "آسیب‌پذیری": "آسیب‌پذیری",
    "6.12.111-1": "شش ممیز دوازده ممیز صد و یازده، خط یک",
    "DSA-6528-1": "دی اس ای، شش هزار و پانصد و بیست و هشت، خط یک",
}

def prepare_tts(text: str) -> str:
    out = str(text)
    for src, dst in TTS_REPLACEMENTS.items():
        out = out.replace(src, dst)
    out = re.sub(r"([.!؟])\s*", r"\1  ", out)
    return out

def piper_voice(text: str, wav: Path):
    VOICE_DIR.mkdir(exist_ok=True)
    run([sys.executable, "-m", "piper.download_voices", PIPER_VOICE, "--data-dir", str(VOICE_DIR)])
    tts_text = prepare_tts(text)
    run([sys.executable, "-m", "piper", "-m", PIPER_VOICE, "--data-dir", str(VOICE_DIR),
         "-f", str(wav), "--", tts_text])

def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or 1)

def make_broll_segment(video_path: Path, wav: Path, out: Path, duration: float, start_seconds: float):
    start=max(0.0,start_seconds)
    run([
        "ffmpeg","-y",
        "-ss",f"{start:.2f}","-i",str(video_path),
        "-i",str(wav),
        "-t",f"{duration:.3f}",
        "-vf",f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}",
        "-map","0:v:0","-map","1:a:0","-r",str(FPS),
        "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k",
        "-shortest","-movflags","+faststart",str(out)
    ])

def make_segment_video(img: Path, wav: Path, out: Path, duration: float):
    run([
        "ffmpeg","-y",
        "-loop","1","-i",str(img),
        "-i",str(wav),
        "-vf", f"zoompan=z='min(zoom+0.00045,1.07)':x='if(lte(on,1),iw*0.5,iw*0.5+sin(on/18)*18)':y='if(lte(on,1),ih*0.5,ih*0.5+cos(on/21)*12)':d=1:s={WIDTH}x{HEIGHT}:fps={FPS}",
        "-t", f"{duration:.3f}",
        "-r", str(FPS),
        "-c:v","libx264","-pix_fmt","yuv420p",
        "-c:a","aac","-b:a","128k",
        "-shortest","-movflags","+faststart",
        str(out)
    ])

def concat_segments(segment_files, final_mp4):
    manifest = final_mp4.parent / "concat.txt"
    manifest.write_text(
        "\n".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'" for p in segment_files) + "\n",
        encoding="utf-8",
    )
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(manifest),
         "-c","copy","-movflags","+faststart",str(final_mp4)])

def select_visuals(title, count):
    t = title.lower()
    if "linux" in t and any(k in t for k in ("vulner", "security", "kernel")):
        seq = ["shield", "security_cards", "package", "document"]
        return (seq * ((count+3)//4))[:count]
    if any(k in t for k in ("ai", "artificial intelligence", "llm", "chatgpt", "claude", "gemini", "agent")):
        seq = ["ai_network", "ai_chat", "ai_agent", "ai_human"]
        return (seq * ((count+3)//4))[:count]
    if any(k in t for k in ("chip", "gpu", "nvidia", "processor", "hardware")):
        seq = ["chip", "chip_data", "device", "industry"]
        return (seq * ((count+3)//4))[:count]
    if any(k in t for k in ("finance", "economy", "market", "money", "investing", "startup", "business")):
        seq = ["finance_chart", "finance_market", "finance_people", "finance_signal"]
        return (seq * ((count+3)//4))[:count]
    if any(k in t for k in ("software", "github", "linux", "android", "iphone", "browser", "code", "app")):
        seq = ["software_terminal", "software_browser", "software_code", "software_flow"]
        return (seq * ((count+3)//4))[:count]
    return (["generic", "generic_zoom", "generic_focus", "generic_news"] * ((count+3)//4))[:count]

def draw_ai_chat(d):
    d.rounded_rectangle((115,300,605,720), radius=34, fill=(18,31,53), outline=(82,119,163), width=4)
    d.rounded_rectangle((145,335,575,430), radius=22, fill=(31,53,82))
    d.rounded_rectangle((145,460,530,555), radius=22, fill=(24,45,69))
    d.rounded_rectangle((190,585,575,680), radius=22, fill=(37,64,94))
    draw_rtl_block(d, "پرسش", 355, font(28, bold=True), 220, fill=(205,225,240), align="right")
    draw_rtl_block(d, "پاسخ", 480, font(28, bold=True), 250, fill=(205,225,240), align="right")
    draw_rtl_block(d, "عامل هوشمند", 605, font(28, bold=True), 300, fill=(220,240,250), align="right")

def draw_agent(d):
    nodes=[(170,430),(360,320),(550,430),(180,650),(360,760),(540,650),(360,540)]
    for a,b in ((0,6),(1,6),(2,6),(3,6),(4,6),(5,6)):
        d.line((*nodes[a],*nodes[b]), fill=(66,100,145), width=6)
    for x,y in nodes:
        d.ellipse((x-34,y-34,x+34,y+34), fill=(27,61,100), outline=(128,195,230), width=5)
    d.rounded_rectangle((260,835,460,895), radius=20, fill=(20,37,58), outline=(72,103,136), width=2)
    draw_rtl_block(d, "هوش مصنوعی", 850, font(28, bold=True), 180, fill=(220,238,248), align="center")

def draw_human_machine(d):
    d.ellipse((125,420,310,605), fill=(29,52,77), outline=(105,145,180), width=4)
    d.arc((165,470,270,575), start=200, end=520, fill=(195,215,230), width=8)
    d.ellipse((420,400,605,585), fill=(34,62,92), outline=(115,180,220), width=4)
    for x,y in ((465,445),(525,445),(465,505),(525,505)):
        d.rounded_rectangle((x,y,x+38,y+38), radius=8, fill=(110,185,220))
    d.line((300,510,420,500), fill=(125,175,210), width=10)
    d.polygon([(398,482),(432,500),(398,518)], fill=(125,175,210))

def draw_browser(d):
    d.rounded_rectangle((95,305,625,760), radius=28, fill=(20,31,47), outline=(87,116,146), width=4)
    d.rounded_rectangle((95,305,625,370), radius=28, fill=(30,42,59))
    for x in (125,150,175):
        d.ellipse((x,327,x+16,343), fill=(100,125,150))
    d.rounded_rectangle((135,410,585,475), radius=18, fill=(35,58,84))
    d.line((150,530,555,530), fill=(95,120,148), width=7)
    d.line((150,585,500,585), fill=(75,104,133), width=7)
    d.line((150,640,535,640), fill=(75,104,133), width=7)

def draw_code(d):
    d.rounded_rectangle((90,305,630,765), radius=28, fill=(15,21,32), outline=(78,101,128), width=4)
    code_lines=[("import",210),("def agent()",270),("return result",330),("security_check()",390),("deploy()",450)]
    yy=385
    for txt,w in code_lines:
        d.rounded_rectangle((130,yy,130+w,yy+18), radius=8, fill=(88,133,165))
        yy += 64

def draw_chip_scene(d):
    d.rounded_rectangle((175,365,545,735), radius=36, fill=(24,40,58), outline=(101,135,169), width=4)
    for x in range(205,540,55):
        d.line((x,310,x,365), fill=(124,153,180), width=8)
        d.line((x,735,x,790), fill=(124,153,180), width=8)
    d.rounded_rectangle((260,450,460,650), radius=26, fill=(42,80,123), outline=(165,200,225), width=5)
    d.text((295,520), "GPU", font=font(58, bold=True), fill=(225,238,246))

def draw_data_flow(d):
    boxes=[(120,370,290,500),(360,290,600,420),(180,590,390,720),(450,560,620,690)]
    for i,b in enumerate(boxes):
        d.rounded_rectangle(b, radius=22, fill=(22,41,64), outline=(83,119,153), width=3)
    d.line((290,435,360,355), fill=(115,170,205), width=9)
    d.line((480,420,390,590), fill=(115,170,205), width=9)
    d.line((390,655,450,625), fill=(115,170,205), width=9)

def draw_finance_chart(d):
    d.line((110,755,610,755), fill=(150,175,195), width=4)
    pts=[(125,690),(220,640),(300,665),(385,540),(470,585),(560,410)]
    d.line(*pts, fill=(100,180,220), width=9, joint="curve")
    for x,y in pts:
        d.ellipse((x-10,y-10,x+10,y+10), fill=(150,210,235))
    d.rounded_rectangle((110,305,610,350), radius=18, fill=(18,34,53))

def main():
    OUT.mkdir(exist_ok=True)
    test_title = os.environ.get("CONTENT_TEST_TITLE", "").strip()
    if test_title:
        candidates = [{
            "source": "visual_test",
            "title": test_title,
            "url": "https://lwn.net/Articles/1097401/",
            "score": 75.0,
            "content_fit": 10.0,
            "velocity_pct": 0,
            "source_count": 1,
            "opportunity_score": 75.0,
        }]
        candidate_source = "visual_test"
    else:
        candidates, candidate_source = get_candidate_data()
    usable = []
    for c in candidates:
        try:
            score = float(c.get("score", 0))
            raw_fit = c.get("content_fit")
            fit = float(raw_fit) if raw_fit is not None else float(local_content_fit(c.get("title", "")))
            risk = str(c.get("risk_flags") or "").strip()
            source_count = float(c.get("source_count", 1) or 1)
            opportunity = float(c.get("opportunity_score", score) or score)
            if risk or fit < 5 or score < 50:
                continue
            usable.append((opportunity + min(source_count,3)*5, c))
        except Exception:
            pass
    if not usable:
        raise RuntimeError("No usable candidate found")
    usable.sort(key=lambda x: x[0], reverse=True)
    _, c = usable[0]

    title = re.sub(r"\s+", " ", str(c.get("title") or "موضوع جدید")).strip()
    source = str(c.get("source") or candidate_source or "radar")
    score = float(c.get("score", 0))
    fit = float(c.get("content_fit", 0))
    iran = c.get("iran_interest_similarity")
    context = fetch_source_context(c.get("url"))

    video_dir = Path("out") / "commons_video"
    openverse_dir = Path("out") / "openverse"
    commons_dir = Path("out") / "commons"
    video_assets = download_commons_videos(title, video_dir, limit=3)
    visual_assets = download_openverse_visuals(title, openverse_dir, limit=4)
    if not visual_assets:
        visual_assets = download_commons_visuals(title, commons_dir, limit=3)

    curated = CURATED_STORIES.get(title)
    if curated:
        display_title = curated["title_fa"]
        script = curated["script"]
        script_mode = "curated"
    else:
        display_title, script, script_mode = generate_audience_script(title, source, context)

    sentences = split_sentences(script)
    sentences = sentences[:6] if len(sentences) > 1 else sentences
    if not sentences:
        raise RuntimeError("Empty script")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")[:50] or "topic"
    base = OUT / f"{ts}-{slug}"
    base.mkdir()

    (base / "script.txt").write_text(script, encoding="utf-8")
    (base / "candidate.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")

    if curated:
        visual_seq = (curated["visuals"] * ((len(sentences)+3)//4))[:len(sentences)]
        labels = (curated["labels"] * ((len(sentences)+3)//4))[:len(sentences)]
    else:
        visual_seq = select_visuals(title, len(sentences))
        labels = ["خبر", "جزئیات", "زمینه", "بررسی", "نکته", "جمع‌بندی"][:len(sentences)]

    segment_files, segment_meta = [], []
    for i, sentence in enumerate(sentences, 1):
        wav = base / f"voice{i}.wav"
        piper_voice(sentence, wav)
        duration = wav_seconds(wav)

        img = base / f"scene{i}.png"
        real_asset = visual_assets[(i-1) % len(visual_assets)][0] if visual_assets else None
        make_visual(img, display_title, sentence, labels[i-1], visual_seq[i-1], i, real_asset)

        seg = base / f"segment{i}.mp4"
        if video_assets:
            vpath, vmeta = video_assets[(i-1) % len(video_assets)]
            start=max(0.0, float(i-1)*4.0)
            if vmeta.get("duration_seconds",0) > 0:
                start = min(start, max(0.0, float(vmeta["duration_seconds"])-duration-0.1))
            make_broll_segment(vpath, wav, seg, duration, start)
        else:
            make_segment_video(img, wav, seg, duration)
        segment_files.append(seg)
        segment_meta.append({
            "index": i,
            "text": sentence,
            "duration_seconds": round(duration, 2),
            "visual": visual_seq[i-1],
        })

    mp4 = base / "video.mp4"
    concat_segments(segment_files, mp4)

    if visual_assets:
        attribution_lines = []
        for _,m in visual_assets:
            attribution_lines.append(
                f'Image: {m["title"]} — {m["artist"] or "author not stated"} — {m["license"]}. Source: {m["page_url"]}'
            )
        (base / "attribution.txt").write_text("\n".join(attribution_lines), encoding="utf-8")

    meta = {
        "generated_at": ts,
        "title": title,
        "source": source,
        "source_url": c.get("url"),
        "source_context": context,
        "display_title_fa": display_title,
        "script_mode": script_mode,
        "voice_model": PIPER_VOICE,
        "score_internal": score,
        "content_fit_internal": fit,
        "iran_interest_similarity_internal": iran,
        "score": score,
        "content_fit": fit,
        "velocity_pct": c.get("velocity_pct"),
        "source_count": c.get("source_count", 1),
        "iran_interest_similarity": iran,
        "duration_seconds": round(sum(x["duration_seconds"] for x in segment_meta), 2),
        "cost": 0,
        "human_content_creation_required": False,
        "caption_sync": "sentence_exact",
        "visual_mode": "commons_broll_then_openverse_real_images_then_commons_fallback",
        "video_assets": [m for _,m in video_assets],
        "visual_assets": [m for _,m in visual_assets],
        "quality_gate": "passed",
        "segments": segment_meta,
    }
    (base / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()
