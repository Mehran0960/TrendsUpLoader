#!/usr/bin/env python3
"""Viral Radar + Licensed Acquisition Engine.

Goal: discover what people are actually watching, then acquire only media that
comes from a source with a usable redistribution license/API. Discovery and
acquisition are deliberately separated: platform popularity is a demand signal,
not permission to re-upload.
"""
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

OUT = Path("out")
CACHE = Path("attention_sources")
STATE_PATH = Path("attention_state/posted.json")
ENGINE_VERSION = "viral_radar_global_opportunity_v9_event_structure_test"
WIDTH, HEIGHT, FPS = 720, 1280, 30
MIN_TOTAL = 1.0
MAX_TOTAL = None
MIN_VISUAL_SCORE = 50.0
MIN_RELEVANCE_SCORE = 65.0
MIN_DEMAND_SCORE = 50.0
MIN_COMBINED_SCORE = 76.0
MIN_HOOK_SCORE = 63.0
MIN_METADATA_HOOK_SCORE = 45.0
MIN_HOOK_STRUCTURE_SCORE = 52.0
MAX_YOUTUBE_SIGNAL_AGE_DAYS = 30

SEED = int(os.environ.get("GITHUB_RUN_ID", "1"))
random.seed(SEED * 7919)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PIXABAY_API = "https://pixabay.com/api/videos/"
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
YOUTUBE_SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_APIS = ["https://www.googleapis.com/youtube/v3"]
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
PEXELS_API = "https://api.pexels.com/v1"
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
PIXABAY_CACHE = Path("attention_api_cache")
YOUTUBE_CACHE = Path("attention_api_cache/youtube")
PEXELS_CACHE = Path("attention_api_cache/pexels")
GDELT_CACHE = Path("attention_api_cache/gdelt")
PIXABAY_QUERIES = {
    "animals": ["animal fail", "funny animal", "animal reaction"],
    "human_funny": ["funny people", "funny fail", "funny reaction"],
    "beauty_style": ["woman dance", "woman fashion", "woman performance"],
    "talent": ["woman singing", "dance performance", "female drummer"],
    "wow": ["amazing skill", "acrobatics", "trick performance"],
    "sports": ["sports fail", "amazing goal", "trick shot"],
    "food": ["satisfying cooking", "street food", "food art"],
    "cars": ["car transformation", "car stunt", "car detail"],
    "satisfying": ["oddly satisfying", "restoration", "cleaning transformation"],
    "travel": ["amazing travel", "beautiful destination", "travel moment"],
    "tech": ["cool technology", "amazing gadget", "tech demo"],
}
YOUTUBE_QUERIES = {
    "animals": ["funny cat", "funny dog"],
    "human_funny": ["funny people", "funny fail"],
    "beauty_style": ["woman dance", "woman fashion"],
    "talent": ["woman singing", "dance performance"],
    "wow": ["amazing skill", "trick performance"],
    "sports": ["sports fail", "amazing goal", "trick shot"],
    "food": ["satisfying cooking", "street food", "food art"],
    "cars": ["car transformation", "car stunt", "car detail"],
    "satisfying": ["oddly satisfying", "restoration", "cleaning transformation"],
    "travel": ["amazing travel", "beautiful destination", "travel moment"],
    "tech": ["cool technology", "amazing gadget", "tech demo"],
}
CATEGORY_ANCHORS = {
    "animals": ["cat","kitten","dog","puppy","animal","pet","monkey","bird","toucan","horse","panda","rabbit"],
    "human_funny": ["funny","fail","prank","reaction","awkward","comedy","laugh","silly","meme"],
    "beauty_style": ["woman","women","fashion","beauty","model","dance","style","makeup","outfit","dress","glam"],
    "talent": ["singing","singer","vocal","drummer","drumming","guitar","piano","talent","performer","performance"],
    "wow": ["amazing","skill","trick","acrobat","acrobatics","stunt","magic","flip","jump"],
    "sports": ["football","soccer","basketball","tennis","goal","match","sport","skateboard","surf","ufc","nba"],
    "food": ["food","cooking","recipe","chef","kitchen","street","cake","dessert","pizza","sushi"],
    "cars": ["car","cars","automotive","drift","racing","vehicle","supercar","truck","motorcycle"],
    "satisfying": ["satisfying","restoration","restore","cleaning","polish","before","after","oddly"],
    "travel": ["travel","trip","destination","vacation","hotel","beach","mountain","island","landscape"],
    "tech": ["technology","tech","iphone","android","ai","robot","gadget","computer","phone","apple","samsung"],
}


def extract_category_query(title, category):
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", str(title or "").lower())
    if not words:
        return None

    anchors = set(CATEGORY_ANCHORS.get(category, []))
    anchor_positions = [i for i,w in enumerate(words) if w in anchors]
    if not anchor_positions:
        return None

    # Prefer one category anchor plus the nearest distinctive non-stopwords.
    pos = anchor_positions[0]
    candidates = []
    for i,w in enumerate(words):
        if w in YOUTUBE_STOPWORDS or w in anchors or len(w) < 3:
            continue
        distance = abs(i-pos)
        candidates.append((distance,i,w))
    candidates.sort()

    phrase_words = [words[pos]]
    for _,_,word in candidates[:2]:
        if word not in phrase_words:
            phrase_words.append(word)

    phrase = " ".join(phrase_words[:3]).strip()
    return phrase if len(phrase) >= 7 else None



HOOK_CUES = {
    "animals": ["funny","fail","reaction","chase","jump","attack","rescue","fight","catch","steal","escape","surprise","unexpected","unusual","strange","crazy","impossible"],
    "human_funny": ["funny","fail","prank","reaction","awkward","unexpected","surprise","embarrassing","crazy"],
    "beauty_style": ["transformation","transition","runway","dance","glow","makeup","before","after","outfit","performance"],
    "talent": ["singing","singer","high note","cover","drummer","drumming","guitar","piano","performance","incredible","insane"],
    "wow": ["amazing","impossible","record","trick","acrobat","acrobatics","stunt","magic","flip","jump","skill","crazy","insane"],
    "sports": ["goal","save","knockout","dunk","trick","flip","catch","shot","fail","win","crazy","amazing"],
    "food": ["giant","fire","carving","cheese pull","satisfying","impossible","perfect","street","art","transformation"],
    "cars": ["drift","stunt","transformation","restoration","supercar","luxury","racing","crazy","insane"],
    "satisfying": ["restoration","restore","cleaning","polish","peel","perfect","pressure","before","after","transformation","satisfying"],
    "travel": ["breathtaking","hidden","aerial","drone","extreme","secret","unexpected","beautiful","impossible"],
    "tech": ["robot","ai","future","invisible","demo","gadget","transformation","crazy","impossible","new"],
}
HOOK_EVENT_WEIGHT = {
    "animals": 0.50, "human_funny": 0.52, "beauty_style": 0.35,
    "talent": 0.45, "wow": 0.52, "sports": 0.52,
    "food": 0.30, "cars": 0.42, "satisfying": 0.30,
    "travel": 0.20, "tech": 0.35,
}
GENERIC_MEDIA_WORDS = {
    "video","videos","clip","footage","animal","animals","cat","dog","woman","women",
    "people","person","food","car","cars","technology","tech","squirrel","nature",
    "cute","beautiful","cool","scene","moment","performance"
}

YOUTUBE_STOPWORDS = {
    "the","and","that","this","with","from","for","you","your","are","was","were",
    "have","has","had","how","what","when","where","why","just","very","really",
    "video","videos","short","shorts","official","music","people","woman","women",
    "funny","dance","performance","amazing","skill","trick","cat","dog","ranking",
    "top","best","new","today","watch","viral","must","try","2026","update"
}

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
    "sports": ["sports fail", "amazing goal", "trick shot", "crazy sports moment"],
    "food": ["satisfying cooking", "street food", "food art", "amazing recipe"],
    "cars": ["car transformation", "car stunt", "car detail", "crazy car"],
    "satisfying": ["oddly satisfying", "restoration", "cleaning transformation", "before after"],
    "travel": ["amazing travel", "beautiful destination", "travel moment", "hidden place"],
    "tech": ["cool technology", "amazing gadget", "tech demo", "future technology"],
}

YOUTUBE_REGION_CODES = ["US", "GB", "CA", "IN", "BR", "DE", "TR"]
YOUTUBE_CHART_CATEGORY_IDS = ["15", "17", "23", "24", "26", "28"]
YOUTUBE_CATEGORY_NAMES = {
    "15": "animals",
    "17": "sports",
    "23": "human_funny",
    "19": "travel",
}


CAPTIONS = {
    "animals": [
        "این موجودات اصلاً با منطق ما کار نمی‌کنن",
        "فقط سه ثانیه نگاه کن… بعد قضاوت کن",
        "وقتی حیوان خونگی‌ت از تو شخصیت بیشتری داره",
        "این پایان اصلاً قابل پیش‌بینی نبود",
    ],
    "human_funny": [
        "همه‌چی خوب بود… تا اینجا",
        "با اعتمادبه‌نفس شروع شد، با فاجعه تموم شد",
        "این دقیقاً همون لحظه‌ایه که نباید دوربین روشن باشه",
        "برنامه: عالی. اجرا: خب…",
    ],
    "beauty_style": [
        "وقتی فقط اومدی بدرخشی… و موفق هم شدی",
        "این ورود، زیادی اعتمادبه‌نفس داشت",
        "همین یه لحظه برای متوقف کردن اسکرول کافیه",
        "بعضی‌ها اصلاً نیازی به معرفی ندارن…",
    ],
    "talent": [
        "صبر کن… این اجرا واقعیه؟",
        "اینجا دیگه فقط «خوب» نیست",
        "دو ثانیه اول رو از دست نده",
        "این اجرا یه چیز دیگه‌ست",
    ],
    "wow": [
        "اول نگاه کن، بعد بگو چطوری؟",
        "این حرکت مغز آدمو چند ثانیه قفل می‌کنه",
        "چند بار دیدمش و هنوز نفهمیدم",
        "صبر کن ببین آخرش چی می‌شه",
    ],
    "sports": [
        "این صحنه رو باید دوباره ببینی",
        "اینجا دیگه فقط شانس نیست",
        "فقط لحظه آخر رو از دست نده",
        "این حرکت اصلاً عادی نبود",
    ],
    "food": [
        "آخرش دقیقاً همون چیزی شد که فکر می‌کردی؟",
        "این صحنه برای عاشقان غذا خطرناکه!",
        "فقط نگاه کن تا آخرش",
        "این یکی واقعاً اشتها رو باز می‌کنه",
    ],
    "cars": [
        "این تغییر رو باید از نزدیک دید",
        "صبر کن آخرش ماشین رو ببینی",
        "این دیگه تیونینگ معمولی نیست",
        "چند ثانیه بیشتر نگاه کن… ارزشش رو داره",
    ],
    "satisfying": [
        "این یکی عجیب آرامش‌بخشه",
        "تا آخرش نگاه کن؛ بهترین بخش آخرشه",
        "چرا این‌قدر رضایت‌بخشه؟",
        "این صحنه رو نمی‌شه نصفه رها کرد",
    ],
    "travel": [
        "این منظره واقعاً واقعی به نظر نمیاد",
        "این مقصد رو ببین!",
        "بعضی جاها انگار برای فیلم ساخته شدن",
        "صبر کن نمای بعدی رو ببینی",
    ],
    "tech": [
        "این فناوری واقعاً عجیبه",
        "صبر کن ببین باهاش چه کار می‌کنن",
        "این دیگه گجت معمولی نیست",
        "چیزی که می‌بینی احتمالاً آینده‌ست",
    ],
}


EXPERIMENTS = [
    ("animal_chaos", {"animals": 3}),
    ("human_fails", {"human_funny": 3}),
    ("beauty_style", {"beauty_style": 2, "talent": 1}),
    ("talent_show", {"talent": 3}),
    ("wow_moments", {"wow": 3}),
    ("mixed_fun", {"animals": 2, "human_funny": 1}),
    ("sports_action", {"sports": 3}),
    ("satisfying_visuals", {"satisfying": 3}),
    ("food_craft", {"food": 3}),
    ("cars_motion", {"cars": 3}),
    ("global_mix", {"wow": 1, "satisfying": 1, "sports": 1}),
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

def pexels_api_get(endpoint, params=None):
    url = PEXELS_API + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = Request(url, headers={"Authorization": PEXELS_KEY, "User-Agent": "attention-remix-engine/pexels/1.0"})
    with urlopen(req, timeout=35) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def discover_pexels(category, trend_queries=None, limit=12):
    if not PEXELS_KEY:
        print("Pexels discovery skipped: API key missing.")
        return []

    PEXELS_CACHE.mkdir(parents=True, exist_ok=True)
    results = []
    queries = [str(q) for q in (trend_queries or [])[:3] if q]
    if not queries:
        queries = [str(x) for x in PIXABAY_QUERIES.get(category, [])[:2]]

    for query in queries:
        cache_file = PEXELS_CACHE / (hashlib.sha1((category + "|search|" + query).encode("utf-8")).hexdigest()[:18] + ".json")
        data = None
        if cache_file.exists() and __import__("time").time() - cache_file.stat().st_mtime <= 24 * 3600:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if data is None:
            try:
                data = pexels_api_get("/videos/search", {"query": query, "orientation": "portrait", "size": "medium", "per_page": min(limit, 20)})
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                print("Pexels search failed:", category, query, exc)
                continue

        for video in data.get("videos", []) or []:
            video_id = str(video.get("id") or "").strip()
            duration = float(video.get("duration") or 0)
            if not video_id or duration < MIN_TOTAL:
                continue
            files = video.get("video_files") or []
            usable = [x for x in files if str(x.get("link") or "").strip() and int(x.get("width") or 0) >= 480]
            if not usable:
                continue
            best_file = max(usable, key=lambda x: int(x.get("width") or 0))
            results.append({
                "id": "pexels:" + video_id,
                "provider": "pexels",
                "filename": f"pexels_{video_id}.mp4",
                "page": str(video.get("url") or ""),
                "download_url": str(best_file.get("link") or ""),
                "license": "Pexels license",
                "author": str((video.get("user") or {}).get("name") or ""),
                "title": "Pexels search",
                "description": query,
                "category": category,
                "attention_score": 82.0,
                "duration_total": duration,
                "min_width": 480,
                "width": int(best_file.get("width") or 0),
                "height": int(best_file.get("height") or 0),
                "search_query": query,
            })

    cache_file = PEXELS_CACHE / (hashlib.sha1((category + "|popular").encode("utf-8")).hexdigest()[:18] + ".json")
    data = None
    if cache_file.exists() and __import__("time").time() - cache_file.stat().st_mtime <= 24 * 3600:
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            data = None
    if data is None:
        try:
            data = pexels_api_get("/videos/popular", {"min_height": 480, "per_page": min(limit, 20)})
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print("Pexels popular feed failed:", exc)
            data = None

    for rank, video in enumerate((data or {}).get("videos", []) or []):
        video_id = str(video.get("id") or "").strip()
        duration = float(video.get("duration") or 0)
        if not video_id or duration < MIN_TOTAL:
            continue
        files = video.get("video_files") or []
        usable = [x for x in files if str(x.get("link") or "").strip() and int(x.get("width") or 0) >= 480]
        if not usable:
            continue
        best_file = max(usable, key=lambda x: int(x.get("width") or 0))
        results.append({
            "id": "pexels:" + video_id,
            "provider": "pexels",
            "filename": f"pexels_{video_id}.mp4",
            "page": str(video.get("url") or ""),
            "download_url": str(best_file.get("link") or ""),
            "license": "Pexels license",
            "author": str((video.get("user") or {}).get("name") or ""),
            "title": "Pexels Popular",
            "description": "",
            "category": category,
            "attention_score": max(80.0, 96.0 - rank),
            "duration_total": duration,
            "min_width": 480,
            "width": int(best_file.get("width") or 0),
            "height": int(best_file.get("height") or 0),
            "popular_rank": rank + 1,
        })

    dedup = {}
    for item in results:
        dedup[item["id"]] = item
    return list(dedup.values())[:limit]

def youtube_api_get(endpoint, params):
    last_error = None
    for base in YOUTUBE_APIS:
        try:
            req = Request(
                base + endpoint + "?" + urllib.parse.urlencode(params),
                headers={"User-Agent": "attention-remix-engine/youtube-trend/1.2"},
            )
            with urlopen(req, timeout=35) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_error = exc
            print("YouTube endpoint failed:", base + endpoint, type(exc).__name__, exc)
    raise last_error or RuntimeError("YouTube API request failed")


def parse_iso_duration(value):
    match = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
        str(value or "")
    )
    if not match:
        return 0.0
    hours = float(match.group(1) or 0)
    minutes = float(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    return hours * 3600.0 + minutes * 60.0 + seconds


def infer_viral_category(video_category_id, title, description=""):
    """Infer our content lane from semantic cues first; category IDs are fallback."""
    blob=(str(title or "")+" "+str(description or "")).lower()

    keyword_map=[
        ("animals", ["cat","kitten","dog","puppy","animal","pet","monkey","bird","toucan","horse","panda","rabbit"]),
        ("sports", ["football","soccer","basketball","tennis","goal","match","nba","fifa","ufc","sport","skateboard","surf"]),
        ("food", ["food","cooking","recipe","chef","kitchen","street food","cake","dessert","pizza","sushi"]),
        ("cars", ["car","cars","automotive","drift","racing","vehicle","supercar","truck","motorcycle"]),
        ("satisfying", ["satisfying","restoration","restore","cleaning","before after","oddly","polishing"]),
        ("travel", ["travel","trip","destination","vacation","hotel","beach","mountain","island","landscape"]),
        ("tech", ["technology","tech","iphone","android","ai","robot","gadget","computer","phone","apple","samsung"]),
        ("talent", ["singing","singer","vocal","drummer","drumming","guitar","piano","talent","performer"]),
        ("beauty_style", ["fashion","beauty","model","dance","style","makeup","outfit","dress","glam","clothing"]),
        ("human_funny", ["funny","fail","prank","reaction","awkward","comedy","laugh","silly","meme"]),
        ("wow", ["amazing","skill","trick","acrobat","acrobatics","stunt","magic","flip","jump"]),
    ]
    for category,words in keyword_map:
        if any(re.search(r"\b"+re.escape(w)+r"\b", blob) for w in words):
            return category

    fallback_map={"15":"animals","17":"sports","28":"tech"}
    return fallback_map.get(str(video_category_id), "other")


def discover_youtube_global_charts(limit_per_bucket=8):
    """Read current official YouTube mostPopular charts across regions/categories.

    This is demand intelligence only. No YouTube audiovisual media is downloaded.
    Requests are parallelized conservatively because videos.list is lightweight.
    """
    if not YOUTUBE_KEY:
        print("YouTube global chart radar skipped: API key missing.")
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone

    cache_dir=YOUTUBE_CACHE / "global_charts"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_bucket(region, category_id):
        cache_file=cache_dir / (
            hashlib.sha1((region+"|"+category_id).encode("utf-8")).hexdigest()[:18]+".json"
        )
        if cache_file.exists() and __import__("time").time()-cache_file.stat().st_mtime <= 24*3600:
            try:
                return region, category_id, json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            data=youtube_api_get("/videos", {
                "key":YOUTUBE_KEY,
                "part":"snippet,contentDetails,statistics",
                "chart":"mostPopular",
                "regionCode":region,
                "videoCategoryId":category_id,
                "maxResults":min(limit_per_bucket,50),
            })
            cache_file.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            return region, category_id, data
        except Exception as exc:
            print("YouTube global chart failed:",region,category_id,exc)
            return region, category_id, {"items":[]}

    raw_results=[]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures=[
            executor.submit(fetch_bucket,region,category_id)
            for region in YOUTUBE_REGION_CODES
            for category_id in YOUTUBE_CHART_CATEGORY_IDS
        ]
        for future in as_completed(futures):
            region,category_id,data=future.result()
            for rank,item in enumerate((data.get("items") or [])[:limit_per_bucket],start=1):
                video_id=str(item.get("id") or "").strip()
                snippet=item.get("snippet") or {}
                stats=item.get("statistics") or {}
                content=item.get("contentDetails") or {}
                duration=parse_iso_duration(content.get("duration"))
                if not video_id or duration < MIN_TOTAL:
                    continue

                title=str(snippet.get("title") or "").strip()
                description=str(snippet.get("description") or "").strip()
                published_at=str(snippet.get("publishedAt") or "").strip()
                views=max(0,int(stats.get("viewCount") or 0))
                likes=max(0,int(stats.get("likeCount") or 0))
                comments=max(0,int(stats.get("commentCount") or 0))
                like_rate=likes/max(1,views)
                comment_rate=comments/max(1,views)
                age_days=float(MAX_YOUTUBE_SIGNAL_AGE_DAYS)
                try:
                    dt=datetime.fromisoformat(published_at.replace("Z","+00:00"))
                    age_days=max(0.125,(datetime.now(timezone.utc)-dt).total_seconds()/86400.0)
                except Exception:
                    pass

                raw_results.append({
                    "provider":"youtube_global_chart",
                    "id":"youtube:"+video_id,
                    "video_id":video_id,
                    "title":title,
                    "description":description[:500],
                    "published_at":published_at,
                    "duration":duration,
                    "views":views,
                    "likes":likes,
                    "comments":comments,
                    "like_rate":round(like_rate,6),
                    "comment_rate":round(comment_rate,6),
                    "velocity":round(views/max(age_days,0.125),2),
                    "chart_rank":rank,
                    "chart_region":region,
                    "chart_category_id":category_id,
                    "category":infer_viral_category(category_id,title,description),
                    "url":"https://www.youtube.com/watch?v="+video_id,
                    "discovery_only":True,
                    "media_downloaded":False,
                })

    grouped={}
    for item in raw_results:
        key=item["id"]
        g=grouped.setdefault(key,{"item":item,"regions":set(),"ranks":[]})
        g["regions"].add(item["chart_region"])
        g["ranks"].append(int(item["chart_rank"]))
        if int(item["chart_rank"]) < int(g["item"].get("chart_rank",999)):
            g["item"]=item

    results=[]
    for g in grouped.values():
        item=dict(g["item"])
        region_count=len(g["regions"])
        avg_rank=sum(g["ranks"])/len(g["ranks"])
        rank_score=max(0.0,min(1.0,(51.0-avg_rank)/50.0))
        cross_region=min(1.0,region_count/4.0)
        freshness=0.5
        try:
            dt=datetime.fromisoformat(str(item.get("published_at","")).replace("Z","+00:00"))
            age=max(0.0,(datetime.now(timezone.utc)-dt).total_seconds()/86400.0)
            freshness=max(0.0,1.0-min(age/MAX_YOUTUBE_SIGNAL_AGE_DAYS,1.0))
        except Exception:
            pass
        engagement=min(1.0,
            3.5*min(float(item.get("like_rate") or 0),0.08)
            +4.0*min(float(item.get("comment_rate") or 0),0.02)
        )
        score=50.0+30.0*rank_score+12.0*cross_region+5.0*freshness+3.0*engagement
        item["chart_regions"]=sorted(g["regions"])
        item["chart_region_count"]=region_count
        item["chart_avg_rank"]=round(avg_rank,2)
        item["demand_score"]=round(min(100.0,score),2)
        results.append(item)

    results.sort(key=lambda x:float(x.get("demand_score") or 0),reverse=True)
    print(
        "YouTube global chart radar complete:",
        "raw=",len(raw_results),
        "unique=",len(results),
        "regions=",len(YOUTUBE_REGION_CODES),
        "buckets=",len(YOUTUBE_REGION_CODES)*len(YOUTUBE_CHART_CATEGORY_IDS),
    )
    return results[:120]


def discover_youtube(category, limit=16):
    """Discover both established and emerging short-form demand signals."""
    if not YOUTUBE_KEY:
        print("YouTube trend discovery skipped: API key missing.")
        return []

    results = []
    YOUTUBE_CACHE.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timedelta, timezone
    recent_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=MAX_YOUTUBE_SIGNAL_AGE_DAYS)
    ).isoformat().replace("+00:00", "Z")

    queries = [str(q) for q in YOUTUBE_QUERIES.get(category, [])[:3] if q]
    modes = [("viewCount", 8), ("date", 8)]
    seen_ids = set()

    for query in queries:
        for order, mode_limit in modes:
            cache_file = YOUTUBE_CACHE / (
                hashlib.sha1((category + "|" + query + "|" + order).encode("utf-8")).hexdigest()[:18] + ".json"
            )
            data = None
            if cache_file.exists():
                age = __import__("time").time() - cache_file.stat().st_mtime
                if age <= 24 * 3600:
                    try:
                        data = json.loads(cache_file.read_text(encoding="utf-8"))
                    except Exception:
                        data = None

            if data is None:
                try:
                    data = youtube_api_get("/search", {
                        "key": YOUTUBE_KEY,
                        "part": "snippet",
                        "q": query,
                        "type": "video",
                        "order": order,
                        "videoDuration": "short",
                        "safeSearch": "strict",
                        "publishedAfter": recent_cutoff,
                        "maxResults": min(mode_limit, 10),
                    })
                    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except Exception as exc:
                    print("YouTube search failed:", category, query, order, exc)
                    continue

            ids = [
                str(x.get("id", {}).get("videoId") or "").strip()
                for x in (data.get("items") or [])
            ]
            ids = [x for x in ids if x and x not in seen_ids]
            seen_ids.update(ids)
            if not ids:
                continue

            try:
                details = youtube_api_get("/videos", {
                    "key": YOUTUBE_KEY,
                    "part": "snippet,contentDetails,statistics",
                    "id": ",".join(ids[:50]),
                })
            except Exception as exc:
                print("YouTube details failed:", category, query, order, exc)
                continue

            for item in details.get("items", []) or []:
                video_id = str(item.get("id") or "").strip()
                snippet = item.get("snippet") or {}
                content = item.get("contentDetails") or {}
                stats = item.get("statistics") or {}
                duration = parse_iso_duration(content.get("duration"))
                if not video_id or duration < MIN_TOTAL:
                    continue

                title = str(snippet.get("title") or "").strip()
                description = str(snippet.get("description") or "").strip()
                published_at = str(snippet.get("publishedAt") or "").strip()
                views = max(0, int(stats.get("viewCount") or 0))
                likes = max(0, int(stats.get("likeCount") or 0))
                comments = max(0, int(stats.get("commentCount") or 0))

                age_days = float(MAX_YOUTUBE_SIGNAL_AGE_DAYS)
                try:
                    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    age_days = max(0.125, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
                except Exception:
                    pass

                velocity = views / age_days
                like_rate = likes / max(1, views)
                comment_rate = comments / max(1, views)

                social_raw = (
                    1.6 * __import__("math").log1p(views)
                    + 4.8 * __import__("math").log1p(likes)
                    + 3.2 * __import__("math").log1p(comments)
                    + 6.0 * __import__("math").log1p(velocity)
                    + 120.0 * min(like_rate, 0.08)
                    + 220.0 * min(comment_rate, 0.02)
                )

                results.append({
                    "provider": "youtube",
                    "id": "youtube:" + video_id,
                    "video_id": video_id,
                    "title": title,
                    "description": description[:500],
                    "published_at": published_at,
                    "duration": duration,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "velocity": round(velocity, 2),
                    "like_rate": round(like_rate, 6),
                    "comment_rate": round(comment_rate, 6),
                    "social_raw": social_raw,
                    "query": query,
                    "order_mode": order,
                    "category": category,
                    "url": "https://www.youtube.com/watch?v=" + video_id,
                    "discovery_only": True,
                    "media_downloaded": False,
                })

    # Normalize within this radar batch. Fresh/latest candidates are deliberately
    # retained alongside large established videos so the radar can catch breakouts.
    results.sort(key=lambda x: float(x.get("social_raw") or 0), reverse=True)
    n = len(results)
    for idx, item in enumerate(results):
        pct = 1.0 if n <= 1 else 1.0 - (idx / (n - 1)) * 0.55
        freshness = 100.0
        try:
            dt = datetime.fromisoformat(str(item.get("published_at","")).replace("Z","+00:00"))
            age = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
            freshness = max(20.0, 100.0 - (age / MAX_YOUTUBE_SIGNAL_AGE_DAYS) * 80.0)
        except Exception:
            pass

        velocity_signal = min(
            100.0,
            35.0 + 15.0 * __import__("math").log10(max(1.0, float(item.get("velocity") or 0)) + 1.0)
        )
        item["demand_score"] = round(
            50.0 + 28.0 * pct + 12.0 * (freshness / 100.0) + 10.0 * (velocity_signal / 100.0),
            2,
        )

    print("YouTube viral radar complete:", category, "signals=", len(results))
    return results[:limit]



def discover_google_news(category, seed_queries=None, limit=12):
    """Read recent Google News search RSS as a no-key broad-web attention signal."""
    queries = [str(q).strip() for q in (seed_queries or []) if q][:2]
    if not queries:
        queries = [str(q) for q in DISCOVERY.get(category, [])[:2]]

    signals = []
    for query in queries:
        cache_key = hashlib.sha1((category + "|" + query + "|google-news").encode("utf-8")).hexdigest()[:18]
        cache_file = GDELT_CACHE / ("google_" + cache_key + ".xml")
        raw = None

        if cache_file.exists() and __import__("time").time() - cache_file.stat().st_mtime <= 6 * 3600:
            try:
                raw = cache_file.read_text(encoding="utf-8")
            except Exception:
                raw = None

        if raw is None:
            try:
                params = urllib.parse.urlencode({
                    "q": query + " when:3d",
                    "hl": "en-US",
                    "gl": "US",
                    "ceid": "US:en",
                })
                req = Request(
                    "https://news.google.com/rss/search?" + params,
                    headers={"User-Agent": "attention-remix-engine/google-news-radar/1.0"},
                )
                with urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                GDELT_CACHE.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(raw, encoding="utf-8")
            except Exception as exc:
                print("Google News discovery failed:", category, query, exc)
                continue

        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            print("Google News XML parse failed:", category, query, exc)
            continue

        seen = set()
        for rank, item in enumerate(root.findall(".//item")[:limit]):
            title = str(item.findtext("title") or "").strip()
            link = str(item.findtext("link") or "").strip()
            pub = str(item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = str(source_el.text or "").strip() if source_el is not None else ""
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            signals.append({
                "provider": "google_news_rss",
                "category": category,
                "query": query,
                "rank": rank + 1,
                "title": title,
                "url": link,
                "domain": source,
                "published_at": pub,
                "web_attention_score": round(max(42.0, 92.0 - rank * 3.2), 2),
            })

    signals.sort(key=lambda x: float(x.get("web_attention_score") or 0), reverse=True)
    print("Google News radar complete:", category, "signals=", len(signals))
    return signals[:limit]


def discover_gdelt(category, seed_queries=None, limit=10):
    """Use GDELT as a broad-web attention signal, not as a media source.

    GDELT's timeline/article APIs expose recent news coverage volume and the
    articles driving spikes. This adds a second, independent web signal without
    scraping social platforms or downloading third-party media.
    """
    GDELT_CACHE.mkdir(parents=True, exist_ok=True)
    queries = [str(q).strip() for q in (seed_queries or []) if q][:3]
    if not queries:
        queries = [str(q) for q in DISCOVERY.get(category, [])[:2]]

    from datetime import datetime, timezone, timedelta
    signals = []

    for query in queries:
        key = hashlib.sha1((category + "|" + query + "|3d").encode("utf-8")).hexdigest()[:18]
        cache_file = GDELT_CACHE / (key + ".json")
        data = None
        if cache_file.exists() and __import__("time").time() - cache_file.stat().st_mtime <= 6 * 3600:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                data = None

        if data is None:
            try:
                cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", query).strip()
                terms = [x for x in cleaned.lower().split() if len(x) >= 4][:4]
                if len(terms) >= 2:
                    gdelt_query = "(" + " OR ".join(terms) + ")"
                elif terms:
                    gdelt_query = terms[0]
                else:
                    continue
                params = urllib.parse.urlencode({
                    "query": gdelt_query,
                    "mode": "artlist",
                    "format": "json",
                    "timespan": "3d",
                    "sort": "datedesc",
                    "maxrecords": 25,
                })
                req = Request(
                    "https://api.gdeltproject.org/api/v2/doc/doc?" + params,
                    headers={"User-Agent": "attention-remix-engine/gdelt-radar/1.0"},
                )
                with urlopen(req, timeout=35) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                print("GDELT discovery failed:", category, query, exc)
                continue

        articles = data.get("articles") or data.get("data") or []
        if isinstance(articles, dict):
            articles = articles.get("articles") or []
        if not isinstance(articles, list):
            articles = []

        # Article presence is a broad-web attention signal, while duplicate
        # headlines/domains are collapsed so one publisher cannot dominate.
        seen_titles = set()
        for rank, article in enumerate(articles[:limit]):
            title = str(article.get("title") or "").strip()
            url = str(article.get("url") or "").strip()
            domain = str(article.get("domain") or article.get("sourcecountry") or "").strip()
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            signals.append({
                "provider": "gdelt",
                "category": category,
                "query": query,
                "rank": rank + 1,
                "title": title,
                "url": url,
                "domain": domain,
                "published_at": str(article.get("seendate") or article.get("socialimage") or ""),
                "web_attention_score": round(max(45.0, 94.0 - rank * 4.0), 2),
            })

    # Prefer distinctive article phrases as acquisition seeds.
    signals.sort(key=lambda x: float(x.get("web_attention_score") or 0), reverse=True)
    return signals[:limit]


def derive_trend_queries(youtube_results, category, max_queries=4):
    """Extract clean topic phrases only from titles that support this category."""
    phrases = []
    category_fallbacks = [str(x) for x in YOUTUBE_QUERIES.get(category, [])[:3]]

    ranked = sorted(
        list(youtube_results or []),
        key=lambda x: float(x.get("demand_score") or x.get("web_attention_score") or 0),
        reverse=True,
    )

    for item in ranked:
        item_category = str(item.get("category") or "")
        if item_category and item_category != category:
            continue
        phrase = extract_category_query(str(item.get("title") or ""), category)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= max_queries:
            break

    for fallback in category_fallbacks:
        if fallback not in phrases and len(phrases) < max_queries:
            phrases.append(fallback)

    return phrases[:max_queries]


def choose_opportunity_category(global_signals, local_signals):
    """Choose the strongest currently observable content lane."""
    buckets = {}
    for item in list(global_signals or []) + list(local_signals or []):
        cat = str(item.get("category") or "")
        if cat not in DISCOVERY:
            continue
        buckets.setdefault(cat, []).append(item)

    scored = []
    for cat, items in buckets.items():
        items = sorted(items, key=lambda x: float(x.get("demand_score") or 0), reverse=True)
        top = items[:5]
        if not top:
            continue
        max_score = float(top[0].get("demand_score") or 0)
        avg_score = sum(float(x.get("demand_score") or 0) for x in top) / len(top)
        region_factor = max(
            float(x.get("chart_region_count") or 0) for x in top
        ) / 4.0
        regional = min(1.0, region_factor)
        support = min(1.0, len(items) / 8.0)
        score = 0.55*max_score + 0.25*avg_score + 10.0*regional + 10.0*support
        scored.append((score, cat, len(items), max_score, regional))

    if not scored:
        return None, 0.0, []

    scored.sort(reverse=True)
    best = scored[0]
    print(
        "Opportunity radar:",
        [
            {
                "category": x[1],
                "score": round(x[0],2),
                "signals": x[2],
                "max_demand": round(x[3],2),
                "max_region_count": round(x[4]*4.0,2),
            }
            for x in scored[:8]
        ],
    )
    return best[1], round(best[0],2), scored



def discover_pixabay(category, limit=15, trend_queries=None):
    if not PIXABAY_KEY:
        return []

    PIXABAY_CACHE.mkdir(parents=True, exist_ok=True)
    results = []
    queries = list(PIXABAY_QUERIES.get(category, [])[:2])
    for tq in (trend_queries or [])[:3]:
        if tq and tq not in queries:
            queries.append(tq)
    for query in queries:
        cache_file = PIXABAY_CACHE / (
            hashlib.sha1((category + "|" + query).encode("utf-8")).hexdigest()[:18] + ".json"
        )
        data = None
        if cache_file.exists():
            age = __import__("time").time() - cache_file.stat().st_mtime
            if age <= 24 * 3600:
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    data = None

        if data is None:
            try:
                params = {
                    "key": PIXABAY_KEY,
                    "q": query,
                    "lang": "en",
                    "order": "popular",
                    "safesearch": "true",
                    "per_page": min(20, max(12, limit)),
                    "min_width": 720,
                }
                data = api_get_pixabay(params)
                cache_file.write_text(
                    json.dumps(data, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:
                print("Pixabay discovery failed:", category, query, exc)
                continue

        for hit in data.get("hits", []) or []:
            video_id = str(hit.get("id") or "").strip()
            streams = hit.get("videos") or {}
            medium = streams.get("medium") or streams.get("small") or {}
            url = str(medium.get("url") or "").strip()
            duration = float(hit.get("duration") or 0)
            width = int(medium.get("width") or 0)
            height = int(medium.get("height") or 0)

            # We are explicitly looking for short-form-native material.
            if (
                not video_id
                or not url
                or duration < MIN_TOTAL
                or min(width, height) < 480
            ):
                continue

            tags = str(hit.get("tags") or "")
            combined = (tags + " " + str(hit.get("pageURL") or "")).lower()
            if BLOCK_TERMS.search(combined) or "logo" in combined or "celebrity" in combined:
                continue

            views = max(0, int(hit.get("views") or 0))
            downloads = max(0, int(hit.get("downloads") or 0))
            likes = max(0, int(hit.get("likes") or 0))
            comments = max(0, int(hit.get("comments") or 0))
            popularity = (
                2.0 * __import__("math").log1p(views)
                + 1.4 * __import__("math").log1p(downloads)
                + 4.0 * __import__("math").log1p(likes)
                + 2.5 * __import__("math").log1p(comments)
            )

            page_url = str(hit.get("pageURL") or f"https://pixabay.com/videos/id-{video_id}/")
            sid = "pixabay:" + video_id
            results.append({
                "id": sid,
                "provider": "pixabay",
                "filename": f"pixabay_{video_id}.mp4",
                "page": page_url,
                "download_url": url,
                "thumbnail_url": str(medium.get("thumbnail") or ""),
                "license": "Pixabay Content License",
                "author": str(hit.get("user") or ""),
                "title": str(tags.split(",")[0].strip() or "Pixabay video"),
                "description": tags,
                "category": category,
                "attention_raw": popularity,
                "pixabay_views": views,
                "pixabay_downloads": downloads,
                "pixabay_likes": likes,
                "pixabay_comments": comments,
                "energy": 4 if category in ("human_funny", "talent", "wow") else 3,
                "duration_total": duration,
                "min_width": 480,
            })

    if not results:
        return []

    # The API itself is sorted by popularity; we additionally use the returned
    # engagement signals so that repeated queries can be ranked consistently.
    dedup = {x["id"]: x for x in results}
    results = list(dedup.values())
    results.sort(
        key=lambda x: (
            float(x.get("attention_raw") or 0),
            float(x.get("pixabay_views") or 0),
            float(x.get("pixabay_likes") or 0),
        ),
        reverse=True,
    )

    n = len(results)
    for idx, item in enumerate(results):
        percentile = 1.0 if n == 1 else 1.0 - (idx / (n - 1)) * 0.45
        item["attention_score"] = round(65 + 34 * percentile, 2)

    return results[:limit]
def api_get_pixabay(params):
    req = Request(
        PIXABAY_API + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "attention-remix-engine/3.0"},
    )
    with urlopen(req, timeout=35) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def discover_sources(categories=None):
    """Discover only openly licensed Commons video for the active experiment."""
    found = {}
    active = set(categories or DISCOVERY.keys())
    for category, queries in DISCOVERY.items():
        if category not in active:
            continue
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

def trend_match_score(src, trend_queries=None):
    """Score semantic evidence from the acquired media metadata.

    The query used to retrieve a Pexels result is NOT treated as proof that
    the resulting clip itself contains that topic; otherwise retrieval would
    create a circular, inflated score.
    """
    queries = [str(q).strip().lower() for q in (trend_queries or []) if q]
    if not queries:
        return 50.0

    provider = str(src.get("provider") or "")
    page_slug = re.sub(r"[-_/]+", " ", str(src.get("page") or ""))
    description = str(src.get("description") or "")
    # Pexels search results currently use description to retain the query that
    # produced them; that query must never count as semantic evidence.
    if provider == "pexels" and src.get("search_query"):
        description = ""
    blob = (
        str(src.get("title") or "") + " " +
        description + " " +
        str(src.get("tags") or "") + " " +
        page_slug
    ).lower()

    best = 0.0
    for query in queries:
        qwords = [
            w for w in re.findall(r"[a-z0-9'-]{3,}", query)
            if w not in YOUTUBE_STOPWORDS
        ]
        if not qwords:
            continue
        hits = sum(1 for w in qwords if w in blob)
        coverage = hits / len(qwords)
        exact_bonus = 0.18 if query in blob else 0.0
        best = max(best, min(1.0, coverage + exact_bonus))

    if best <= 0:
        # Pexels search has already performed semantic retrieval, but its API
        # currently does not expose detailed video tags/statistics. Give it only
        # a modest prior rather than pretending the topic was verified in-frame.
        if provider == "pexels":
            return 58.0 if src.get("search_query") else 52.0
        return 35.0

    return round(45.0 + 50.0 * best, 2)



def source_relevance_score(src):
    category = str(src.get("category") or "")
    page_slug = re.sub(r"[-_/]+", " ", str(src.get("page") or ""))
    description = str(src.get("description") or "")
    if str(src.get("provider") or "") == "pexels" and src.get("search_query"):
        description = ""
    text_blob = (
        str(src.get("title") or "") + " " +
        description + " " +
        page_slug
    ).lower()

    keywords = {
        "animals": ["cat","dog","kitten","puppy","animal","pet","monkey","bird","horse","funny","cute"],
        "human_funny": ["funny","funniest","fail","failure","flop","reaction","prank","laugh","awkward","silly","people","person"],
        "beauty_style": ["woman","women","fashion","beauty","model","dance","style","makeup","performance"],
        "talent": ["sing","singer","singing","vocal","music","drum","drummer","guitar","dance","performance"],
        "wow": ["amazing","skill","trick","acrobat","acrobatics","stunt","jump","flip","magic","performance"],
        "sports": ["sport","sports","football","soccer","basketball","tennis","goal","match","nba","fifa","ufc","skateboard","surf"],
        "food": ["food","cooking","recipe","chef","kitchen","street","cake","dessert","pizza","sushi"],
        "cars": ["car","cars","automotive","drift","racing","vehicle","supercar","truck","motorcycle"],
        "satisfying": ["satisfying","restoration","restore","cleaning","polish","before","after","oddly"],
        "travel": ["travel","trip","destination","vacation","hotel","beach","mountain","island"],
        "tech": ["technology","tech","iphone","android","ai","robot","gadget","computer","phone"],
    }.get(category, [])

    if not keywords:
        return 50.0

    hits = sum(1 for word in keywords if word in text_blob)
    if hits == 0:
        if str(src.get("provider") or "") == "pexels":
            return 68.0 if src.get("search_query") else (70.0 if src.get("popular_rank") else 52.0)
        return 35.0
    return min(100.0, 55.0 + 10.0 * hits)

def orientation_score(width, height):
    """Score how naturally a source fits vertical short-form framing."""
    w=float(width or 0)
    h=float(height or 0)
    if w <= 0 or h <= 0:
        return 0.0
    ratio=w/h
    if 0.52 <= ratio <= 0.65:
        return 100.0
    if ratio < 0.52:
        return max(82.0, 100.0 - (0.52-ratio)*120.0)
    return max(35.0, 100.0 - (ratio-0.65)*70.0)


def duration_score(duration):
    """Prefer shorter clips, but never reject a longer one."""
    d=float(duration or 0)
    if d <= 0:
        return 0.0
    if d <= 8.0:
        return 100.0
    if d <= 15.0:
        return 96.0
    if d <= 30.0:
        return 90.0
    if d <= 60.0:
        return 82.0
    if d <= 120.0:
        return 72.0
    if d <= 180.0:
        return 62.0
    if d <= 300.0:
        return 50.0
    return 38.0



def visual_score(path):
    try:
        import cv2
    except Exception:
        return {"score": 0.0, "best_start": 0.0, "best_duration": 3.8}

    cap=cv2.VideoCapture(str(path))
    fps=float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration=total/fps if fps>0 else 0.0
    if duration < MIN_TOTAL:
        return {"score": 0.0, "best_start": 0.0, "best_duration": duration}

    samples=[]
    frame_index=0
    sample_step=max(1,int(round(fps/4.0)))
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
        sharp=min(float(cv2.Laplacian(gray,cv2.CV_64F).var())/180.0,1.0)
        contrast=min(float(gray.std())/64.0,1.0)
        edges=cv2.Canny(gray,60,150)
        edge_score=min(float((edges>0).mean())/0.18,1.0)

        hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
        saturation=min(float(hsv[:,:,1].mean())/128.0,1.0)
        brightness=float(hsv[:,:,2].mean())/255.0
        lighting_score=max(0.0,1.0-abs(brightness-0.58)/0.58)

        samples.append((
            frame_index/fps,motion,sharp,contrast,edge_score,saturation,lighting_score
        ))
        prev=gray
        frame_index += 1

    cap.release()
    if not samples:
        return {"score":0.0,"best_start":0.0,"best_duration":duration}

    # Adapt the window to the source length. Very short clips are evaluated
    # over their entire available duration.
    target_samples=max(2,int(round(min(3.8,max(0.6,duration))*4.0)))
    window=min(len(samples),max(2,target_samples))
    if window < 2:
        window=len(samples)

    best=None
    for i in range(0,len(samples)-window+1):
        chunk=samples[i:i+window]
        vals=[sum(x[j] for x in chunk)/window for j in range(1,7)]
        motion,sharp,contrast,edge,saturation,lighting=vals

        score=100.0*(
            0.22*min(motion*1.8,1.0)
            +0.20*sharp
            +0.16*contrast
            +0.16*edge
            +0.14*saturation
            +0.12*lighting
        )
        start=chunk[0][0]
        if best is None or score>best[0]:
            best=(score,start)

    motions=[float(x[1]) for x in samples]
    early=[float(x[1]) for x in samples if float(x[0]) < min(3.0,duration)]
    first=[float(x[1]) for x in samples if float(x[0]) < min(1.6,duration)]
    mean_motion=sum(motions)/max(1,len(motions))
    p95_motion=__import__("numpy").percentile(motions,95) if motions else 0.0
    early_p95=__import__("numpy").percentile(early,95) if early else p95_motion
    first_p95=__import__("numpy").percentile(first,95) if first else early_p95
    spike_share=sum(1 for x in motions if x>0.12)/max(1,len(motions))
    first_spike_share=sum(1 for x in first if x>0.12)/max(1,len(first))
    event_score=100.0*(
        0.25*min(mean_motion/0.12,1.0)
        +0.30*min(max(p95_motion-0.04,0.0)/0.10,1.0)
        +0.25*min(spike_share/0.25,1.0)
        +0.20*min(max(early_p95-0.04,0.0)/0.10,1.0)
    )
    first_event_score=100.0*(
        0.45*min(max(first_p95-0.04,0.0)/0.10,1.0)
        +0.35*min(first_spike_share/0.22,1.0)
        +0.20*min(sum(first)/max(1,len(first))/0.10,1.0)
    )

    # Hook structure: distinguish a real event arc from sustained generic motion.
    # Five temporal buckets let us reward either:
    #   1) an immediate shock/spike, or
    #   2) a buildup that culminates later.
    buckets=[float(__import__("numpy").mean(b)) for b in __import__("numpy").array_split(__import__("numpy").array(motions),5)]
    baseline=float(__import__("numpy").median(buckets[1:-1])) if len(buckets) >= 3 else mean_motion
    early_level=float(__import__("numpy").mean(buckets[:2]))
    late_level=float(__import__("numpy").mean(buckets[-2:]))
    peak=max(buckets) if buckets else mean_motion
    peak_idx=int(max(range(len(buckets)), key=lambda i:buckets[i])) if buckets else 0
    peak_without=max([buckets[i] for i in range(len(buckets)) if i != peak_idx] or [baseline])

    late_escalation=min(max((late_level-early_level)/0.08,0.0),1.0)
    peak_isolation=min(max((peak-peak_without)/0.08,0.0),1.0)
    instant_escalation=min(max((max(first or [0.0])-float(__import__("numpy").median(motions[1:] or motions)))/0.10,0.0),1.0)
    profile_spread=min(float(__import__("numpy").std(buckets))/0.045,1.0)
    payoff_direction=late_escalation if peak_idx >= 3 else 0.0
    shock_direction=instant_escalation if peak_idx <= 1 else 0.0

    hook_structure_score=100.0*max(
        0.60*shock_direction + 0.25*peak_isolation + 0.15*profile_spread,
        0.55*payoff_direction + 0.30*peak_isolation + 0.15*profile_spread,
    )

    return {
        "score":round(min(best[0],99.0),2),
        "best_start":round(best[1],3),
        "best_duration":round(min(3.8,duration),3),
        "hook_event_score":round(min(event_score,99.0),2),
        "hook_first_event_score":round(min(first_event_score,99.0),2),
        "hook_structure_score":round(min(hook_structure_score,99.0),2),
    }



def metadata_hook_score(src):
    category=str(src.get("category") or "")
    provider=str(src.get("provider") or "")
    description=str(src.get("description") or "")
    if provider == "pexels" and src.get("search_query"):
        description=""
    page_slug=re.sub(r"[-_/]+"," ",str(src.get("page") or ""))
    text_blob=" ".join([
        str(src.get("title") or ""),
        description,
        str(src.get("tags") or ""),
        page_slug,
    ]).lower()
    title_words=re.findall(r"[a-z0-9'-]{3,}",str(src.get("title") or "").lower())
    cues=HOOK_CUES.get(category,[])
    cue_hits=sum(1 for cue in cues if cue in text_blob)
    distinctive=[]
    for word in re.findall(r"[a-z0-9'-]{3,}",text_blob):
        if word in GENERIC_MEDIA_WORDS or word in YOUTUBE_STOPWORDS:
            continue
        if word not in distinctive:
            distinctive.append(word)

    score=30.0 + min(42.0, cue_hits*10.0) + min(18.0, len(distinctive)*4.0)
    if len(title_words) <= 2 and cue_hits == 0:
        score -= 22.0
    elif len(title_words) <= 2 and cue_hits == 1:
        score -= 8.0
    if provider == "pexels" and src.get("search_query") and cue_hits == 0:
        score -= 5.0
    return round(max(0.0,min(100.0,score)),2)

def source_hook_score(src, visual):
    category=str(src.get("category") or "")
    meta=metadata_hook_score(src)
    event=float(visual.get("hook_event_score") or 0.0)
    first_event=float(visual.get("hook_first_event_score") or 0.0)
    structure=float(visual.get("hook_structure_score") or 0.0)
    pop=float(src.get("attention_score") or 0.0)
    ew=float(HOOK_EVENT_WEIGHT.get(category,0.40))
    later_w=0.18 + 0.10*ew
    first_w=0.30
    structure_w=0.24
    meta_w=max(0.0,0.22-0.05*ew)
    pop_w=max(0.0,1.0-first_w-later_w-structure_w-meta_w)
    score=(
        first_w*first_event
        +later_w*event
        +structure_w*structure
        +meta_w*meta
        +pop_w*pop
    )

    # A generic stock clip cannot compensate for a weak event arc simply by
    # having lots of motion later in the clip.
    title_words=re.findall(r"[a-z0-9'-]{3,}",str(src.get("title") or "").lower())
    if structure < MIN_HOOK_STRUCTURE_SCORE and meta < 60.0:
        score=min(score,49.0)
    if meta < MIN_METADATA_HOOK_SCORE:
        score=min(score,50.0)
    if len(title_words) <= 1 and meta < 55.0 and first_event < 70.0:
        score=min(score,56.0)
    return round(max(0.0,min(100.0,score)),2)

def download_source(src):
    CACHE.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+","_",str(src["id"]))
    suffix = Path(str(src.get("filename") or ".webm")).suffix or ".mp4"
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
    img = Image.new("RGBA",(700,230 if big else 205),(0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(find_font(), 50 if big else 44)
    lines = [shape_fa(x) for x in fit_lines(draw,text,font,640)]
    line_h = 58 if big else 52
    total_h = max(1,len(lines))*line_h
    pad_x, pad_y = 18, 14
    y = (img.height-total_h)/2 - 2

    widths=[]
    for line in lines[:3]:
        box=draw.textbbox((0,0),line,font=font,stroke_width=0)
        widths.append(box[2]-box[0])

    max_w=min(660,max(widths) if widths else 0)
    panel_left=max(8,(700-max_w)/2-pad_x)
    panel_right=min(692,(700+max_w)/2+pad_x)
    panel_top=max(6,y-pad_y)
    panel_bottom=min(img.height-6,y+len(lines[:3])*line_h+pad_y)
    draw.rounded_rectangle(
        (panel_left,panel_top,panel_right,panel_bottom),
        radius=18,fill=(0,0,0,165)
    )

    for line in lines[:3]:
        box=draw.textbbox((0,0),line,font=font,stroke_width=0)
        tw=box[2]-box[0]
        x=(img.width-tw)/2
        draw.text(
            (x,y),line,font=font,
            fill=(255,255,255,255),
            stroke_width=2,stroke_fill=(0,0,0,255)
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
    # Fill the vertical canvas with the source itself. This avoids the
    # large blurred bands that previously made landscape clips look like
    # a padded presentation instead of native short-form content.
    vf=(
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p[v]"
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

def select_sources(pool, state, forced_experiment=None, forced_targets=None):
    counts=state.get("experiment_counts",{})
    untested=min(counts.get(name,0) for name,_ in EXPERIMENTS)
    options=[x for x in EXPERIMENTS if counts.get(x[0],0)==untested]
    experiment=forced_experiment or random.choice(options)[0]
    if forced_targets is not None:
        targets=forced_targets
    else:
        targets=next(t for name,t in EXPERIMENTS if name==experiment)
    history=state.get("keys",set())

    candidates=list(pool)
    evaluated=[]
    used=set()

    for category in targets:
        choices=[x for x in candidates if x.get("category")==category]
        by_provider={}
        for src in choices:
            provider=str(src.get("provider") or "unknown")
            by_provider.setdefault(provider,[]).append(src)

        shortlist=[]
        for provider, items in by_provider.items():
            for src in items:
                src["_pre_score"]=(
                    0.38*float(src.get("attention_score") or 0)
                    +0.27*float(src.get("relevance_score") or source_relevance_score(src))
                    +0.35*float(src.get("cross_web_score") or src.get("trend_match_score") or 50.0)
                )
            items.sort(key=lambda x:float(x.get("_pre_score") or 0),reverse=True)
            shortlist.extend(items[:10])

        shortlist.sort(key=lambda x:float(x.get("_pre_score") or 0),reverse=True)

        provider_limits={"pixabay":6,"pexels":6,"commons":5}
        provider_taken={}

        for src in shortlist:
            provider=str(src.get("provider") or "unknown")
            limit=provider_limits.get(provider,5)
            if provider_taken.get(provider,0)>=limit:
                continue
            provider_taken[provider]=provider_taken.get(provider,0)+1

            if src.get("id") in used:
                continue

            try:
                path=download_source(src)
                info=probe(path)
                if info["width"]<int(src.get("min_width",480)):
                    continue

                duration=float(info["duration"] or 0)
                if duration < MIN_TOTAL:
                    continue

                visual=visual_score(path)
                visual_score_value=float(visual.get("score") or 0)
                relevance=float(src.get("relevance_score") or source_relevance_score(src))
                demand=float(src.get("cross_web_score") or src.get("trend_match_score") or 50.0)
                orient=orientation_score(info["width"],info["height"])
                shortness=duration_score(duration)
                hook=source_hook_score(src,visual)
                hook_event=float(visual.get("hook_event_score") or 0.0)
                hook_first_event=float(visual.get("hook_first_event_score") or 0.0)
                hook_structure=float(visual.get("hook_structure_score") or 0.0)
                hook_meta=float(metadata_hook_score(src))

                if visual_score_value < MIN_VISUAL_SCORE:
                    print("REJECT quality floor",src["id"],
                          "provider=",provider,"visual=",round(visual_score_value,2),
                          "relevance=",round(relevance,2))
                    continue
                if relevance < MIN_RELEVANCE_SCORE:
                    print("REJECT relevance floor",src["id"],
                          "provider=",provider,"visual=",round(visual_score_value,2),
                          "relevance=",round(relevance,2))
                    continue
                if demand < MIN_DEMAND_SCORE:
                    print("REJECT demand-match floor",src["id"],
                          "provider=",provider,"demand=",round(demand,2),
                          "relevance=",round(relevance,2))
                    continue
                if hook_meta < MIN_METADATA_HOOK_SCORE:
                    print("REJECT semantic hook floor",src["id"],
                          "provider=",provider,
                          "meta=",round(hook_meta,2),
                          "required=",MIN_METADATA_HOOK_SCORE)
                    continue
                if hook_structure < MIN_HOOK_STRUCTURE_SCORE and hook_meta < 60.0:
                    print("REJECT hook structure floor",src["id"],
                          "provider=",provider,
                          "structure=",round(hook_structure,2),
                          "meta=",round(hook_meta,2))
                    continue
                if hook < MIN_HOOK_SCORE:
                    print("REJECT hook floor",src["id"],
                          "provider=",provider,
                          "hook=",round(hook,2),
                          "event=",round(hook_event,2),
                          "first_event=",round(hook_first_event,2),
                          "structure=",round(hook_structure,2),
                          "meta=",round(hook_meta,2))
                    continue

                # Blend category-level live demand with exact phrase evidence.
                category_demand=float(src.get("category_demand_score") or demand or 50.0)
                exact_match=float(src.get("trend_match_score") or 50.0)
                demand_blend=min(100.0, 0.70*category_demand + 0.30*exact_match)

                combined_score=(
                    0.27*demand_blend
                    +0.24*visual_score_value
                    +0.18*relevance
                    +0.10*float(src.get("attention_score") or 0)
                    +0.12*hook
                    +0.05*orient
                    +0.04*shortness
                )

                prepared=dict(src)
                prepared.pop("_pre_score",None)
                prepared["start"]=0.0
                prepared["duration"]=round(duration,3)
                prepared["visual_score"]=round(visual_score_value,2)
                prepared["hook_score"]=round(hook,2)
                prepared["hook_event_score"]=round(hook_event,2)
                prepared["hook_first_event_score"]=round(hook_first_event,2)
                prepared["hook_structure_score"]=round(hook_structure,2)
                prepared["hook_metadata_score"]=round(hook_meta,2)
                prepared["relevance_score"]=round(relevance,2)
                prepared["trend_match_score"]=round(float(src.get("trend_match_score") or 50.0),2)
                # Category demand is the main discovery signal; exact phrase
                # overlap is supporting evidence, not a hard requirement.
                prepared["cross_web_score"]=round(demand_blend,2)
                prepared["category_demand_score"]=round(category_demand,2)
                prepared["orientation_score"]=round(orient,2)
                prepared["shortness_score"]=round(shortness,2)
                prepared["combined_score"]=round(combined_score,2)

                evaluated.append((prepared,path,info))
                used.add(src["id"])

                print(
                    "EVALUATE",experiment,category,prepared["id"],
                    "provider=",provider,
                    "combined=",prepared["combined_score"],
                    "pop=",prepared.get("attention_score"),
                    "visual=",prepared["visual_score"],
                    "hook=",prepared["hook_score"],
                    "relevance=",prepared["relevance_score"],
                    "demand_match=",prepared["cross_web_score"],
                    "duration=",prepared["duration"],
                )
            except Exception as exc:
                print("REJECT",src.get("id"),"provider=",provider,exc)

    if not evaluated:
        return [],experiment

    evaluated.sort(key=lambda x:float(x[0].get("combined_score") or 0),reverse=True)

    for chosen in evaluated:
        key=combo_key([chosen[0]])
        if (
            key not in history
            and float(chosen[0].get("combined_score") or 0) >= MIN_COMBINED_SCORE
        ):
            return [chosen],experiment

    return [],experiment



def validate(path, sources):
    info=probe(path)
    problems=[]
    if info["width"]!=WIDTH or info["height"]!=HEIGHT:
        problems.append("wrong_canvas")
    if info["duration"] < MIN_TOTAL:
        problems.append(f"duration={info['duration']:.2f}")
    if path.stat().st_size > 50*1024*1024:
        problems.append("file_too_large")

    for x in sources:
        if float(x.get("visual_score") or 0) < MIN_VISUAL_SCORE:
            problems.append("visual_score_floor")
        if float(x.get("relevance_score") or 0) < MIN_RELEVANCE_SCORE:
            problems.append("relevance_score_floor")
        combined_floor = 70.0 if len(sources) > 1 else MIN_COMBINED_SCORE
        if float(x.get("combined_score") or 0) < combined_floor:
            problems.append("combined_score_floor")
    return info,problems


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    run_dir=OUT/("attention_"+str(SEED))
    run_dir.mkdir(parents=True,exist_ok=True)

    if not PIXABAY_KEY and not PEXELS_KEY:
        (run_dir/"skip.json").write_text(
            json.dumps(
                {
                    "reason":"LICENSED_ACQUISITION_KEYS_MISSING",
                    "message":"At least one licensed acquisition API key is required: PIXABAY_API_KEY or PEXELS_API_KEY."
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8"
        )
        print("Viral radar skipped: no licensed acquisition API key.")
        return 0

    state=load_state()

    # First discover current demand signals. Selection is opportunity-first:
    # experiments remain a fallback for periods with weak/no radar data.
    # YouTube is used for metadata/trend intelligence only; its media is never downloaded.
    youtube_signals = []
    global_youtube_signals = discover_youtube_global_charts(limit_per_bucket=8)
    youtube_signals.extend(global_youtube_signals)
    web_signals = []
    trend_queries_by_category = {}
    # Let the global chart radar choose the category first. Local search is
    # only run for the winning category, keeping API cost bounded.
    opportunity_category, opportunity_score, opportunity_ranked = choose_opportunity_category(
        global_youtube_signals, []
    )

    if opportunity_category:
        top_categories = [x[1] for x in opportunity_ranked[:3]]
        experiment = "global_opportunity:" + opportunity_category
        targets = {category: 1 for category in top_categories}
    else:
        counts=state.get("experiment_counts",{})
        least=min(counts.get(name,0) for name,_ in EXPERIMENTS)
        candidates_exp=[x for x in EXPERIMENTS if counts.get(x[0],0)==least]
        experiment,targets=random.choice(candidates_exp)

    local_probe = []
    for category in targets:
        local_probe.extend(discover_youtube(category, limit=10))
        yt = [x for x in local_probe if x.get("category") == category]
        global_cat = [x for x in global_youtube_signals if x.get("category") == category][:30]
        category_signals = sorted(
            yt + global_cat,
            key=lambda x: float(x.get("demand_score") or 0),
            reverse=True,
        )
        youtube_signals.extend(yt)
        yt_queries = derive_trend_queries(category_signals, category, max_queries=4)

        gdelt = discover_gdelt(category, seed_queries=yt_queries, limit=10)
        if gdelt:
            web_signals.extend(gdelt)
        else:
            google_news = discover_google_news(category, seed_queries=yt_queries, limit=12)
            web_signals.extend(google_news)

        combined_queries = list(yt_queries)
        active_web = gdelt if gdelt else google_news
        web_phrases = derive_trend_queries(active_web, category, max_queries=2)
        for phrase in web_phrases:
            if phrase not in combined_queries and len(combined_queries) < 5:
                combined_queries.append(phrase)

        trend_queries_by_category[category] = combined_queries[:5]

    # Then look for licensed short videos on Pixabay that match the social-demand signal.
    pool={}

    # Wikimedia Commons is an additional licensed-acquisition lane. Only
    # CC0/public-domain video is accepted, and its media is handled exactly
    # like Pixabay/Pexels by the same quality gate.
    commons_pool = discover_sources(targets.keys())
    for x in commons_pool:
        if str(x.get("category") or "") in targets:
            x["provider"] = "commons"
            x["attention_score"] = float(x.get("attention_score") or 65.0)
            x["license_verified"] = True
            pool[x["id"]] = x

    for category in targets:
        for x in discover_pixabay(
            category,
            limit=15,
            trend_queries=trend_queries_by_category.get(category),
        ):
            pool[x["id"]]=x
        pexels_items = discover_pexels(
            category,
            trend_queries=trend_queries_by_category.get(category),
            limit=12,
        )
        print("Pexels discovery complete:", category, "candidates=", len(pexels_items))
        for x in pexels_items:
            pool[x["id"]]=x

    # Attach live category demand and cross-platform web demand signals.
    opportunity_scores = {x[1]: float(x[0]) for x in opportunity_ranked}
    for item in pool.values():
        cat = str(item.get("category") or "")
        item["category_demand_score"] = round(
            max(50.0, min(100.0, opportunity_scores.get(cat, 50.0))),
            2,
        )
        queries = trend_queries_by_category.get(cat, [])
        item["trend_match_score"] = trend_match_score(item, queries)

        # A recent web spike can strengthen demand only when the candidate
        # actually matches the discovered topic; it cannot override quality.
        related_web = [
            str(x.get("title") or "")
            for x in web_signals
            if str(x.get("category") or "") == cat
        ][:5]
        web_match = trend_match_score(item, related_web) if related_web else 50.0
        item["web_signal_score"] = round(web_match, 2)
        if related_web:
            item["cross_web_score"] = round(
                0.70 * float(item.get("trend_match_score") or 50.0)
                + 0.30 * float(item.get("web_signal_score") or 50.0),
                2,
            )
        else:
            item["cross_web_score"] = round(
                float(item.get("trend_match_score") or 50.0),
                2,
            )

    (run_dir/"radar.json").write_text(
        json.dumps({
            "engine_version": ENGINE_VERSION,
            "discovery_platforms": ["youtube_search", "youtube_global_charts", "gdelt", "google_news_rss"],
            "acquisition_platforms": ["pixabay", "pexels", "wikimedia_commons"],
            "experiment": experiment,
            "opportunity_category": opportunity_category,
            "opportunity_score": opportunity_score,
            "trend_queries_by_category": trend_queries_by_category,
            "global_youtube_signal_count": len(global_youtube_signals),
            "top_global_youtube_signals": sorted(
                global_youtube_signals,
                key=lambda x: float(x.get("demand_score") or 0),
                reverse=True,
            )[:40],
            "web_signal_count": len(web_signals),
            "top_web_signals": sorted(
                web_signals,
                key=lambda x: float(x.get("web_attention_score") or 0),
                reverse=True,
            )[:20],
            "top_demand_signals": sorted(
                youtube_signals,
                key=lambda x: float(x.get("demand_score") or 0),
                reverse=True,
            )[:20],
            "licensed_candidate_count": len(pool),
            "licensed_candidates_by_provider": {
                p: sum(1 for x in pool.values() if x.get("provider") == p)
                for p in sorted(set(str(x.get("provider") or "") for x in pool.values()))
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Licensed acquisition pool:", len(pool), {
        "commons": sum(1 for x in pool.values() if x.get("provider")=="commons"),
        "pixabay": sum(1 for x in pool.values() if x.get("provider")=="pixabay"),
        "pexels": sum(1 for x in pool.values() if x.get("provider")=="pexels"),
    })

    if len(pool)<1:
        (run_dir/"skip.json").write_text(
            json.dumps(
                {
                    "reason":"no_licensed_short_candidates",
                    "experiment":experiment,
                    "candidate_count":0,
                    "targets":list(targets),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8"
        )
        print("Attention build skipped: no eligible short Pixabay candidates.")
        return 0

    selected,experiment=select_sources(
        pool.values(),
        state,
        forced_experiment=experiment,
        forced_targets=targets,
    )
    if len(selected)!=1:
        (run_dir/"skip.json").write_text(
            json.dumps(
                {
                    "reason":"no_new_valid_single_source",
                    "experiment":experiment,
                    "candidate_count":len(pool),
                    "history_count":len(state["keys"]),
                },
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )
        return 0

    src,path,info=selected[0]
    src=dict(src)
    src["caption"]=random.choice(
        CAPTIONS.get(src.get("category"),CAPTIONS["wow"])
    )

    cap=run_dir/"caption.png"
    scene=run_dir/"scene.mp4"
    caption_png(src["caption"],cap,big=True)
    render_scene(path,src,cap,scene)

    final=run_dir/"video.mp4"
    final.write_bytes(scene.read_bytes())

    meta=[{
        "id":src["id"],
        "title":src["title"],
        "author":src.get("author",""),
        "license":src.get("license",""),
        "page":src["page"],
        "filename":src["filename"],
        "category":src.get("category"),
        "provider":src.get("provider"),
        "attention_score":src.get("attention_score"),
        "visual_score":src.get("visual_score"),
        "hook_score":src.get("hook_score"),
        "hook_event_score":src.get("hook_event_score"),
        "hook_first_event_score":src.get("hook_first_event_score"),
        "hook_structure_score":src.get("hook_structure_score"),
        "hook_metadata_score":src.get("hook_metadata_score"),
        "relevance_score":src.get("relevance_score"),
        "shortness_score":src.get("shortness_score"),
        "combined_score":src.get("combined_score"),
        "trend_match_score":src.get("trend_match_score"),
        "cross_web_score":src.get("cross_web_score"),
        "category_demand_score":src.get("category_demand_score"),
        "start":0.0,
        "duration":src["duration"],
    }]

    info,problems=validate(final,meta)

    display_title_fa=src["caption"]
    metadata={
        "content_type":"viral_radar_licensed_single_video",
        "engine_version":ENGINE_VERSION,
        "experiment":experiment,
        "source_provider":str(src.get("provider") or ""),
        "selection_model":ENGINE_VERSION,
        "discovery_platforms":["youtube_search","youtube_global_charts","gdelt","google_news_rss"],
        "acquisition_platforms":["pixabay","pexels","wikimedia_commons"],
        "quality_gate":"passed_publish" if not problems else "failed",
        "script_quality":"passed",
        "display_title_fa":display_title_fa,
        "clip_count":1,
        "duration_seconds":round(info["duration"],3),
        "width":info["width"],
        "height":info["height"],
        "content_key":"attention:"+src["id"],
        "visual_scores":[src.get("visual_score")],
        "combined_scores":[src.get("combined_score")],
        "combination_key":combo_key([src]),
        "originality":{
            "voice":"none",
            "original_persian_captions":True,
            "new_edit_structure":False,
            "new_vertical_reframing":True,
            "subject_preserving_background":True,
            "full_source_preserved":True,
        },
        "sources":meta,
        "validation_problems":problems,
        "source_market_signals":[{
            "id":src.get("id"),
            "views":src.get("pixabay_views"),
            "downloads":src.get("pixabay_downloads"),
            "likes":src.get("pixabay_likes"),
            "comments":src.get("pixabay_comments"),
            "duration":src.get("duration"),
        }],
        "youtube_trend_signals":youtube_signals[:20],
        "web_attention_signals":web_signals[:30],
        "trend_queries_by_category":trend_queries_by_category,
        "actual_categories":[str(src.get("category"))],
        "licensed_acquisition_only":True,
        "web_discovery_only_signals":True,
        "youtube_media_downloaded":False,
    }

    (run_dir/"metadata.json").write_text(
        json.dumps(metadata,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

    if src.get("provider") == "pexels":
        attribution = [
            "Source: Pexels.",
            "Source license: Pexels license.",
            "Attribution: Pexels asks API users to show a prominent link to Pexels and credit the creator when possible.",
            f"- {src['filename']} — {src['license']} — {src['author']} — {src['page']}",
        ]
    elif src.get("provider") == "commons":
        attribution = [
            "Source: Wikimedia Commons.",
            f"Source license: {src.get('license')}.",
            f"- {src['filename']} — {src['license']} — {src['author']} — {src['page']}",
        ]
    else:
        attribution = [
            "Source: Pixabay.",
            "Source license: Pixabay Content License.",
            f"- {src['filename']} — {src['license']} — {src['author']} — {src['page']}",
        ]
    attribution.append(
        "Transformation: one complete licensed source retained, re-framed vertically, and given an original Persian on-screen caption."
    )
    (run_dir/"attribution.txt").write_text(
        "\n".join(attribution)+"\n",
        encoding="utf-8"
    )

    print(json.dumps(metadata,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    sys.exit(main())
