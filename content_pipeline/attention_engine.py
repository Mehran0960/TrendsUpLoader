#!/usr/bin/env python3
"""Attention Remix Engine.

Goal: build a reusable audience asset, not merely chase platform payouts.
Sources are discovered from Wikimedia Commons and restricted to CC0/public-domain
media. The engine tests multiple attention drivers with the same edit grammar.
"""
import hashlib
import html
import itertools
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

OUT = Path("out")
CACHE = Path("attention_sources")
STATE_PATH = Path("attention_state/posted.json")
WIDTH, HEIGHT, FPS = 720, 1280, 30
MIN_TOTAL, MAX_TOTAL = 11.0, 18.0

SEED = int(os.environ.get("GITHUB_RUN_ID", "1"))
random.seed(SEED * 7919)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSE = ("cc0", "public domain", "public domain mark")
BLOCK_TERMS = re.compile(
    r"\b(child|children|minor|teen|schoolgirl|schoolboy|explicit|pornographic|gore)\b",
    re.I,
)
BORING_TERMS = re.compile(
    r"\b(conference|lecture|webinar|meeting|committee|documentary|press conference|classroom)\b",
    re.I,
)

DISCOVERY = {
    "animals": ["funny cat", "funny dog", "cute animal", "animal fail"],
    "human_funny": ["funny people", "funny fail", "funny reaction", "awkward moment"],
    "beauty_style": ["woman dance", "woman fashion", "woman performance", "style performance"],
    "talent": ["woman singing", "women singing", "singing performance", "female drummer"],
    "wow": ["amazing skill", "acrobatics", "trick performance", "satisfying performance"],
}

CAPTIONS = {
    "animals": [
        "این موجودات اصلاً با منطق ما کار نمی‌کنن 😂",
        "فقط سه ثانیه نگاه کن… بعد قضاوت کن 😭",
        "وقتی حیوان خونگی‌ت از تو شخصیت بیشتری داره 😂",
        "این پایان اصلاً قابل پیش‌بینی نبود 👀",
    ],
    "human_funny": [
        "همه‌چی خوب بود… تا اینجا 😭",
        "با اعتمادبه‌نفس شروع شد، با فاجعه تموم شد 😂",
        "این دقیقاً همون لحظه‌ایه که نباید دوربین روشن باشه 😅",
        "برنامه: عالی. اجرا: خب… 😂",
    ],
    "beauty_style": [
        "وقتی فقط اومدی بدرخشی… و موفق هم شدی ✨",
        "این ورود، زیادی اعتمادبه‌نفس داشت 😏",
        "همین یه لحظه برای متوقف کردن اسکرول کافیه 👀",
        "بعضی‌ها اصلاً نیازی به معرفی ندارن…",
    ],
    "talent": [
        "صبر کن… این اجرا واقعیه؟ 😳",
        "اینجا دیگه فقط «خوب» نیست 🔥",
        "دو ثانیه اول رو از دست نده 👀",
        "این اجرا یه چیز دیگه‌ست 😮",
    ],
    "wow": [
        "اول نگاه کن، بعد بگو چطوری؟ 😳",
        "این حرکت مغز آدمو چند ثانیه قفل می‌کنه 🤯",
        "چند بار دیدمش و هنوز نفهمیدم 😭",
        "صبر کن ببین آخرش چی می‌شه 👀",
    ],
}

EXPERIMENTS = [
    ("animal_chaos", {"animals": 3}),
    ("human_fails", {"human_funny": 3}),
    ("beauty_style", {"beauty_style": 2, "talent": 1}),
    ("talent_show", {"talent": 3}),
    ("wow_moments", {"wow": 3}),
    ("mixed_fun", {"animals": 2, "human_funny": 1}),
]

# Small vetted fallback pool; dynamic discovery is preferred.
STATIC_SOURCES = [
    {"id":"cat_jumpscare","filename":"Cat Jumpscare.webm","page":"https://commons.wikimedia.org/wiki/File:Cat_Jumpscare.webm","license":"CC0","author":"Panini!","title":"Cat runs toward the camera","category":"animals","attention_score":96,"energy":4},
    {"id":"husky_howl","filename":"Howling Husky Dog.webm","page":"https://commons.wikimedia.org/wiki/File:Howling_Husky_Dog.webm","license":"CC0","author":"Klaudya Teodora","title":"Howling Husky Dog","category":"animals","attention_score":92,"energy":3},
    {"id":"cat_pigeon","filename":"Cat chasing a pigeon.webm","page":"https://commons.wikimedia.org/wiki/File:Cat_chasing_a_pigeon.webm","license":"CC0","author":"Jeromi Mikhael","title":"Cat fails to catch a pigeon","category":"animals","attention_score":94,"energy":4,"min_width":640},
    {"id":"phantom_cat","filename":"Cat with phantom forelimb.webmhd.webm","page":"https://commons.wikimedia.org/wiki/File:Cat_with_phantom_forelimb.webmhd.webm","license":"CC0","author":"AioftheStorm","title":"Cat with phantom forelimb","category":"animals","attention_score":89,"energy":4},
    {"id":"sophy_cat","filename":"Sophy the Cat is Really High On A Ledge.webm","page":"https://commons.wikimedia.org/wiki/File:Sophy_the_Cat_is_Really_High_On_A_Ledge.webm","license":"CC0","author":"PseudoSkull","title":"Sophy the Cat on a ledge","category":"animals","attention_score":90,"energy":3},
    {"id":"moomin_cat","filename":"Moomin1.webm","page":"https://commons.wikimedia.org/wiki/File:Moomin1.webm","license":"CC0","author":"Tet","title":"Female housecat playing with a cotton bud","category":"animals","attention_score":84,"energy":2},
    {"id":"medha_drummers","filename":"MeDha.webm","page":"https://commons.wikimedia.org/wiki/File:MeDha.webm","license":"CC0","author":"KhalidAlzeyoudi","title":"All-female drummer group performance","category":"talent","attention_score":88,"energy":4},
    {"id":"baile_square","filename":"Baile.webm","page":"https://commons.wikimedia.org/wiki/File:Baile.webm","license":"CC0","author":"Sebastian Enrique Tanca Huatuco","title":"Dance performance","category":"beauty_style","attention_score":82,"energy":3,"min_width":720},
    {"id":"dance_hackathon","filename":"Dance hackathon.webm","page":"https://commons.wikimedia.org/wiki/File:Dance_hackathon.webm","license":"CC0","author":"ErikaGuetti","title":"Hackathon celebration dance","category":"beauty_style","attention_score":79,"energy":3,"min_width":720},
]

def sh(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)

def probe(path):
    raw = subprocess.check_output(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-show_entries","stream=width,height,codec_type","-of","json",str(path)],
        text=True,
    )
    data = json.loads(raw)
    duration = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": audio,
    }

def strip_html_text(value):
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()

def api_get(params):
    req = Request(
        COMMONS_API + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent":"attention-remix-engine/2.0"},
    )
    with urlopen(req, timeout=35) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))

def discover_sources():
    found = {}
    for category, queries in DISCOVERY.items():
        for query in queries:
            try:
                data = api_get({
                    "action":"query","format":"json","generator":"search",
                    "gsrnamespace":6,"gsrsearch":query,"gsrlimit":12,
                    "prop":"imageinfo","iilimit":1,
                    "iiprop":"url|size|mime|mediatype|extmetadata",
                })
            except Exception as exc:
                print("Discovery failed:", query, exc)
                continue

            pages = ((data.get("query") or {}).get("pages") or {})
            for page in pages.values():
                infos = page.get("imageinfo") or []
                if not infos:
                    continue
                info = infos[0]
                mime = str(info.get("mime") or "").lower()
                mediatype = str(info.get("mediatype") or "").upper()
                if not (mime.startswith("video/") or mediatype == "VIDEO"):
                    continue

                ext = info.get("extmetadata") or {}
                license_name = strip_html_text(
                    (ext.get("LicenseShortName") or {}).get("value")
                ).lower()
                if not any(x in license_name for x in ALLOWED_LICENSE):
                    continue

                title = str(page.get("title") or "")
                desc = strip_html_text((ext.get("ImageDescription") or {}).get("value"))
                combined = (title + " " + desc).lower()
                if BLOCK_TERMS.search(combined) or BORING_TERMS.search(combined):
                    continue

                size = int(info.get("size") or 0)
                width = int(info.get("width") or 0)
                height = int(info.get("height") or 0)
                if size and size > 32_000_000:
                    continue
                if min(width, height) < 480:
                    continue

                download_url = str(info.get("url") or "")
                if not download_url:
                    continue

                page_url = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(
                    title.replace(" ", "_"), safe=":/()"
                )
                score = 58
                for word in ("funny","fail","reaction","cute","dance","woman","women",
                             "sing","performance","amazing","skill","trick","unexpected"):
                    if word in combined:
                        score += 5

                sid = "commons:" + hashlib.sha1(page_url.encode()).hexdigest()[:14]
                found[sid] = {
                    "id": sid,
                    "filename": title.split("File:",1)[-1],
                    "page": page_url,
                    "download_url": download_url,
                    "license": license_name,
                    "author": strip_html_text((ext.get("Artist") or {}).get("value")),
                    "title": strip_html_text(title.split("File:",1)[-1]),
                    "description": desc[:600],
                    "category": category,
                    "attention_score": min(score,99),
                    "energy": 4 if category in ("talent","wow") else 3,
                    "dynamic": True,
                }

    results = [x for x in found.values() if x["attention_score"] >= 65]
    results.sort(key=lambda x: x["attention_score"], reverse=True)
    print("Discovered attention sources:", len(results))
    return results[:60]

def visual_score(path):
    try:
        import cv2
    except Exception:
        return {"score": 0.0, "best_start": 0.0, "best_duration": 3.8}

    cap=cv2.VideoCapture(str(path))
    fps=float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration=total/fps if fps>0 else 0.0
    if duration < 3.9:
        return {"score": 0.0, "best_start": 0.0, "best_duration": max(0.0,duration)}

    samples=[]
    frame_index=0
    sample_step=max(1,int(round(fps/3.0)))  # ~3 samples/sec
    prev=None

    while True:
        ok,frame=cap.read()
        if not ok:
            break
        if frame_index % sample_step != 0:
            frame_index += 1
            continue
        small=cv2.resize(frame,(192,108),interpolation=cv2.INTER_AREA)
        gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        motion=0.0 if prev is None else float(cv2.absdiff(gray,prev).mean())/255.0
        sharp=float(cv2.Laplacian(gray,cv2.CV_64F).var())
        contrast=float(gray.std())/128.0
        samples.append((frame_index/fps,motion,min(sharp/180.0,1.0),min(contrast,1.0)))
        prev=gray
        frame_index += 1

    cap.release()
    if len(samples)<8:
        return {"score":0.0,"best_start":0.0,"best_duration":min(3.8,duration)}

    window=12
    best=None
    for i in range(0,len(samples)-window+1):
        chunk=samples[i:i+window]
        motion=sum(x[1] for x in chunk)/window
        sharp=sum(x[2] for x in chunk)/window
        contrast=sum(x[3] for x in chunk)/window
        score=100.0*(0.50*motion+0.32*sharp+0.18*contrast)
        start=chunk[0][0]
        if best is None or score>best[0]:
            best=(score,start)

    return {
        "score":round(min(best[0],99.0),2),
        "best_start":round(best[1],3),
        "best_duration":3.8
    }


def download_source(src):
    CACHE.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+","_",str(src["id"]))
    suffix = Path(str(src.get("filename") or ".webm")).suffix or ".webm"
    dest = CACHE / (safe_id + suffix)
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest

    url = str(src.get("download_url") or "").strip()
    if not url:
        url = "https://commons.wikimedia.org/wiki/Special:Redirect/file/" + urllib.parse.quote(str(src["filename"]))

    req = Request(url, headers={"User-Agent":"attention-remix-engine/2.0"})
    with urlopen(req, timeout=75) as r:
        data = r.read()
    if len(data) < 100_000:
        raise RuntimeError(f"Small download for {src['id']}")
    dest.write_bytes(data)
    return dest

def find_font():
    for p in [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(p).exists():
            return p
    raise FileNotFoundError("Arabic font missing")

def shape_fa(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return str(text)

def fit_lines(draw, text, font, max_width):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        box = draw.textbbox((0,0), shape_fa(candidate), font=font)
        if box[2] - box[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def caption_png(text, out_path, big=False):
    img = Image.new("RGBA",(700,250 if big else 205),(0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(), 54 if big else 46)
    lines = fit_lines(draw,text,font,650)
    line_h = 62 if big else 56
    total_h = max(1,len(lines))*line_h
    y = (img.height-total_h)/2 - 4

    for index,line in enumerate(lines[:3]):
        s=shape_fa(line)
        box=draw.textbbox((0,0),s,font=font,stroke_width=0)
        tw=box[2]-box[0]
        x=(img.width-tw)/2
        draw.text(
            (x+2,y+3),s,font=font,
            fill=(0,0,0,215),stroke_width=5,stroke_fill=(0,0,0,215)
        )
        fill=(255,255,255,255)
        draw.text(
            (x,y),s,font=font,fill=fill,
            stroke_width=3,stroke_fill=(0,0,0,255)
        )
        y += line_h

    img.save(out_path)

def clip_source(src, info, visual):
    item=dict(src)
    total=float(info["duration"])
    if total < 4.0:
        raise RuntimeError(f"Source too short: {src['id']} {total:.2f}s")

    clip_dur=min(3.8,max(3.2,total-0.35))
    max_start=max(0.0,total-clip_dur-0.05)
    best_start=float(visual.get("best_start") or 0.0)
    start=min(best_start,max_start)
    item["start"]=round(start,3)
    item["duration"]=round(clip_dur,3)
    item["visual_score"]=float(visual.get("score") or 0.0)
    return item


def render_scene(src_path, src, cap_path, out_path):
    info=probe(src_path)
    vf=(
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},boxblur=18:2[bg2];"
        f"[fg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},format=yuv420p[v]"
    )

    base=["ffmpeg","-y","-ss",str(src["start"]),"-t",str(src["duration"]),"-i",str(src_path),"-loop","1","-i",str(cap_path)]
    if info["has_audio"]:
        cmd=base+[
            "-filter_complex",f"{vf};[1:v]format=rgba[cap];[v][cap]overlay=10:900:shortest=1[vout]",
            "-map","[vout]","-map","0:a:0","-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","96k","-ar","44100","-t",str(src["duration"]),"-movflags","+faststart",str(out_path)
        ]
    else:
        cmd=base+[
            "-f","lavfi","-t",str(src["duration"]),"-i","anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex",f"{vf};[1:v]format=rgba[cap];[v][cap]overlay=10:900:shortest=1[vout]",
            "-map","[vout]","-map","2:a:0","-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","96k","-ar","44100","-t",str(src["duration"]),"-movflags","+faststart",str(out_path)
        ]
    sh(cmd)

def concat(parts, final_path):
    lst=final_path.parent/"concat.txt"
    lst.write_text("".join("file '"+p.resolve().as_posix().replace("'","'\\''")+"'\n" for p in parts),encoding="utf-8")
    sh(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),"-c","copy","-movflags","+faststart",str(final_path)])

def load_state():
    try:
        data=json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data,dict):
            return {
                "keys": {str(x) for x in data.get("keys",[])},
                "experiment_counts": {str(k): int(v) for k,v in (data.get("experiment_counts") or {}).items()},
            }
    except Exception:
        pass
    return {"keys":set(),"experiment_counts":{}}

def choose_experiment(counts):
    # Exploration first: choose the least-tested experiment, randomizing ties.
    min_count=min(counts.get(name,0) for name,_ in EXPERIMENTS)
    names=[name for name,_ in EXPERIMENTS if counts.get(name,0)==min_count]
    return random.choice(names)

def combo_key(sources):
    return "attention:"+"|".join(sorted(str(x["id"]) for x in sources))

def select_sources(pool, state):
    counts=state.get("experiment_counts",{})
    untested=min(counts.get(name,0) for name,_ in EXPERIMENTS)
    options=[x for x in EXPERIMENTS if counts.get(x[0],0)==untested]
    experiment,targets=random.choice(options)
    history=state.get("keys",set())

    candidates=list(pool)
    random.shuffle(candidates)
    candidates.sort(
        key=lambda x:float(x.get("attention_score") or 0)+random.random()*8,
        reverse=True,
    )

    selected=[]
    used=set()

    def fill_category(category, need):
        nonlocal selected
        choices=[x for x in candidates if x.get("category")==category and x.get("id") not in used]
        scored=[]
        for src in choices[:18]:
            try:
                path=download_source(src)
                info=probe(path)
                if info["width"]<int(src.get("min_width",480)):
                    continue
                visual=visual_score(path)
                total_score=0.58*float(visual["score"])+0.42*float(src.get("attention_score") or 0)
                if visual["score"]<26:
                    continue
                scored.append((total_score,src,path,info,visual))
            except Exception as exc:
                print("REJECT",src.get("id"),exc)

        scored.sort(key=lambda x:x[0],reverse=True)
        for total_score,src,path,info,visual in scored[:need]:
            prepared=clip_source(src,info,visual)
            prepared["combined_score"]=round(total_score,2)
            selected.append((prepared,path,info))
            used.add(src["id"])
            print("SELECT",experiment,category,prepared["id"],prepared["combined_score"],prepared["visual_score"])

    for category,need in targets.items():
        fill_category(category,int(need))

    # For sparse experiments, use only semantically adjacent buckets.
    fallback={
        "animal_chaos":["animals","human_funny"],
        "human_fails":["human_funny","animals"],
        "beauty_style":["beauty_style","talent"],
        "talent_show":["talent","beauty_style","wow"],
        "wow_moments":["wow","animals","human_funny"],
        "mixed_fun":["animals","human_funny","wow"],
    }
    for category in fallback.get(experiment,["animals","human_funny","wow"]):
        if len(selected)>=3:
            break
        fill_category(category,3-len(selected))

    if len(selected)<3:
        return [],experiment
    chosen=sorted(selected,key=lambda x:float(x[0].get("combined_score") or 0),reverse=True)[:3]
    key=combo_key([x[0] for x in chosen])
    if key in history:
        return [],experiment
    return chosen,experiment


def validate(path, sources):
    info=probe(path)
    problems=[]
    if info["width"]!=WIDTH or info["height"]!=HEIGHT:
        problems.append("wrong_canvas")
    if not MIN_TOTAL <= info["duration"] <= MAX_TOTAL:
        problems.append(f"duration={info['duration']:.2f}")
    if path.stat().st_size > 50*1024*1024:
        problems.append("file_too_large")
    scores=[float(x.get("attention_score") or 0) for x in sources]
    if scores and sum(scores)/len(scores) < 67:
        problems.append("attention_score_floor")
    return info,problems

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    run_dir=OUT/("attention_"+str(SEED))
    run_dir.mkdir(parents=True,exist_ok=True)

    discovered=discover_sources()
    pool={x["id"]:x for x in STATIC_SOURCES}
    for x in discovered:
        pool[x["id"]]=x

    state=load_state()
    selected,experiment=select_sources(list(pool.values()),state)
    if len(selected)<3:
        (run_dir/"skip.json").write_text(
            json.dumps({"reason":"not_enough_valid_sources","experiment":experiment,"history_count":len(state["keys"])},ensure_ascii=False,indent=2),
            encoding="utf-8"
        )
        return 0

    parts=[]
    meta=[]
    for i,(src,path,info) in enumerate(selected,1):
        src=dict(src)
        src["caption"]=random.choice(CAPTIONS.get(src.get("category"),CAPTIONS["wow"]))
        cap=run_dir/f"{i:02d}_caption.png"
        scene=run_dir/f"{i:02d}_scene.mp4"
        caption_png(src["caption"],cap,big=(i==1))
        render_scene(path,src,cap,scene)
        parts.append(scene)
        meta.append({
            "id":src["id"],"title":src["title"],"author":src.get("author",""),
            "license":src.get("license",""),"page":src["page"],"filename":src["filename"],
            "category":src.get("category"),"attention_score":src.get("attention_score"),"visual_score":src.get("visual_score"),"combined_score":src.get("combined_score"),
            "start":src["start"],"duration":src["duration"]
        })

    final=run_dir/"video.mp4"
    concat(parts,final)
    info,problems=validate(final,meta)

    metadata={
        "content_type":"attention_remix","experiment":experiment,
        "quality_gate":"passed_publish" if not problems else "failed",
        "script_quality":"passed",
        "display_title_fa":str(meta[0]["title"]),
        "duration_seconds":round(info["duration"],3),"width":info["width"],"height":info["height"],
        "content_key":"attention:"+"-".join(x["id"] for x in meta),
        "visual_scores":[x.get("visual_score") for x in meta],
        "combined_scores":[x.get("combined_score") for x in meta],
        "combination_key":combo_key([x[0] for x in selected]),
        "originality":{"voice":"none","original_persian_captions":True,"new_edit_structure":True,"new_vertical_reframing":True,"subject_preserving_background":True},
        "sources":meta,"validation_problems":problems,
        "actual_categories":[str(x.get("category")) for x in meta]
    }
    (run_dir/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")

    attribution=[
        "Sources were selected from Wikimedia Commons and filtered to CC0/public-domain licensing metadata.",
        "Output transformation: short excerpts, new sequencing, Persian captions and vertical reframing.",
        ""
    ]
    for x in meta:
        attribution.append(f"- {x['filename']} — {x['license']} — {x['author']} — {x['page']}")
    (run_dir/"attribution.txt").write_text("\n".join(attribution)+"\n",encoding="utf-8")

    print(json.dumps(metadata,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    sys.exit(main())
