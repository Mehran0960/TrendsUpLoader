"""Trend discovery and text-only review queue. This module does not build or download video."""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

FEED_PATH = Path("attention_state/public_radar.json")
STATE_PATH = Path("attention_state/trend_review_queue_state.json")
OUTPUT_PATH = Path("out/trend_review_queue.json")

ALLOWED = {
    "tech", "animals", "sports", "food", "human_funny", "wow",
    "satisfying", "travel", "cars", "talent", "beauty_style",
}
MIN_DEMAND_SCORE = 70.0
MAX_SIGNAL_AGE_HOURS = 72.0
MAX_FEED_AGE_HOURS = 18.0
DEFAULT_LIMIT = 5

UNSAFE_TITLE = re.compile(
    r"\b(politic(?:s|al)?|election|president|parliament|government|war|attack|terror|bomb|"
    r"murder|killed|death|violence|protest|disaster|earthquake|flood|suicide|self.?harm|porn|"
    r"sexual|gambling|casino)\b|انتخابات|رئیس.?جمهور|سیاست|جنگ|حمله|ترور|انفجار|قتل|"
    r"کشته|مرگ|خشونت|اعتراض|زلزله|سیل|خودکشی|خودآزاری|پورن|قمار",
    re.IGNORECASE,
)

TITLE_RULES = [
    ("animals", ["cat", "kitten", "dog", "puppy", "animal", "pet", "shark", "whale",
                 "swan", "wildlife", "penguin", "dolphin", "turtle", "snake", "eagle",
                 "owl", "wolf", "fox", "bear", "lion", "tiger", "elephant", "duck",
                 "parrot", "squirrel", "otter", "seal", "fish", "frog", "crocodile"]),
    ("tech", ["artificial intelligence", "which ai", "ai ", " ai", "chatgpt", "claude",
              "gemini", "robot", "iphone", "android", "nvidia", "gpu", "software",
              "computer", "technology", "tech ", "tech:", "coding"]),
    ("sports", ["football", "soccer", "basketball", "tennis", "goal", "nba", "fifa",
                "ufc", "sport", "skateboard", "surf", "runner", "running", "marathon",
                "athlete", "sprinter", "gymnast", "race", "backflip", "racing"]),
    ("food", ["food", "cooking", "recipe", "chef", "kitchen", "street food", "cake",
              "dessert", "pizza", "sushi", "apple browning"]),
    ("cars", ["car", "cars", "automotive", "drift", "vehicle", "supercar", "truck", "motorcycle"]),
    ("satisfying", ["satisfying", "restoration", "restore", "cleaning", "before after", "polishing"]),
    ("travel", ["travel", "trip", "destination", "vacation", "hotel", "beach", "mountain",
                "island", "landscape", "locals in"]),
    ("talent", ["singing", "singer", "vocal", "drummer", "drumming", "guitar", "piano", "talent"]),
    ("beauty_style", ["fashion", "beauty", "model", "dance", "makeup", "outfit", "dress", "clothing"]),
    ("human_funny", ["funny", "fail", "prank", "reaction", "awkward", "comedy", "laugh", "meme"]),
    ("wow", ["amazing", "skill", "trick", "acrobat", "stunt", "magic", "flip", "jump", "cheated a race"]),
]

CATEGORY_ID_FALLBACK = {
    "15": "animals",
    "17": "sports",
    "23": "human_funny",
    "28": "tech",
}


def infer_category(title, existing_category="", chart_category_id=""):
    """Prefer cues in the specific video's title over potentially noisy descriptions."""
    text = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    for category, keywords in TITLE_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    existing = str(existing_category or "").strip().lower()
    if existing in ALLOWED and existing != "other":
        return existing
    return CATEGORY_ID_FALLBACK.get(str(chart_category_id or ""), "")


def parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def select_trends(feed, state=None, now=None, limit=DEFAULT_LIMIT):
    """Select fresh, high-demand public trend signals; never fetches audiovisual media."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(feed, dict) or not isinstance(feed.get("signals"), list):
        return [], "trend_radar_feed_invalid"

    feed_updated = parse_timestamp(feed.get("updated_at"))
    if not feed_updated:
        return [], "trend_radar_timestamp_invalid"
    if (now - feed_updated).total_seconds() > MAX_FEED_AGE_HOURS * 3600:
        return [], "trend_radar_feed_stale"

    state = state if isinstance(state, dict) else {}
    seen_ids = {str(x) for x in state.get("recent_signal_ids", []) if x}
    seen_urls = {str(x) for x in state.get("recent_urls", []) if x}
    eligible = []
    seen_this_run = set()

    for signal in feed.get("signals", []):
        if not isinstance(signal, dict):
            continue
        title = re.sub(r"\s+", " ", str(signal.get("title") or "")).strip()
        url = str(signal.get("url") or "").strip()
        signal_id = str(signal.get("id") or signal.get("video_id") or url).strip()
        if not title or not url.startswith("https://") or not signal_id:
            continue
        if signal_id in seen_ids or url in seen_urls or signal_id in seen_this_run:
            continue
        if UNSAFE_TITLE.search(title):
            continue

        category = infer_category(
            title,
            signal.get("category"),
            signal.get("chart_category_id"),
        )
        if category not in ALLOWED:
            continue
        try:
            demand = float(signal.get("demand_score") or 0)
        except (TypeError, ValueError, OverflowError):
            demand = 0.0
        if demand < MIN_DEMAND_SCORE:
            continue

        published_at = parse_timestamp(signal.get("published_at"))
        if not published_at:
            continue
        age_hours = (now - published_at).total_seconds() / 3600.0
        if age_hours < 0 or age_hours > MAX_SIGNAL_AGE_HOURS:
            continue

        try:
            views = max(0, int(signal.get("views") or 0))
        except (TypeError, ValueError, OverflowError):
            views = 0
        try:
            region_count = max(0, int(signal.get("chart_region_count") or 0))
        except (TypeError, ValueError, OverflowError):
            region_count = 0

        seen_this_run.add(signal_id)
        eligible.append({
            "signal_id": signal_id,
            "title": title[:220],
            "url": url,
            "category": category,
            "demand_score": round(demand, 2),
            "views": views,
            "chart_region_count": region_count,
            "published_at": published_at.isoformat(),
            "age_hours": round(age_hours, 1),
            "discovery_only": True,
            "media_downloaded": False,
            "public_publishing_attempted": False,
        })

    eligible.sort(
        key=lambda item: (
            item["demand_score"],
            item["chart_region_count"],
            -item["age_hours"],
            item["views"],
        ),
        reverse=True,
    )
    selected = eligible[:max(1, min(int(limit), 10))]
    if not selected:
        return [], "no_fresh_supported_trend_candidates"
    return selected, "ok"


def load_state(path=STATE_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def build_queue(feed=None, state=None, now=None, limit=DEFAULT_LIMIT):
    if feed is None:
        try:
            feed = json.loads(FEED_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {
                "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
                "status": "skipped",
                "reason": "trend_radar_feed_unavailable_or_invalid",
                "items": [],
            }
    items, reason = select_trends(feed, state=state, now=now, limit=limit)
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "status": "ready" if items else "skipped",
        "reason": reason,
        "count": len(items),
        "items": items,
        "scope": "trend_discovery_and_text_queue_only",
        "video_production": False,
        "media_downloaded": False,
        "public_publishing_attempted": False,
    }


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    queue = build_queue(state=load_state())
    OUTPUT_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": queue.get("status"),
        "reason": queue.get("reason"),
        "count": queue.get("count", 0),
        "titles": [item["title"] for item in queue.get("items", [])],
        "video_production": False,
        "media_downloaded": False,
        "public_publishing_attempted": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
