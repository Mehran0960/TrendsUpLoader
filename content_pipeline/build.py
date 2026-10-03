#!/usr/bin/env python3
# automated zero-cost content pipeline
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

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

def get_candidate_data():
    errors = []
    for url in (RADAR_URL, RADAR_URL.rsplit("/", 1)[0] + "/status"):
        try:
            data = get_json(url)
            candidates = data.get("candidates") if isinstance(data, dict) else None
            if isinstance(candidates, list) and candidates:
                return candidates, "radar"
            top = data.get("top") if isinstance(data, dict) else None
            if isinstance(top, list) and top:
                return top, "status"
        except Exception as e:
            errors.append(str(e))

    # Zero-cost direct fallback: public Hacker News API.
    try:
        ids = get_json("https://hacker-news.firebaseio.com/v0/beststories.json")[:15]
        candidates = []
        keywords = re.compile(r"\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics|software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|startup|business|chip|gpu|nvidia|hardware)\b", re.I)
        for i, story_id in enumerate(ids):
            try:
                item = get_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
                title = str(item.get("title") or "").strip()
                if item.get("type") == "story" and title and keywords.search(title):
                    candidates.append({
                        "source": "hacker_news",
                        "title": title,
                        "url": item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                        "score": min(75, float(item.get("score") or 0) / 2.0),
                        "content_fit": 12,
                        "velocity_pct": 0,
                        "source_count": 1,
                        "opportunity_score": min(75, float(item.get("score") or 0) / 2.0)
                    })
            except Exception:
                continue
        if candidates:
            return candidates, "hn-direct"
    except Exception as e:
        errors.append(str(e))
    raise RuntimeError("No usable candidate source found: " + " | ".join(errors[-3:]))

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
    r = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1",str(path)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

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
            fit = float(c.get("content_fit", 0))
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

    script = (
        f"یه موضوع جالب که الان در رادار ما دیده شده: {title}. "
        f"این موضوع در منبع {source.replace('_',' ')} دیده شده و امتیاز محتوایی آن {fit:.0f} است. "
        f"نکته مهم اینجاست که صرفاً داغ بودن یک موضوع به معنی خوب بودنش برای محتوا نیست؛ "
        f"ما دنبال موضوعی هستیم که بشود از آن یک روایت کوتاه و مفید ساخت. "
        f"سؤال اصلی اینه: این موضوع چرا الان توجه گرفته و برای کاربر ایرانی چه نکته‌ای از دلش درمیاد؟ "
        f"این مورد فعلاً در مرحله آزمایش رادار قرار دارد و قبل از هر ادعای بزرگ، باید واکنش واقعی مخاطب را اندازه بگیریم."
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")[:50] or "topic"
    base = OUT / f"{ts}-{slug}"
    base.mkdir()

    (base / "script.txt").write_text(script, encoding="utf-8")
    (base / "candidate.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")

    wav = base / "voice.wav"
    piper_voice(script, wav)
    duration = ffprobe_seconds(wav)

    scenes = [
        ("امروز رادار چی پیدا کرده؟", "TREND RADAR"),
        ("فقط داغ بودن کافی نیست؛ باید قابل تبدیل به محتوا هم باشد.", "WHY IT MATTERS"),
        ("حالا باید واکنش واقعی مخاطب را اندازه بگیریم.", "NEXT TEST"),
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
        "score": score,
        "content_fit": fit,
        "iran_interest_similarity": iran,
        "duration_seconds": round(duration, 2),
        "cost": 0,
        "human_content_creation_required": False
    }
    (base / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == "__main__":
    main()

