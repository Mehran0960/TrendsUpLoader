#!/usr/bin/env python3
"""
Automated Persian comedy remix prototype.
# Smoke-triggered animal-first build; publication is gated by validation.
V1 deliberately avoids TTS and social-media scraping:
- source pool is limited to CC0/public-domain Commons files
- several short moments are cut and reframed into 9:16
- original Persian hook/captions are burned into the video
- source/license metadata is written beside the output
"""
import json
import os
import random
import re
import subprocess
import itertools
import sys
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

OUT = Path("out")
CACHE = Path("comedy_sources")
WIDTH, HEIGHT = 720, 1280
FPS = 30
MIN_TOTAL = 18.0
MAX_TOTAL = 28.0
random.seed(int(os.environ.get("GITHUB_RUN_ID", "1")))

# Curated, license-explicit starter pool. Keep the pool small while we validate
# the actual format. The next iteration can discover more Commons media via API.
SOURCES = [
    {
        "id": "cat_jumpscare",
        "filename": "Cat Jumpscare.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Cat_Jumpscare.webm",
        "license": "CC0",
        "author": "Panini!",
        "title": "Cat runs toward the camera",
        "start": 1.0,
        "duration": 4.6,
        "caption": "من فقط اومدم بگم سلام…\nنزدیک‌تر 😐😂",
    },
    {
        "id": "husky_howl",
        "filename": "Howling Husky Dog.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Howling_Husky_Dog.webm",
        "license": "CC0",
        "author": "Klaudya Teodora",
        "title": "Howling Husky Dog",
        "start": 4.0,
        "duration": 5.8,
        "caption": "وقتی هنوز چیزی نگفتی…\nولی طرف تصمیم گرفته سخنرانی کنه 😂",
    },
    {
        "id": "doge_warning",
        "filename": "Cuidado con el Perro, Gran Plaza Mazatlán, 20 de junio de 2026.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Cuidado_con_el_Perro,_Gran_Plaza_Mazatlán,_20_de_junio_de_2026.webm",
        "license": "CC0",
        "author": "Roedor Pacheco",
        "title": "Beware of the dog",
        "start": 0.8,
        "duration": 4.7,
        "caption": "روی در نوشته بود «مراقب سگ»…\nولی نگفته بود کِی! 😭",
    },
    {
        "id": "cat_pigeon",
        "filename": "Cat chasing a pigeon.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Cat_chasing_a_pigeon.webm",
        "license": "CC0",
        "author": "Jeromi Mikhael",
        "title": "Cat fails to catch a pigeon",
        "start": 3.0,
        "duration": 4.8,
        "caption": "نقشه: شکار حرفه‌ای\nواقعیت: یک جورایی… نه 😂",
        "min_width": 640,
    },
    {
        "id": "sophy_cat",
        "filename": "Sophy the Cat is Really High On A Ledge.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Sophy_the_Cat_is_Really_High_On_A_Ledge.webm",
        "license": "CC0",
        "author": "PseudoSkull",
        "title": "Sophy the Cat on a ledge",
        "start": 0.6,
        "duration": 4.8,
        "caption": "وقتی با اعتمادبه‌نفس می‌ری بالا…\nو وسط راه تازه می‌فهمی چرا نه 😭",
        "min_width": 720,
    },
    {
        "id": "curious_bird",
        "filename": "Curious Little Bird Looking at a Camera.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Curious_Little_Bird_Looking_at_a_Camera.webm",
        "license": "CC0",
        "author": "jlaswilson",
        "title": "Curious little bird looking at a camera",
        "start": 6.0,
        "duration": 4.8,
        "caption": "این دیگه چیه؟ 👀\nبذار یه کم زل بزنم ببینم…",
        "min_width": 720,
    },
    {
        "id": "colorful_bird",
        "filename": "Cute Colorful Bird on Walking on Ledge.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Cute_Colorful_Bird_on_Walking_on_Ledge.webm",
        "license": "CC0",
        "author": "Yourusernamewillbepublic2",
        "title": "Cute colorful bird walking on a ledge",
        "start": 2.0,
        "duration": 4.8,
        "caption": "بچه‌ها من فقط داشتم رد می‌شدم…\nچرا همه دارن نگام می‌کنن؟ 😂",
        "min_width": 720,
    },
    {
        "id": "ocicat_wheel",
        "filename": "Ocicat on Cat Wheel.webm",
        "page": "https://commons.wikimedia.org/wiki/File:Ocicat_on_Cat_Wheel.webm",
        "license": "CC0",
        "author": "Oldperson",
        "title": "Ocicat running on a cat wheel",
        "start": 8.0,
        "duration": 4.8,
        "caption": "من: از فردا ورزش می‌کنم.\nمن از فردا: همین الان 😭",
        "min_width": 720,
    },
]

HOOKS = [
    ("حیوانات امروز تصمیم گرفتن کاملاً غیرقابل‌پیش‌بینی باشن 😂", 1.0),
    ("وقتی حیوان خونگی‌ت یه شخصیت فرعی نیست…\nقهرمان اصلیه 😂", 1.0),
    ("همه‌چی عادی بود… تا اینا وارد صحنه شدن 😭", 1.0),
]
ENDS = [
    ("این یکی رو برای اون رفیقت بفرست 😂", 1.0),
    ("اگه خندیدی، تقصیر خودش بود 😭", 0.95),
    ("قسمت بعدی حیوانات؟ 👀😂", 0.9),
]

def sh(cmd, **kwargs):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)


def probe(path: Path):
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=width,height,codec_type",
            "-of", "json", str(path),
        ],
        text=True,
    )
    data = json.loads(out)
    duration = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": has_audio,
    }


def download_source(src):
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / (src["id"] + ".webm")
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest

    redirect = (
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/"
        + urllib.parse.quote(src["filename"])
    )
    req = Request(redirect, headers={"User-Agent": "comedy-remix-pipeline/1.0"})
    with urlopen(req, timeout=60) as r:
        payload = r.read()
    if len(payload) < 100_000:
        raise RuntimeError(f"Downloaded source is unexpectedly small: {src['id']}")
    dest.write_bytes(payload)
    return dest


def find_font(bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    raise FileNotFoundError("No Arabic-capable font found")


def shape_fa(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def fit_lines(draw, text, font, max_width):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), shape_fa(candidate), font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_caption_png(text, out_path, big=False):
    img = Image.new("RGBA", (680, 230 if big else 185), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(True), 52 if big else 45)
    lines = fit_lines(draw, text, font, 610)
    line_h = 64 if big else 56
    needed = max(1, len(lines)) * line_h + 38
    h = min(img.height, needed)
    # Rounded translucent plate.
    draw.rounded_rectangle((8, 8, 672, h - 8), radius=28, fill=(0, 0, 0, 205))
    y = (h - len(lines) * line_h) / 2 - 3
    for line in lines[:3]:
        s = shape_fa(line)
        bbox = draw.textbbox((0, 0), s, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((680 - tw) / 2, y), s, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.crop((0, 0, 680, h)).save(out_path)


def make_title_card(text, out_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), (17, 17, 17))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(True), 60)
    lines = fit_lines(draw, text, font, 620)
    y = 490 - (len(lines) * 70) / 2
    for line in lines[:3]:
        s = shape_fa(line)
        bbox = draw.textbbox((0, 0), s, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((WIDTH - tw) / 2, y), s, font=font, fill=(255, 255, 255))
        y += 70
    img.save(out_path)


def has_sound(path):
    return probe(path)["has_audio"]


def render_scene(src_path, src, caption_png, out_path):
    info = probe(src_path)
    if info["duration"] < src["start"] + 0.8:
        raise RuntimeError(f"Requested cut is beyond source duration: {src['id']}")
    if info["width"] < int(src.get("min_width", 720)):
        raise RuntimeError(f"Source below minimum width: {src['id']} ({info['width']})")

    dur = float(src["duration"])
    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p"
    )
    # Slow 1% zoom keeps static-feeling scenes lively without hiding the subject.
    video_filter += ",zoompan=z='min(zoom+0.0007,1.03)':d=1:s=720x1280:fps=30"

    base_args = [
        "ffmpeg", "-y",
        "-ss", str(src["start"]),
        "-t", str(dur),
        "-i", str(src_path),
        "-loop", "1", "-i", str(caption_png),
    ]
    if has_sound(src_path):
        args = base_args + [
            "-filter_complex", f"[0:v]{video_filter}[v];[1:v]format=rgba[cap];[v][cap]overlay=20:850:shortest=1[vout]",
            "-map", "[vout]", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
            "-t", str(dur), "-movflags", "+faststart", str(out_path),
        ]
    else:
        args = base_args + [
            "-f", "lavfi", "-t", str(dur), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", f"[0:v]{video_filter}[v];[1:v]format=rgba[cap];[v][cap]overlay=20:850:shortest=1[vout]",
            "-map", "[vout]", "-map", "2:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
            "-t", str(dur), "-movflags", "+faststart", str(out_path),
        ]
    sh(args)


def render_title_card(text, seconds, out_path):
    card = out_path.with_suffix(".png")
    make_title_card(text, card)
    sh([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(card),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(seconds),
        "-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest", "-movflags", "+faststart", str(out_path),
    ])


def concat(parts, final_path):
    list_file = final_path.parent / "concat.txt"
    list_file.write_text(
        "".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n" for p in parts),
        encoding="utf-8",
    )
    sh([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(final_path),
    ])


def duration(path):
    return probe(path)["duration"]


def validate(final_path, sources_used):
    info = probe(final_path)
    problems = []
    if info["width"] != WIDTH or info["height"] != HEIGHT:
        problems.append("wrong canvas")
    if not (MIN_TOTAL <= info["duration"] <= MAX_TOTAL):
        problems.append(f"duration={info['duration']:.2f}s")
    if final_path.stat().st_size > 50 * 1024 * 1024:
        problems.append("file > 50MB")
    if len(sources_used) < 3:
        problems.append("fewer than 3 source scenes")
    return info, problems


STATE_PATH = Path("comedy_state/posted.json")


def load_published_combinations():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {str(x) for x in data.get("keys", [])}
    except Exception:
        return set()


def combination_key(sources):
    return "animal:" + "|".join(sorted(str(src["id"]) for src in sources))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    run_dir = OUT / ("comedy_" + str(int(os.environ.get("GITHUB_RUN_ID", "1"))))
    run_dir.mkdir(parents=True, exist_ok=True)

    hook_text, hook_sec = random.choice(HOOKS)
    end_text, end_sec = random.choice(ENDS)

    history = load_published_combinations()

    # Pick a fresh 4-clip combination, not merely a fresh hook.
    # With 8 sources this gives 70 possible 4-clip combinations.
    combos = []
    for combo in itertools.combinations(SOURCES, 4):
        key = combination_key(combo)
        if key not in history:
            combos.append(combo)
    random.shuffle(combos)

    selected = []
    for combo in combos:
        trial = []
        ok = True
        for src in combo:
            try:
                p = download_source(src)
                info = probe(p)
                if info["width"] < int(src.get("min_width", 720)):
                    print("Skip low-res source:", src["id"], info)
                    ok = False
                    break
                if info["duration"] < src["start"] + src["duration"] + 0.2:
                    print("Skip too-short source:", src["id"], info)
                    ok = False
                    break
                trial.append((src, p))
            except Exception as exc:
                print("Source failed:", src["id"], exc)
                ok = False
                break
        if ok and len(trial) == 4:
            selected = trial
            break

    if len(selected) < 4:
        (run_dir / "skip.json").write_text(
            json.dumps(
                {"reason": "not_enough_valid_sources", "selected": [x[0]["id"] for x in selected]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Comedy build skipped:", [x[0]["id"] for x in selected])
        return 0

    parts = []
    # Hook
    hook_mp4 = run_dir / "00_hook.mp4"
    render_title_card(hook_text, hook_sec, hook_mp4)
    parts.append(hook_mp4)

    sources_meta = []
    for idx, (src, source_path) in enumerate(selected, start=1):
        cap_png = run_dir / f"{idx:02d}_caption.png"
        scene_mp4 = run_dir / f"{idx:02d}_scene.mp4"
        make_caption_png(src["caption"], cap_png)
        render_scene(source_path, src, cap_png, scene_mp4)
        parts.append(scene_mp4)
        sources_meta.append({
            "id": src["id"],
            "title": src["title"],
            "author": src["author"],
            "license": src["license"],
            "page": src["page"],
            "filename": src["filename"],
            "start": src["start"],
            "duration": src["duration"],
        })

    end_mp4 = run_dir / "99_end.mp4"
    render_title_card(end_text, end_sec, end_mp4)
    parts.append(end_mp4)

    final_path = run_dir / "video.mp4"
    concat(parts, final_path)
    info, problems = validate(final_path, sources_meta)

    metadata = {
        "content_type": "comedy_remix",
        "quality_gate": "passed_publish" if not problems else "failed",
        "script_quality": "passed",
        "display_title_fa": hook_text,
        "caption": " | ".join([hook_text] + [s["id"] for s in sources_meta]),
        "duration_seconds": round(info["duration"], 3),
        "width": info["width"],
        "height": info["height"],
        "content_key": "comedy:" + "-".join(s["id"] for s in sources_meta) + ":" + hook_text,
        "combination_key": combination_key([s for s, _ in selected]),
        "originality": {
            "voice": "none",
            "original_persian_captions": True,
            "new_edit_structure": True,
            "new_vertical_reframing": True,
            "synthetic_title_cards": True,
        },
        "sources": sources_meta,
        "validation_problems": problems,
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    attribution = [
        "All source media below were selected from Wikimedia Commons pages that explicitly state CC0.",
        "This output is an original edit: four short animal scenes, original Persian captions, vertical reframing and title cards.",
        "",
    ]
    for s in sources_meta:
        attribution.append(f"- {s['filename']} — {s['license']} — {s['author']} — {s['page']}")
    (run_dir / "attribution.txt").write_text("\n".join(attribution) + "\n", encoding="utf-8")

    if problems:
        print("QUALITY GATE FAILED:", problems)
        return 0
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
