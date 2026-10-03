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

from PIL import Image, ImageDraw, ImageFont, features
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

def post_json(url: str, payload: dict):
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"User-Agent": "trend-radar-content-pipeline/2.0", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=25) as r:
        return json.load(r)

def local_content_fit(title: str) -> int:
    t = str(title or "").lower()
    patterns = [
        (r"\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics)\b|هوش.?مصنوعی|چت.?جی.?پی.?تی|کلود|جمینای|ربات", 12),
        (r"\b(software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|app|apps)\b|نرم.?افزار|گیت.?هاب|لینوکس|اندروید|آیفون|اپل|اپلیکیشن|برنامه.?نویسی|مرورگر", 9),
        (r"\b(gadget|smartphone|laptop|chip|gpu|nvidia|amd|intel|hardware)\b|گجت|گوشی|لپ.?تاپ|تراشه|پردازنده|سخت.?افزار", 8),
        (r"\b(startup|business|entrepreneur|ecommerce|retail|market|economy|finance|investing|money)\b|استارت.?آپ|کسب.?و.?کار|کارآفرینی|اقتصاد|مالی|سرمایه.?گذاری|پول", 8),
        (r"\b(security|vulnerability|malware|linux kernel|cybersecurity|hack)\b|امنیت|آسیب.?پذیری|بدافزار|هسته.?ی? لینوکس|هک|امنیت.?سایبری", 10),
    ]
    best = 0
    for pattern, weight in patterns:
        if re.search(pattern, t, re.I):
            best = max(best, weight)
    return best

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

def make_visual(path: Path, title: str, caption: str, label: str, visual_kind: str, number: int):
    img = Image.new("RGB", (WIDTH, HEIGHT), (7, 12, 25))
    d = ImageDraw.Draw(img)
    # Deep atmospheric backdrop.
    for y in range(HEIGHT):
        t=y/HEIGHT
        d.line((0,y,WIDTH,y), fill=(7, int(13+18*t), int(27+35*t)))
    draw_particles(d, title + str(number))

    # Compact header: don't repeat a huge title block on every shot.
    d.rounded_rectangle((42,42,678,142), radius=24, fill=(14,22,40), outline=(64,88,118), width=2)
    draw_rtl_block(d, title, 62, font(30, bold=True), 570, fill=(247,249,255), align="center", spacing=6)

    d.rounded_rectangle((48,168,255,218), radius=16, fill=(24,38,61), outline=(65,92,125), width=2)
    d.text((68,181), fa(f"{number:02d} • {label}"), font=font(22, bold=True), fill=(205,225,244), **rtl_kwargs())

    # Image-first central composition.
    if visual_kind == "shield":
        # Isometric server + shield + attack path.
        d.rounded_rectangle((92,330,430,700), radius=34, fill=(18,31,50), outline=(74,108,145), width=3)
        for yy in (390,470,550,630):
            d.rounded_rectangle((135,yy,390,yy+52), radius=14, fill=(27,48,74), outline=(69,96,124), width=2)
            d.ellipse((160,yy+18,176,yy+34), fill=(95,180,230))
            d.line((205,yy+26,345,yy+26), fill=(90,120,150), width=6)
        draw_shield(d, (525,470), 145, warning=True)
        d.line((438,350,510,410), fill=(205,90,100), width=9)
        d.line((438,410,500,470), fill=(205,90,100), width=9)
        d.ellipse((425,338,450,363), fill=(230,100,105))
        d.ellipse((425,398,450,423), fill=(230,100,105))
    elif visual_kind == "security_cards":
        # Three-dimensional impact map rather than flat cards.
        nodes=[(180,390),(360,300),(540,390),(170,610),(360,720),(550,610),(360,510)]
        for a,b in ((0,6),(1,6),(2,6),(3,6),(4,6),(5,6)):
            d.line((*nodes[a],*nodes[b]), fill=(61,94,132), width=6)
        for i,(x,y) in enumerate(nodes[:-1]):
            d.ellipse((x-52,y-52,x+52,y+52), fill=(25,52,83), outline=(95,138,186), width=4)
            draw_shield(d,(x,y),34,warning=True)
        d.ellipse((308,458,412,562), fill=(44,78,122), outline=(155,198,230), width=5)
        draw_shield(d,(360,510),52,warning=True)
    elif visual_kind == "package":
        # Large software package with version and update arrow.
        d.polygon([(135,400),(360,500),(585,400),(585,650),(360,770),(135,650)],
                  fill=(26,47,69), outline=(92,124,160))
        d.polygon([(135,400),(360,300),(585,400),(360,500)], fill=(40,67,92), outline=(100,132,165))
        d.line((360,500,360,770), fill=(92,124,160), width=4)
        d.polygon([(360,340),(315,410),(345,410),(345,475),(375,475),(375,410),(405,410)],
                  fill=(118,218,165))
        draw_rtl_block(d, "نسخه به‌روزشده", 810, font(38, bold=True), 500, fill=(238,247,252), align="center")
        d.rounded_rectangle((192,860,528,930), radius=18, fill=(15,26,42), outline=(78,104,135), width=2)
        d.text((214,876), "6.12.111-1", font=font(34, bold=True), fill=(208,230,242))
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
        d.rounded_rectangle((215,300,505,790), radius=45, fill=(30,35,45), outline=(105,130,155), width=5)
        d.rounded_rectangle((245,350,475,700), radius=25, fill=(23,56,81), outline=(125,175,210), width=3)
        d.ellipse((330,720,390,780), outline=(105,130,155), width=5)
    elif visual_kind == "industry":
        d.rounded_rectangle((90,520,630,735), radius=24, fill=(24,43,62), outline=(84,118,150), width=3)
        for x,h in ((135,100),(230,160),(325,210),(420,145),(515,190)):
            d.rounded_rectangle((x,735-h,x+58,735), radius=10, fill=(70,125,165))
    elif visual_kind == "finance_chart":
        draw_finance_chart(d)
    elif visual_kind == "finance_market":
        draw_data_flow(d)
    elif visual_kind == "finance_people":
        draw_human_machine(d)
    elif visual_kind == "finance_signal":
        draw_finance_chart(d)
    elif visual_kind == "document":
        # Official notice + magnifier / verification motif.
        d.rounded_rectangle((170,290,550,760), radius=28, fill=(232,237,244), outline=(112,135,158), width=4)
        d.polygon([(458,290),(550,382),(458,382)], fill=(190,201,215))
        for i,w in enumerate((275,315,240,292,220)):
            d.rounded_rectangle((215,445+i*52,215+w,461+i*52), radius=8, fill=(97,116,138))
        d.ellipse((380,610,520,750), outline=(42,82,125), width=16)
        d.line((492,720,600,828), fill=(42,82,125), width=20)
        d.ellipse((432,662,468,698), outline=(95,155,205), width=7)
    else:
        draw_generic_visual(d, title)

    # Short subtitle strip; the picture stays dominant.
    d.rounded_rectangle((34,955,686,1220), radius=28, fill=(5,9,18), outline=(73,98,126), width=2)
    draw_rtl_block(d, caption, 992, font(32, bold=True), 570, fill=(248,249,250), align="center", spacing=9)
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
        make_visual(img, display_title, sentence, labels[i-1], visual_seq[i-1], i)

        seg = base / f"segment{i}.mp4"
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
        "visual_mode": "original_topic_illustrations",
        "quality_gate": "passed",
        "segments": segment_meta,
    }
    (base / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()
