#!/usr/bin/env python3
"""One-shot, stronger-hook video review for the user's explicitly named channels.

Uses the existing private-review quality gates. Public uploads are made only by
this one-shot workflow, not by the scheduled private-review sender.
"""
import os
import sys
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import content_pipeline.send_review_videos as sender

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_TARGETS = ("@TapTerend", "@trendup_channel")
PUBLIC_CAPTION_SOURCE_LIMIT = 780
successful_public = []
failed_public = []

# For this one-time sample, favor visible payoff / humor / surprise over newsy
# talking-head clips. This modifies only the sample ordering, not daily sends.
LANE_BONUS = {
    "comedy": 28,
    "animals": 26,
    "surprise_or_reveal": 24,
    "skill_or_spectacle": 23,
    "sports_or_athletic_skill": 22,
    "transformation_or_satisfying": 21,
    "puzzles_and_visual_illusions": 18,
    "human_reaction_or_relatable": 17,
    "diy_and_creativity": 14,
    "practical_tips_or_life_hacks": 13,
    "food_cooking": 10,
    "nature_and_wildlife": 10,
    "wholesome_or_heartwarming": 9,
    "music_or_dance": 8,
    "science_curiosity": 7,
    "technology_demo": -8,
    "travel_and_places": 7,
    "nostalgia_and_then_now": 6,
}


def engagement_rank(item):
    lanes = set(item.get("hook_signals") or [])
    base = float(item.get("follow_growth_score") or 0)
    bonus = max((LANE_BONUS.get(lane, 0) for lane in lanes), default=0)
    if "technology_demo" in lanes and not lanes.intersection({
        "comedy", "animals", "surprise_or_reveal", "skill_or_spectacle",
        "sports_or_athletic_skill", "transformation_or_satisfying",
        "human_reaction_or_relatable",
    }):
        bonus -= 15
    age = max(0.0, float(item.get("age_hours") or 9999))
    freshness_bonus = max(0.0, 8.0 * (1.0 - min(age / 24.0, 1.0)))
    views = max(0.0, float(item.get("views") or item.get("telegram_post_views") or 0))
    # Low, capped support from views; the main signal is subject/hook plus the
    # existing relevance score. View counts are platform-dependent and imperfect.
    view_bonus = min(6.0, views ** 0.5 / 100.0)
    return base + bonus + freshness_bonus + view_bonus


original_select = sender.select_media_candidates


def quality_test_select(items, reviewed, recent_topics=None, limit=None):
    # Ask the normal eligibility filter for a wider pool so a failed download
    # does not force us back to the weak first candidate.
    pool = original_select(
        items, reviewed, recent_topics=recent_topics,
        limit=max(int(limit or sender.MAX_TRIES), 30),
    )
    pool.sort(key=engagement_rank, reverse=True)
    print("QUALITY_TEST_RANKED_CANDIDATES")
    for item in pool[:12]:
        print({
            "title": str(item.get("title") or "")[:100],
            "url": item.get("url"),
            "score": item.get("follow_growth_score"),
            "age_hours": item.get("age_hours"),
            "lanes": list(item.get("hook_signals") or []),
            "engagement_rank": round(engagement_rank(item), 2),
        })
    return pool[:max(1, int(limit or sender.MAX_TRIES))]


def send_to_public_channel(path, item, target):
    caption = sender.render_caption(item)
    source_url = str(item.get("_canonical_media_url") or item.get("url") or "").strip()
    if source_url:
        caption = f"{caption}\n\nمنبع ویدئو: {source_url}"
    caption = caption[:PUBLIC_CAPTION_SOURCE_LIMIT]
    endpoint = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
    with open(path, "rb") as video:
        response = requests.post(
            endpoint,
            data={"chat_id": target, "caption": caption, "supports_streaming": "true"},
            files={"video": (os.path.basename(path), video, "video/mp4")},
            timeout=(30, 180),
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok or not payload.get("ok"):
        error_code = payload.get("error_code", response.status_code)
        description = str(payload.get("description") or "")[:180]
        raise RuntimeError(f"Telegram rejected target {target}: code={error_code}, {description}")
    return (payload.get("result") or {}).get("message_id")


original_send_video = sender.send_video


def send_private_then_public(path, item):
    # Never publish unless the video has first passed all private-review gates
    # and the internal review-chat upload succeeds.
    private_message_id = original_send_video(path, item)
    for target in PUBLIC_TARGETS:
        try:
            message_id = send_to_public_channel(path, item, target)
            successful_public.append({"target": target, "message_id": message_id})
            print(f"QUALITY_TEST_PUBLIC_VIDEO_SENT target={target} message_id={message_id}")
        except Exception as exc:
            failed_public.append({"target": target, "error": str(exc)[:240]})
            print(f"QUALITY_TEST_PUBLIC_VIDEO_FAILED target={target} error={type(exc).__name__}: {str(exc)[:240]}")
    return private_message_id


if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN is not configured.")

sender.MAX_SENDS = 1
sender.select_media_candidates = quality_test_select
sender.send_video = send_private_then_public

result = sender.main()
print("QUALITY_TEST_PUBLIC_DELIVERY_SUMMARY", {
    "sent": successful_public,
    "failed": failed_public,
    "target_count": len(PUBLIC_TARGETS),
})
if not successful_public:
    raise SystemExit(2)
if failed_public:
    raise SystemExit(3)
raise SystemExit(result)
