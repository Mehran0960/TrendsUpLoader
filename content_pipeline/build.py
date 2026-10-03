#!/usr/bin/env python3
# automated zero-cost content pipeline
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

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

RADAR_URL = os.environ.get("RADAR_URL", "https://trend-radar.m-hoseyni-6966.workers.dev/candidates")
OUT = Path("out")
VOICE_DIR = Path(".voices")
WIDTH, HEIGHT = 720, 1280

def get_json(url: str):
    req = Request(url, headers={"User-Agent": "trend-radar-content-pipeline/1.0"})
    with urlopen(req, timeout=20) as r:
        return json.load(r)

def post_json(url: str, payload: dict):
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"User-Agent": "trend-radar-content-pipeline/1.0", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=25) as r:
        return json.load(r)

def local_content_fit(title: str) -> int:
    t = str(title or "").lower()
    patterns = [
        (r"\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics)\b|هوش مصنوعی|چت.?جی.?پی.?تی|کلود|جمینای|ربات", 12),
        (r"\b(software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|app|apps)\b|نرم.?افزار|گیت.?هاب|لینوکس|اندروید|آیفون|اپلیکیشن|برنامه.?نویسی", 9),
        (r"\b(gadget|smartphone|laptop|chip|gpu|nvidia|amd|intel|hardware)\b|گجت|گوشی|لپ.?تاپ|تراشه|پردازنده|سخت.?افزار", 8),
        (r"\b(startup|business|entrepreneur|ecommerce|retail|market|economy|finance|investing|money)\b|استارت.?آپ|کسب.?و.?کار|کارآفرینی|اقتصاد|مالی|سرمایه.?گذاری|پول", 8),
    ]
    best = 0
    for pattern, weight in patterns:
        if re.search(pattern, t, re.I):
            best = max(best, weight)
    return best

def get_candidate_data():
    collected = []
    errors = []

    for url, mode in (
        (RADAR_URL, "radar"),
        (RADAR_URL.rsplit("/", 1)[0] + "/status", "status"),
    ):
        try:
            data = get_json(url)
            if mode == "radar":
                items = data.get("candidates") if isinstance(data, dict) else None
            else:
                items = data.get("top") if isinstance(data, dict) else None
            if isinstance(items, list):
                collected.extend(items)
        except Exception as e:
            errors.append(f"{mode}: {e}")

    # Independent zero-cost fallback: public Hacker News API.
    try:
        ids = get_json("https://hacker-news.firebaseio.com/v0/beststories.json")[:20]
        keywords = re.compile(
            r"\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics|software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|startup|business|chip|gpu|nvidia|hardware)\b",
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
                        "content_fit": 12,
                        "velocity_pct": 0,
                        "source_count": 1,
                        "opportunity_score": sc
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
        req = Request(str(url), headers={"User-Agent": "Mozilla/5.0 (compatible; TrendRadarBot/1.0)"})
        with urlopen(req, timeout=10) as r:
            raw = r.read(180000).decode("utf-8", errors="ignore")
        m = re.search(r'<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)', raw, re.I)
        desc = re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else ""
        return {"description": desc[:700]}
    except Exception:
        return {}
def fa(text: str) -> str:
    return get_display(arabic_reshaper.reshape(str(text)))

def font(size: int):
    for p in (
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def wrap_pixels(draw, text, fnt, max_width):
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        nxt = (cur + " " + word).strip()
        if draw.textbbox((0, 0), fa(nxt), font=fnt)[2] <= max_width:
            cur = nxt
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def draw_center(draw, text, y, fnt, max_width, fill=(245,245,245), spacing=16):
    rendered = [fa(x) for x in wrap_pixels(draw, text, fnt, max_width)]
    heights = [draw.textbbox((0,0), x, font=fnt)[3] for x in rendered]
    total = sum(heights) + spacing * max(0, len(rendered)-1)
    cy = y - total / 2
    for line, h in zip(rendered, heights):
        box = draw.textbbox((0,0), line, font=fnt)
        x = (WIDTH - (box[2]-box[0])) / 2
        draw.text((x+2, cy+2), line, font=fnt, fill=(0,0,0))
        draw.text((x, cy), line, font=fnt, fill=fill)
        cy += h + spacing

def gradient_bg(seed: str):
    img = Image.new("RGB", (WIDTH, HEIGHT))
    px = img.load()
    s = sum(ord(c) for c in seed) % 255
    for y in range(HEIGHT):
        for x in range(WIDTH):
            t = (x+y)/(WIDTH+HEIGHT)
            px[x,y] = (int(10 + 25*t + s*0.05) % 70,
                       int(14 + 30*(1-t)) % 75,
                       int(28 + 55*t) % 100)
    return img

def make_scene(path: Path, title: str, body: str, label: str):
    img = gradient_bg(title + label)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((36,36,WIDTH-36,HEIGHT-36), radius=28, outline=(140,140,140), width=2)
    d.text((52,62), fa(label), font=font(28), fill=(190,190,190))
    draw_center(d, title, 390, font(54), WIDTH-110)
    draw_center(d, body, 820, font(42), WIDTH-100, fill=(225,225,225), spacing=14)
    img.save(path)

def build_safe_script(title, source, context):
    desc = re.sub(r"\s+", " ", str(context.get("description") or "")).strip(" .")
    if desc:
        return (
            f"{title}. {desc}. "
            "جزئیات بیشتر و صحت ادعاها را باید از منبع اصلی بررسی کرد. "
            f"منبع: {source.replace('_', ' ')}."
        )
    return (
        f"{title}. "
        "این خبر فعلاً بر اساس عنوان منبع روایت می‌شود؛ برای جزئیات بیشتر باید متن منبع اصلی بررسی شود. "
        f"منبع: {source.replace('_', ' ')}."
    )

def generate_audience_script(title, source, context):
    fallback = build_safe_script(title, source, context)
    writer_url = os.environ.get("CONTENT_WRITER_URL", "").strip()
    if not writer_url:
        return title, fallback, "template"
    try:
        data = post_json(writer_url, {"title": title, "description": str(context.get("description") or "")[:1600], "source": source})
        raw = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()
        marker_title, marker_script = "", raw
        m = re.search(r"TITLE\s*:\s*(.*?)\s*SCRIPT\s*:\s*(.*)$", raw, re.I)
        if m:
            marker_title = m.group(1).strip(" |-:")
            marker_script = m.group(2).strip()
        forbidden = ("رادار", "امتیاز", "الگوریتم", "فرایند تولید", "فرآیند تولید")
        if not data.get("ok") or len(marker_script) < 80 or any(x in marker_script for x in forbidden):
            return title, fallback, "template"
        return marker_title or title, marker_script[:1600], "ai"
    except Exception:
        return title, fallback, "template"

def run(cmd):
    subprocess.run(cmd, check=True)

def piper_voice(text: str, wav: Path):
    VOICE_DIR.mkdir(exist_ok=True)
    run([sys.executable, "-m", "piper.download_voices",
         "fa_IR-amir-medium", "--data-dir", str(VOICE_DIR)])
    run([sys.executable, "-m", "piper",
         "-m", "fa_IR-amir-medium", "--data-dir", str(VOICE_DIR),
         "-f", str(wav), "--", text])

def ffprobe_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate or 1)

def make_video(images, wav, mp4, duration):
    per = max(3.0, duration / len(images))
    inputs, filters = [], []
    for i, img in enumerate(images):
        inputs += ["-loop","1","-t",f"{per:.3f}","-i",str(img)]
        filters.append(
            f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
        )
    concat = "".join(f"[v{i}]" for i in range(len(images)))
    filters.append(f"{concat}concat=n={len(images)}:v=1:a=0[v]")
    run(["ffmpeg","-y",*inputs,"-i",str(wav),"-filter_complex",";".join(filters),
         "-map","[v]","-map",f"{len(images)}:a:0","-t",f"{duration:.3f}",
         "-r","30","-c:v","libx264","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","128k","-movflags","+faststart",str(mp4)])

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
    display_title, script, script_mode = generate_audience_script(title, source, context)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")[:50] or "topic"
    base = OUT / f"{ts}-{slug}"
    base.mkdir()

    (base / "script.txt").write_text(script, encoding="utf-8")
    (base / "candidate.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")

    wav = base / "voice.wav"
    piper_voice(script, wav)
    duration = ffprobe_seconds(wav)

    summary = re.sub(r"\s+", " ", str(context.get("description") or "")).strip()
    summary_short = summary[:220] if summary else "برای جزئیات بیشتر، منبع اصلی خبر را بررسی کنید."
    scenes = [
        (display_title, "01 • خبر"),
        (summary_short, "02 • زمینه"),
        ("جزئیات و صحت ادعاها را از منبع اصلی بررسی کنید.", "03 • بررسی"),
        ("منبع اصلی در توضیحات پست قرار می‌گیرد.", "04 • منبع"),
    ]
    images = []
    for i, (body, label) in enumerate(scenes, 1):
        p = base / f"scene{i}.png"
        make_scene(p, title, body, label)
        images.append(p)

    mp4 = base / "video.mp4"
    make_video(images, wav, mp4, duration)

    meta = {
        "generated_at": ts,
        "title": title,
        "source": source,
        "source_url": c.get("url"),
        "source_context": context,
        "display_title_fa": display_title,
        "script_mode": script_mode,
        "script_mode": script_mode,
        "score_internal": score,
        "content_fit_internal": fit,
        "iran_interest_similarity_internal": iran,
        "score": score,
        "content_fit": fit,
        "velocity_pct": c.get("velocity_pct"),
        "source_count": c.get("source_count", 1),
        "iran_interest_similarity": iran,
        "duration_seconds": round(duration, 2),
        "cost": 0,
        "human_content_creation_required": False,
        "fact_safe_script_mode": True,
        "quality_gate": "passed"
    }
    (base / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()

