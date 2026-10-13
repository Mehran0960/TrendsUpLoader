#!/usr/bin/env python3
"""Download a small, private-review shortlist and send the actual video files to Telegram.

This is a curator-only step. It does not publish to public social platforms, and a
successful download does not grant rights to repost the original creator's work.
"""
import html
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image, ImageChops, ImageStat

try:
    from content_pipeline.follower_growth import annotate_candidate
except ModuleNotFoundError:
    from follower_growth import annotate_candidate

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "attention_state" / "social_discovery.json"
STATE = ROOT / "attention_state" / "social_discovery_notification_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_AGE_HOURS = 36.0
MAX_TELEGRAM_REVIEW_AGE_HOURS = 72.0
MIN_STALE_TELEGRAM_SCORE = 50.0
MIN_SCORE = 42.0
MAX_VIDEO_BYTES = 48 * 1024 * 1024
MAX_SENDS = 1
# Output smoke test requested 2026-10-11; keep persisted dedupe state intact.
MAX_TRIES = 12
SUPPORTED = {"telegram", "youtube", "tiktok", "instagram", "aparat", "x"}
# These platforms remain discoverable, but this runner currently cannot
# reliably acquire their video files: YouTube requires an anti-bot challenge,
# Instagram extraction failed for all sampled reels, TikTok returns a browser
# challenge, YouTube asks for sign-in, and recent Aparat candidates all fail in
# yt-dlp with "Unable to extract og:title". Keep them discovery-only until a
# supported public acquisition path works; do not use private cookies or
# bypass platform challenges.
UNAVAILABLE_FILE_RELAY_PLATFORMS = {"youtube", "instagram", "tiktok", "aparat"}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def canonical_media_url(url):
    """Normalize tracking variants so the same public post is reviewed only once."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parsed.path or "") or "/"
    if not host:
        return ""
    if host == "youtu.be":
        video_id = path.strip("/").split("/", 1)[0]
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    if host in {"youtube.com", "m.youtube.com"}:
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            match = re.match(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]+)", path)
            if match:
                video_id = match.group(1)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    if host == "instagram.com":
        match = re.match(r"^/(?:reel|reels)/([A-Za-z0-9_-]+)$", path, re.I)
        if match:
            path = "/reel/" + match.group(1)
    return f"https://{host}{path}"


def candidate_id(item):
    return str(item.get("id") or item.get("url") or "").strip()


def candidate_review_keys(item):
    url = str(item.get("url") or "").strip()
    canonical = canonical_media_url(url)
    keys = {candidate_id(item), url, canonical}
    if canonical:
        keys.add(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    return {key for key in keys if key}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def difference_hash(image):
    """Return a 64-bit perceptual hash, tolerant of minor encoding changes."""
    gray = image.convert("L").resize((9, 8))
    pixels = list(gray.getdata())
    value = 0
    bit = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            if pixels[row + x] > pixels[row + x + 1]:
                value |= 1 << bit
            bit += 1
    return f"{value:016x}"


def media_fingerprints_match(left, right, max_distance=8, min_matching_frames=4):
    """Detect exact files or the same clip re-encoded with small visual changes."""
    left_sha = str((left or {}).get("sha256") or "")
    right_sha = str((right or {}).get("sha256") or "")
    if left_sha and right_sha and left_sha == right_sha:
        return True

    a = [str(x) for x in (left or {}).get("frame_hashes", []) if re.fullmatch(r"[0-9a-fA-F]{16}", str(x))]
    b = [str(x) for x in (right or {}).get("frame_hashes", []) if re.fullmatch(r"[0-9a-fA-F]{16}", str(x))]
    if len(a) < min_matching_frames or len(b) < min_matching_frames:
        return False

    pairs = []
    for i, ah in enumerate(a):
        for j, bh in enumerate(b):
            distance = (int(ah, 16) ^ int(bh, 16)).bit_count()
            if distance <= max_distance:
                pairs.append((distance, i, j))
    pairs.sort()
    used_a, used_b = set(), set()
    for _distance, i, j in pairs:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
    return len(used_a) >= min_matching_frames


def duration_content_gate(item, quality):
    """Keep long clips only when duration and topic evidence justify the extra watch time."""
    if not quality or not quality.get("passed"):
        return False, "technical_qc_failed"
    try:
        duration = float(quality.get("duration_seconds") or 0)
        score = float(item.get("follow_growth_score") or 0)
    except (TypeError, ValueError):
        return False, "missing_duration_or_score"
    lanes = set(item.get("hook_signals") or [])
    # Very short clips have too little room for a payoff; technical clarity alone is not substance.
    if duration < 8.0:
        return False, "clip_too_short_for_substantive_content"
    # A channel-invite link plus a call-to-action is a promotional teaser, not a useful clip.
    source_text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "description", "source_post_caption", "cleaned_review_caption")
    )
    if CTA_INVITE_URL_RE.search(source_text) and CTA_INVITE_SIGNAL_RE.search(source_text):
        return False, "cta_or_invite_heavy_source_caption"
    if duration > 60:
        return False, "duration_over_60_seconds"
    if duration > 45 and (
        score < 60
        or len(lanes) < 2
        or not lanes.intersection(SPECIFIC_LONG_CLIP_LANES)
    ):
        return False, "long_clip_requires_stronger_topic_fit"
    return True, "duration_and_topic_gate_passed"


TARGET_LANES = {
    "comedy",
    "animals",
    "surprise_or_reveal",
    "transformation_or_satisfying",
    "skill_or_spectacle",
    "technology_demo",
    "human_reaction_or_relatable",
    "food_cooking",
    "practical_tips_or_life_hacks",
    "wholesome_or_heartwarming",
    "sports_or_athletic_skill",
    "nature_and_wildlife",
    "science_curiosity",
    "diy_and_creativity",
    "home_organization_or_cleaning",
    "music_or_dance",
    "fashion_or_cultural_showcase",
    "travel_and_places",
    "puzzles_and_visual_illusions",
    "nostalgia_and_then_now",
}

# Longer clips need at least one concrete subject lane; generic "surprising/reaction"
# wording alone should not be enough to justify the additional watch time.
SPECIFIC_LONG_CLIP_LANES = TARGET_LANES - {
    "surprise_or_reveal",
    "human_reaction_or_relatable",
}


CAPTION_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200e\u200f\u2060\ufeff\u00ad]")
CAPTION_URL_RE = re.compile(r"(?:https?://|www\.)\S+|(?:t\.me|eitaa\.com)/\S+", re.I)
CAPTION_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,}")
CAPTION_BRAND_TAG_RE = re.compile(
    r"#(?:جادوی[_\s]*تندیس|خلق[_\s]*طعم[_\s]*رؤیاها|لذت[_\s]*آشپزی)", re.I
)
CAPTION_CTA_TAIL_RE = re.compile(
    r"لینک\s+(?:زیر\s*)?(?:را\s*)?(?:رو\s*)?(?:بزن|کلیک(?:\s*کن(?:ید|ین))?|باز\s*کن(?:ید|ین))|"
    r"شعبه\s+جدید|کانال\s+(?:دوم|سوم)|پیج\s+اینستا|کانال\s+ایتا|"
    r"برای\s+عضویت|عضو\s+(?:شوید|بشید)|لینک\s+در\s+بیو|"
    r"subscribe|follow\s+for\s+more|link\s+in\s+bio|join\s+our\s+channel",
    re.I,
)
CTA_INVITE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)[A-Za-z0-9_-]+",
    re.I,
)
CTA_INVITE_SIGNAL_RE = re.compile(
    r"👇|⬇|برای\s+عضویت|عضو\s+(?:شوید|بشید)|join\s+(?:our\s+)?channel|"
    r"subscribe|لینک\s+(?:زیر|کانال)|کانال\s+(?:دوم|سوم)",
    re.I,
)
GENERIC_GREETING_CAPTION_RE = re.compile(
    r"روز(?:تون|تان| شما)?\s*(?:زیبا|عالی|خوب|پاییزی)|روز\s*پاییزی(?:تون)?|"
    r"صبح(?:تون)?\s*بخیر|شب(?:تون)?\s*بخیر|ان\s*شاءالله\s*امروز|"
    r"برای\s*تک\s*تک\s*دوستانم|پراز\s*(?:خیر\s*و\s*برکت|دلخوشی|موفقیت|محبت)|"
    r"در\s*پناه\s*خدا|روزتون.*آرزو",
    re.I,
)
CAPTION_SPECIFIC_LANES = SPECIFIC_LONG_CLIP_LANES - {"wholesome_or_heartwarming"}

# Three repeatable core pillars, one adjacent-content bucket, and a deliberately
# small exploration bucket. Percentages are rolling targets, not hard quotas.
PILLAR_TARGETS = {
    "reaction_and_comedy": 0.30,
    "visual_wow": 0.25,
    "curiosity_and_process": 0.15,
    "adjacent": 0.20,
    "experimental": 0.10,
}
PILLAR_PRIORITY = tuple(PILLAR_TARGETS)
EXPERIMENT_LANES = {
    "travel_and_places",
    "puzzles_and_visual_illusions",
    "nostalgia_and_then_now",
}
REACTION_LANES = {
    "comedy", "animals", "surprise_or_reveal", "human_reaction_or_relatable",
}
VISUAL_WOW_LANES = {
    "skill_or_spectacle", "sports_or_athletic_skill", "transformation_or_satisfying",
}
CURIOSITY_LANES = {"technology_demo", "science_curiosity"}


def content_pillar_profile(item):
    """Return the strategy bucket and stable subject key for one short-video candidate."""
    lanes = set(item.get("hook_signals") or [])
    # Keep topical experiments isolated even if they also match generic surprise/nature cues.
    for lane in ("travel_and_places", "puzzles_and_visual_illusions", "nostalgia_and_then_now"):
        if lane in lanes:
            return "experimental", lane
    # Cooking remains an adjacent lane even when the caption includes broad 'wow' language.
    if "food_cooking" in lanes:
        return "adjacent", "food_cooking"
    for pillar, ordered_lanes in (
        ("reaction_and_comedy", ("comedy", "animals", "human_reaction_or_relatable", "surprise_or_reveal")),
        ("visual_wow", ("skill_or_spectacle", "sports_or_athletic_skill", "transformation_or_satisfying")),
        ("curiosity_and_process", ("technology_demo", "science_curiosity")),
    ):
        for lane in ordered_lanes:
            if lane in lanes:
                return pillar, lane
    for lane in (
        "practical_tips_or_life_hacks", "nature_and_wildlife",
        "wholesome_or_heartwarming", "diy_and_creativity",
        "home_organization_or_cleaning", "music_or_dance",
        "fashion_or_cultural_showcase",
    ):
        if lane in lanes:
            return "adjacent", lane
    return "adjacent", next(iter(sorted(lanes)), "other")


def normalize_topic_history(history):
    """Keep only well-formed topic rows, newest last."""
    rows = []
    for row in history if isinstance(history, list) else []:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        pillar = str(row.get("pillar") or "").strip()
        if topic and pillar in PILLAR_TARGETS:
            rows.append({**row, "topic": topic, "pillar": pillar})
    return rows[-20:]


def infer_recent_topic_history(state, candidate_items):
    """Use persisted successful-send topics; bootstrap best-effort from known sent fingerprints."""
    saved = normalize_topic_history((state or {}).get("media_reviewed_topics", []))
    if saved:
        return saved[-19:]

    by_url = {}
    for item in candidate_items if isinstance(candidate_items, list) else []:
        if isinstance(item, dict) and item.get("url"):
            key = canonical_media_url(item.get("url"))
            if key:
                by_url[key] = item

    inferred = []
    fingerprints = (state or {}).get("media_reviewed_fingerprints", [])
    for fingerprint in fingerprints if isinstance(fingerprints, list) else []:
        if not isinstance(fingerprint, dict):
            continue
        item = by_url.get(canonical_media_url(fingerprint.get("url")))
        if not item:
            continue
        pillar, topic = content_pillar_profile(item)
        inferred.append({
            "pillar": pillar,
            "topic": topic,
            "url": str(fingerprint.get("url") or ""),
            "sent_at": "",
            "inferred": True,
        })
    return inferred[-19:]


def profile_food_is_not_food(item):
    return content_pillar_profile(item)[1] != "food_cooking"


def balanced_candidate_shortlist(eligible, recent_topics=None, limit=MAX_TRIES):
    """Order qualified videos towards the rolling 30/25/15/20/10 strategy without lowering quality."""
    remaining = list(eligible)
    history = normalize_topic_history(recent_topics or [])[-19:]
    selected = []
    planned = []
    food_in_batch = 0
    non_food_qualified_initial = sum(
        1 for row in remaining if profile_food_is_not_food(row)
    )

    def get_window():
        return (history + planned)[-19:]

    def profile(row):
        return content_pillar_profile(row)

    while remaining and len(selected) < max(0, int(limit)):
        current_window = get_window()
        current_food_count = sum(1 for row in current_window if row.get("topic") == "food_cooking")
        non_food_count = sum(1 for row in remaining if profile(row)[1] != "food_cooking")

        # Soft rolling cap: no more than 6 cooking videos per last 20 successful
        # sends when there are alternatives. Within a single 3-video batch, max
        # one cooking video when at least two non-cooking candidates are qualified.
        block_food_for_history = current_food_count >= 6 and non_food_qualified_initial >= MAX_SENDS
        block_food_for_batch = len(selected) < 3 and food_in_batch >= 1 and non_food_qualified_initial >= 2
        usable = [
            row for row in remaining
            if not (
                profile(row)[1] == "food_cooking"
                and (block_food_for_history or block_food_for_batch)
            )
        ]
        if not usable:
            # These are soft, not hard quotas: do not starve output if the only
            # qualified options are cooking videos.
            usable = remaining

        by_pillar = {}
        for row in usable:
            pillar, _topic = profile(row)
            by_pillar.setdefault(pillar, []).append(row)

        # Choose the bucket that most reduces the deviation from the target mix
        # after the next selection in the 20-video window.
        scored_pillars = []
        for pillar, rows in by_pillar.items():
            window = current_window + [{"pillar": pillar, "topic": profile(rows[0])[1]}]
            window = window[-20:]
            counts = {key: sum(1 for row in window if row.get("pillar") == key) for key in PILLAR_TARGETS}
            size = len(window)
            error = sum(abs(counts[key] - PILLAR_TARGETS[key] * size) for key in PILLAR_TARGETS)
            # Ties preserve the core-first strategy (then adjacent, then experiments).
            scored_pillars.append((error, PILLAR_PRIORITY.index(pillar), pillar))
        chosen_pillar = min(scored_pillars)[2]
        choices = by_pillar[chosen_pillar]

        # Avoid repeating a subject key until the qualified distinct subjects run out.
        recent_subjects = {row.get("topic") for row in (current_window + planned)[-8:]}
        choices.sort(key=lambda row: (
            1 if profile(row)[1] in recent_subjects else 0,
            -float(row.get("follow_growth_score") or 0),
            float(row.get("age_hours") or 0),
        ))
        choice = choices[0]
        selected.append(choice)
        remaining.remove(choice)
        pillar, topic = profile(choice)
        planned.append({
            "pillar": pillar,
            "topic": topic,
            "url": str(choice.get("url") or ""),
            "sent_at": "",
            "planned": True,
        })
        if topic == "food_cooking":
            food_in_batch += 1

    counts = {key: sum(1 for row in planned if row["pillar"] == key) for key in PILLAR_TARGETS}
    print(json.dumps({
        "MEDIA_REVIEW_TOPIC_PLAN": {
            "history_size": len(history),
            "history_pillars": {
                key: sum(1 for row in history if row.get("pillar") == key)
                for key in PILLAR_TARGETS
            },
            "planned_shortlist": len(selected),
            "planned_pillars": counts,
            "planned_food": sum(1 for row in planned if row.get("topic") == "food_cooking"),
            "last20_food_before": sum(1 for row in history if row.get("topic") == "food_cooking"),
            "target_pillar_mix": PILLAR_TARGETS,
            "soft_caps": {"food_per_20": 6, "food_per_first_3": 1},
        }
    }, ensure_ascii=False))
    return selected


def clean_review_caption(value, max_chars=1024):
    """Keep the meaningful source caption while removing invisible padding and promotion."""
    text = html.unescape(str(value or ""))
    text = CAPTION_ZERO_WIDTH_RE.sub("", text)
    # Persian ZWNJ is linguistically meaningful inside words; remove repeated
    # padding runs and edge padding without flattening valid Persian spelling.
    text = re.sub(r"[\u200c]{2,}", "", text).strip("\u200c")
    cta = CAPTION_CTA_TAIL_RE.search(text)
    if cta:
        text = text[:cta.start()]
    text = CAPTION_URL_RE.sub(" ", text)
    text = CAPTION_HANDLE_RE.sub(" ", text)
    text = CAPTION_BRAND_TAG_RE.sub(" ", text)
    # Preserve useful hashtag words (for example #اسنک شکلاتی), not the hash or underscores.
    text = re.sub(
        r"#([\w\u0600-\u06ff]+)",
        lambda match: match.group(1).replace("_", " "),
        text,
    )
    # Collapse repeated emoji spam while leaving ordinary individual emoji intact.
    text = re.sub(r"([\U0001F300-\U0001FAFF])(?:\s*\1){2,}", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([،؛:,.!?])", r"\1", text)
    text = re.sub(r"([،؛:,.!?])(?:\s*[\u200b\u200c\u200e\u200f])*([،؛:,.!?])+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" \t\r\n-–—|•")
    return text[:max_chars].strip()


def is_generic_greeting_caption(caption, lanes):
    """Reject generic calendar/wish messages unless the caption names a concrete video topic."""
    if not GENERIC_GREETING_CAPTION_RE.search(str(caption or "")):
        return False
    return not bool(set(lanes or set()).intersection(CAPTION_SPECIFIC_LANES))


def select_media_candidates(items, reviewed_ids=None, now=None, limit=MAX_TRIES, recent_topics=None):
    """Select unique, fresh, substantive clips and expose the actual rejection bottleneck."""
    reviewed = {str(x) for x in (reviewed_ids or [])}
    eligible = []
    seen_source_urls = set()
    rejected_topic = 0
    rejected_duplicates = 0
    rejection_reasons = {
        "file_relay_unavailable": 0,
        "already_reviewed_or_duplicate_url": 0,
        "unsupported_platform": 0,
        "no_target_topic_signal": 0,
        "invalid_score_or_age": 0,
        "score_below_minimum": 0,
        "outside_age_window": 0,
        "stale_telegram_not_strong_or_specific": 0,
        "insufficient_engagement_confidence": 0,
        "safety_news_compilation_or_promo": 0,
        "caption_empty_or_generic_greeting": 0,
    }
    telegram_near_misses = {reason: [] for reason in rejection_reasons}

    def reject(reason, item=None):
        rejection_reasons[reason] += 1
        if (
            isinstance(item, dict)
            and str(item.get("platform") or "").lower() == "telegram"
            and reason in telegram_near_misses
            and len(telegram_near_misses[reason]) < 3
        ):
            telegram_near_misses[reason].append({
                "source": str(item.get("source") or ""),
                "title": str(item.get("title") or "")[:110],
                "score": item.get("follow_growth_score"),
                "age_hours": item.get("age_hours"),
                "confidence": item.get("score_confidence"),
                "lanes": list(item.get("hook_signals") or [])[:4],
            })

    for raw_item in items if isinstance(items, list) else []:
        if not isinstance(raw_item, dict):
            continue

        # Re-score against current rules; hashtags alone are not topic evidence.
        item = annotate_candidate(raw_item)
        ident = candidate_id(item)
        url = str(item.get("url") or "").strip()
        canonical_url = canonical_media_url(url)
        platform = str(item.get("platform") or "").strip().lower()
        if not ident or not url.startswith(("https://", "http://")):
            rejection_reasons["invalid_score_or_age"] += 1
            continue
        if platform in UNAVAILABLE_FILE_RELAY_PLATFORMS:
            reject("file_relay_unavailable", item)
            continue
        if candidate_review_keys(item).intersection(reviewed):
            rejected_duplicates += 1
            reject("already_reviewed_or_duplicate_url", item)
            continue
        if canonical_url and canonical_url in seen_source_urls:
            rejected_duplicates += 1
            reject("already_reviewed_or_duplicate_url", item)
            continue
        if platform not in SUPPORTED:
            reject("unsupported_platform", item)
            continue

        lanes = set(item.get("hook_signals") or [])
        if not lanes.intersection(TARGET_LANES):
            rejected_topic += 1
            reject("no_target_topic_signal", item)
            continue

        cleaned_caption = clean_review_caption(item.get("title") or item.get("description") or "")
        if not cleaned_caption or is_generic_greeting_caption(cleaned_caption, lanes):
            reject("caption_empty_or_generic_greeting", item)
            continue
        item["cleaned_review_caption"] = cleaned_caption
        try:
            score = float(item.get("follow_growth_score") or 0)
            age = float(item.get("age_hours"))
        except (TypeError, ValueError):
            reject("invalid_score_or_age", item)
            continue

        native_telegram = platform == "telegram" and str(item.get("source") or "").lower() == "telegram_native_video"
        # Firecrawl's explicit qdr:d search filter is a defensible recency bound
        # for private review, but not proof of engagement or publication rights.
        # Allow only concrete subject lanes, a conservative minimum score, and
        # download/visual-QC checks downstream; never use this for auto-publishing.
        fresh_firecrawl_review_ok = (
            str(item.get("source") or "").lower() == "firecrawl_search"
            and platform in {"instagram", "tiktok", "aparat", "x"}
            and str(item.get("freshness_basis") or "") in {
                "published_date", "firecrawl_tbs_qdr_d_upper_bound"
            }
            and 0 <= age <= 24
            and score >= 25
            and bool(lanes.intersection(SPECIFIC_LONG_CLIP_LANES))
        )
        # Private review is not auto-publication: let fresh, single-hook native
        # Telegram clips qualify at a lower floor. Specific subject lanes can enter
        # at 30; broader reveal/reaction/comedy hooks need 36. Visual QC, duplicate
        # fingerprints, and all safety/news/promotion checks still apply downstream.
        fresh_native_telegram_specific_review_ok = (
            native_telegram
            and 0 <= age <= MAX_AGE_HOURS
            and score >= 30
            and bool(lanes.intersection(SPECIFIC_LONG_CLIP_LANES))
        )
        fresh_native_telegram_broad_hook_review_ok = (
            native_telegram
            and 0 <= age <= MAX_AGE_HOURS
            and score >= 36
            and bool(lanes.intersection(TARGET_LANES))
        )
        fresh_native_telegram_review_ok = (
            fresh_native_telegram_specific_review_ok or fresh_native_telegram_broad_hook_review_ok
        )
        if score < MIN_SCORE and not fresh_firecrawl_review_ok and not fresh_native_telegram_review_ok:
            reject("score_below_minimum", item)
            continue
        age_limit = MAX_TELEGRAM_REVIEW_AGE_HOURS if native_telegram else MAX_AGE_HOURS
        if age < 0 or age > age_limit:
            reject("outside_age_window", item)
            continue
        # Keep the wider 36-72h window narrow: only genuinely strong clips with
        # a concrete topic, never generic reactions or stale reposts.
        if age > MAX_AGE_HOURS and (
            not native_telegram
            or score < MIN_STALE_TELEGRAM_SCORE
            or not lanes.intersection(SPECIFIC_LONG_CLIP_LANES)
        ):
            reject("stale_telegram_not_strong_or_specific", item)
            continue

        confidence = item.get("score_confidence")
        # Missing native view counters may still be reviewed privately when a
        # concrete subject lane and score support the candidate.
        low_confidence_telegram_ok = (
            native_telegram
            and confidence == "low"
            and 0 <= age <= MAX_AGE_HOURS
            and (
                (score >= 30 and bool(lanes.intersection(SPECIFIC_LONG_CLIP_LANES)))
                or (score >= 36 and bool(lanes.intersection(TARGET_LANES)))
            )
        )
        low_confidence_fresh_firecrawl_ok = fresh_firecrawl_review_ok and confidence == "low"
        if confidence not in {"medium", "high"} and not low_confidence_telegram_ok and not low_confidence_fresh_firecrawl_ok:
            reject("insufficient_engagement_confidence", item)
            continue
        if any(item.get(k) for k in (
            "news_risk_signal", "sensitive_risk_signal", "safety_review_signal",
            "compilation_signal", "promotional_signal",
        )):
            reject("safety_news_compilation_or_promo", item)
            continue

        item["_review_id"] = ident
        item["_canonical_media_url"] = canonical_url
        eligible.append(item)
        if canonical_url:
            seen_source_urls.add(canonical_url)

    print(json.dumps({
        "MEDIA_REVIEW_SELECTION": {
            "eligible": len(eligible),
            "rejected_non_target_or_hashtag_only": rejected_topic,
            "rejected_duplicate_urls_or_reviewed": rejected_duplicates,
            "rejection_reasons": rejection_reasons,
            "telegram_rejection_samples": {key: value for key, value in telegram_near_misses.items() if value},
        }
    }, ensure_ascii=False))
    eligible.sort(key=lambda x: (
        float(x.get("follow_growth_score") or 0),
        -float(x.get("age_hours") or 0),
    ), reverse=True)

    # Preserve the old preference for distinct subject lanes and platforms when
    # building a qualified pool, then order that pool by rolling pillar deficits.
    pool, seen_primary_lanes, seen_ids, seen_platforms = [], set(), set(), set()
    for item in eligible:
        _pillar, primary_lane = content_pillar_profile(item)
        if primary_lane and primary_lane in seen_primary_lanes:
            continue
        pool.append(item)
        if primary_lane:
            seen_primary_lanes.add(primary_lane)
        seen_ids.add(item["_review_id"])
        seen_platforms.add(str(item.get("platform") or "unknown").lower())
        if len(pool) >= limit:
            break

    if len(pool) < limit:
        for item in eligible:
            if item["_review_id"] in seen_ids:
                continue
            platform = str(item.get("platform") or "unknown").lower()
            if platform in seen_platforms:
                continue
            pool.append(item)
            seen_platforms.add(platform)
            seen_ids.add(item["_review_id"])
            if len(pool) >= limit:
                break

    if len(pool) < limit:
        for item in eligible:
            if item["_review_id"] in seen_ids:
                continue
            pool.append(item)
            seen_ids.add(item["_review_id"])
            if len(pool) >= limit:
                break

    return balanced_candidate_shortlist(pool, recent_topics=recent_topics, limit=limit)

def find_telegram_post_block(page, channel, post_id):
    """Return only the HTML block for the exact Telegram post, never a nearby post."""
    expected = f"{channel}/{post_id}".strip("/")
    blocks = re.split(r'(?=<div\b[^>]*class="[^"]*\btgme_widget_message_wrap\b)', page, flags=re.I)
    for block in blocks:
        marker = re.search(r"""data-post=["']([^"']+)["']""", block[:30000], flags=re.I)
        if marker and marker.group(1).strip("/") == expected:
            return block
    return ""


def extract_telegram_post_caption(post_block):
    """Read visible caption text only from the exact source-post block."""
    if not post_block:
        return ""
    match = re.search(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        post_block,
        flags=re.I | re.S,
    )
    if not match:
        return ""
    caption = html.unescape(re.sub(r"<br\s*/?>", " ", match.group(1), flags=re.I))
    caption = re.sub(r"<[^>]+>", " ", caption)
    caption = re.sub(r"\s+", " ", caption).strip()
    return caption[:800]


def extract_telegram_post_video_urls(post_block):
    """Extract video URLs only from a verified single-post HTML block."""
    if not post_block:
        return []
    media_urls = []
    for tag in re.findall(r"<video\b[^>]*>", post_block, flags=re.I | re.S):
        for attr in ("src", "data-src"):
            found = re.search(rf"""{attr}\s*=\s*["']([^"']+)""", tag, flags=re.I)
            if found:
                media_url = html.unescape(found.group(1)).replace("&amp;", "&")
                if media_url.startswith("https://"):
                    media_urls.append(media_url)
    # Some Telegram layouts expose the media URL elsewhere inside the same post.
    # Deliberately never search the surrounding channel page.
    for value in re.findall(
        r"""https://[^\\\"'<> ]+\.(?:mp4|m4v)(?:\?[^\\\"'<> ]*)?""",
        post_block,
        flags=re.I,
    ):
        media_urls.append(html.unescape(value).replace("&amp;", "&"))
    return list(dict.fromkeys(media_urls))


def evaluate_video_metrics(duration, width, height, frame_metrics):
    """Fail closed on broken/black videos; score only observable technical signals."""
    try:
        duration = float(duration)
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return {"passed": False, "score": 0, "reason": "invalid_metadata"}

    if duration < 4 or duration > 60:
        return {"passed": False, "score": 0, "reason": "duration_out_of_range"}
    if width < 320 or height < 240 or width > 7680 or height > 7680:
        return {"passed": False, "score": 0, "reason": "resolution_out_of_range"}
    ratio = width / max(1, height)
    if ratio < 0.25 or ratio > 3.0:
        return {"passed": False, "score": 0, "reason": "aspect_ratio_out_of_range"}
    if len(frame_metrics) < 3:
        return {"passed": False, "score": 0, "reason": "insufficient_decodable_frames"}

    black_count = sum(1 for row in frame_metrics if row.get("mean_luma", 0) < 12 and row.get("std_luma", 0) < 7)
    black_ratio = black_count / len(frame_metrics)
    if black_ratio >= 0.6:
        return {
            "passed": False, "score": 0, "reason": "mostly_black_frames",
            "duration_seconds": round(duration, 2), "width": width, "height": height,
            "sampled_frames": len(frame_metrics), "black_frame_ratio": round(black_ratio, 2),
        }

    differences = []
    for index in range(1, len(frame_metrics)):
        try:
            differences.append(float(frame_metrics[index].get("difference_from_previous", 0)))
        except (TypeError, ValueError):
            pass
    mean_difference = sum(differences) / len(differences) if differences else 0.0

    if width >= 720 and height >= 720:
        resolution_points = 25
    elif width >= 480 and height >= 480:
        resolution_points = 18
    else:
        resolution_points = 10
    diversity_points = min(15, mean_difference / 5)
    score = round(max(0, min(100, 60 + resolution_points + diversity_points - black_ratio * 30)), 1)
    result = {
        "passed": True,
        "score": score,
        "reason": "technical_checks_passed",
        "duration_seconds": round(duration, 2),
        "width": width,
        "height": height,
        "sampled_frames": len(frame_metrics),
        "black_frame_ratio": round(black_ratio, 2),
        "mean_frame_difference": round(mean_difference, 2),
        "note": "Technical screen only; does not judge humor, topic relevance, or repost rights.",
    }
    frame_hashes = [str(row["dhash"]) for row in frame_metrics if row.get("dhash")]
    if len(frame_hashes) >= 4:
        result["frame_hashes"] = frame_hashes
    return result


def inspect_video_quality(video_path):
    """Use ffprobe/ffmpeg samples to reject technically broken media before Telegram upload."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration:format=duration",
                "-of", "json", str(video_path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if probe.returncode != 0:
            return {"passed": False, "score": 0, "reason": "ffprobe_failed"}
        payload = json.loads(probe.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return {"passed": False, "score": 0, "reason": "no_video_stream"}
        stream = streams[0]
        duration_raw = (payload.get("format") or {}).get("duration") or stream.get("duration")
        duration = float(duration_raw)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"passed": False, "score": 0, "reason": f"metadata_error:{type(exc).__name__}"}

    frame_metrics = []
    # Uniformly inspect the beginning, middle, and end rather than just the opening.
    with tempfile.TemporaryDirectory(prefix="video-frames-") as frame_dir:
        for index, fraction in enumerate((0.08, 0.28, 0.50, 0.72, 0.92)):
            frame_path = Path(frame_dir) / f"frame-{index}.jpg"
            timestamp = max(0.05, min(duration - 0.05, duration * fraction))
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{timestamp:.3f}", "-i", str(video_path),
                        "-frames:v", "1", "-vf",
                        "scale=96:96:force_original_aspect_ratio=decrease,"
                        "pad=96:96:(ow-iw)/2:(oh-ih)/2",
                        "-update", "1", str(frame_path),
                    ],
                    capture_output=True, text=True, timeout=25,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0 or not frame_path.is_file() or frame_path.stat().st_size == 0:
                continue
            try:
                with Image.open(frame_path) as opened:
                    image = opened.convert("RGB")
                gray_stats = ImageStat.Stat(image.convert("L"))
                row = {
                    "mean_luma": gray_stats.mean[0],
                    "std_luma": gray_stats.stddev[0],
                    "dhash": difference_hash(image),
                }
                if frame_metrics:
                    previous_path = Path(frame_dir) / "previous-frame.jpg"
                    # Keep the previous decoded frame in memory for exact same-size comparison.
                    with Image.open(previous_path) as previous_opened:
                        previous = previous_opened.convert("RGB")
                    row["difference_from_previous"] = sum(ImageStat.Stat(
                        ImageChops.difference(previous, image)
                    ).mean) / 3
                frame_metrics.append(row)
                image.save(Path(frame_dir) / "previous-frame.jpg", format="JPEG")
            except (OSError, ValueError):
                continue

    return evaluate_video_metrics(duration, width, height, frame_metrics)


def download_direct_telegram_post(url, destination, item=None):
    """Download video media only when it belongs to the exact Telegram post URL."""
    parsed = urlparse(url)
    match = re.match(r"^/([A-Za-z0-9_]+)/([0-9]+)/?$", parsed.path or "")
    if not match or parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me"}:
        return False
    channel, post_id = match.groups()
    page_url = f"https://t.me/s/{channel}/{post_id}"
    try:
        response = requests.get(page_url, headers={"User-Agent": UA}, timeout=25)
        response.raise_for_status()
        page = response.text[:8_000_000]
    except Exception as exc:
        print("TELEGRAM_SOURCE_PAGE_FAIL", channel, type(exc).__name__)
        return False

    post_block = find_telegram_post_block(page, channel, post_id)
    if not post_block:
        # Fail closed rather than risk sending the media from an adjacent post.
        print("TELEGRAM_TARGET_POST_NOT_FOUND", f"{channel}/{post_id}")
        return False
    source_caption = extract_telegram_post_caption(post_block)
    if source_caption and isinstance(item, dict):
        item["source_post_caption"] = source_caption
    media_urls = extract_telegram_post_video_urls(post_block)
    if not media_urls:
        print("TELEGRAM_TARGET_POST_HAS_NO_EXTRACTABLE_VIDEO", f"{channel}/{post_id}")
        return False

    for media_url in media_urls:
        try:
            with requests.get(media_url, headers={"User-Agent": UA}, stream=True, timeout=40) as response:
                response.raise_for_status()
                length = int(response.headers.get("Content-Length") or 0)
                if length and length > MAX_VIDEO_BYTES:
                    print("VIDEO_TOO_LARGE_SKIP", channel, length)
                    continue
                total = 0
                with open(destination, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            output.close()
                            Path(destination).unlink(missing_ok=True)
                            print("VIDEO_TOO_LARGE_SKIP", channel, total)
                            break
                        output.write(chunk)
                if Path(destination).is_file() and Path(destination).stat().st_size > 100_000:
                    return True
        except Exception as exc:
            print("TELEGRAM_MEDIA_URL_FAIL", type(exc).__name__)
            Path(destination).unlink(missing_ok=True)
    return False

def download_with_ytdlp(url, workdir):
    """Use yt-dlp for supported public individual-post URLs; never downloads playlists."""
    output_template = str(Path(workdir) / "%(id)s.%(ext)s")
    command = [
        "yt-dlp", "--no-warnings", "--no-progress", "--no-playlist",
        "--retries", "2", "--fragment-retries", "2", "--socket-timeout", "20",
        "--max-filesize", "48M",
        "-f",
        "bv*[height<=720][ext=mp4][filesize<48000000]+ba[ext=m4a]/"
        "b[height<=720][ext=mp4][filesize<48000000]/b[ext=mp4][filesize<48000000]/"
        "best[filesize<48000000]",
        "--merge-output-format", "mp4", "-o", output_template, url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print("YTDLP_DOWNLOAD_FAIL", url[:120], (result.stderr or result.stdout or "")[-500:].replace("\n", " "))
        return None
    files = [p for p in Path(workdir).glob("*") if p.is_file() and p.suffix.lower() == ".mp4"]
    files = [p for p in files if p.stat().st_size > 100_000 and p.stat().st_size <= MAX_VIDEO_BYTES]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


CAPTION_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
CAPTION_LONG_FORM_RE = re.compile(
    r"زندگی.?نامه|بیوگرافی|سوابق(?: حرفه.?ای)?|کارنامه|متولد شد|فعالیت.?های حرفه.?ای|"
    r"known for|biography|career highlights|personal life|was born|is a [^.]{0,80} (?:actor|singer|creator|influencer)",
    re.I,
)
CAPTION_RECIPE_DETAILS_RE = re.compile(
    r"مواد\s*لازم|طرز\s*تهیه|دستور\s*پخت|روش\s*تهیه|مراحل\s*تهیه|"
    r"ingredients|instructions|method|preparation|برای\s*تهیه",
    re.I,
)


def build_short_review_hook(item, max_chars=150):
    """Turn source copy into a short, topic-led hook without calling any paid AI API."""
    if not isinstance(item, dict):
        item = {"source_post_caption": str(item or "")}

    source = (
        item.get("source_post_caption")
        or item.get("cleaned_review_caption")
        or item.get("title")
        or item.get("description")
        or ""
    )
    cleaned = clean_review_caption(source, max_chars=1800)
    title = clean_review_caption(item.get("title") or "", max_chars=500)
    description = clean_review_caption(item.get("description") or "", max_chars=900)
    blob = " ".join(part for part in (title, cleaned, description) if part)
    folded = blob.lower()
    lanes = set(item.get("hook_signals") or [])

    # Keep experimental subjects recognizable while staying within the same caption style.
    if "travel_and_places" in lanes:
        return "این جای دیدنی رو تا حالا دیده بودی؟ 🗺️👀"[:max_chars]
    if "puzzles_and_visual_illusions" in lanes:
        return "چند ثانیه نگاه کن؛ متوجه نکته می‌شی؟ 🧩👀"[:max_chars]
    if "nostalgia_and_then_now" in lanes:
        return "یادت میاد اون روزها چه شکلی بودن؟ 🕰️✨"[:max_chars]

    # Specific, verifiable subjects take precedence over generic lane hooks.
    if re.search(
        r"(?:تشکیل|فرایند|چطور|چگونه).{0,35}تخم[\s‌-]?مرغ|"
        r"تخم[\s‌-]?مرغ.{0,35}(?:تشکیل|فرایند|بدن مرغ)|"
        r"egg formation|how eggs form|forming an egg|egg develops",
        folded, re.I,
    ):
        return "تا حالا فکر کردی تخم‌مرغ چطور شکل می‌گیره؟ 🥚👀"[:max_chars]

    if "food_cooking" in lanes or re.search(
        r"غذا|آشپزی|طرز تهیه|اسنک|کوبیده|کیک|شیرینی|دسر|recipe|cooking|baking|ingredients",
        folded, re.I,
    ):
        if re.search(r"اسنک[\s‌-]*شکلاتی|chocolate snack", folded, re.I):
            return "این اسنک شکلاتی رو ببین؛ وسوسه‌کننده‌ست 🍫🤤"[:max_chars]
        if re.search(r"کوبیده|koobideh|kebab", folded, re.I):
            return "کوبیده‌دوست‌ها، این یکی رو از دست ندین 🤤🔥"[:max_chars]
        if re.search(r"کیک[\s‌-]*شکلاتی|chocolate cake", folded, re.I):
            return "این ایدهٔ کیک شکلاتی رو ببین؛ دلت می‌خواد امتحانش کنی؟ 🍫😋"[:max_chars]
        subject = re.split(CAPTION_RECIPE_DETAILS_RE, cleaned, maxsplit=1)[0]
        subject = CAPTION_EMOJI_RE.sub(" ", subject)
        subject = re.sub(r"^\s*(?:طرز تهیه|دستور پخت|آموزش آشپزی|آشپزی با)\s*", "", subject, flags=re.I)
        subject = re.sub(r"\s+", " ", subject).strip(" #\t\r\n-–—|•:،؛")
        if 3 <= len(subject) <= 42 and not re.search(
            r"^(?:آشپزی|غذا|دستور پخت|طرز تهیه|مواد لازم)$", subject, re.I
        ):
            return f"این {subject} رو ببین؛ اشتهابرانگیزه 😋🔥"[:max_chars]
        return "یه ایدهٔ خوشمزه که ارزش دیدن داره 😋🍽️"[:max_chars]

    # Rewrite common clips as native social captions rather than summaries.
    if "animals" in lanes or re.search(r"گربه|سگ|حیوان|cat|dog|animal|pet", folded, re.I):
        if re.search(r"گربه|\bcat\b", folded, re.I):
            subject, emoji = "گربه", "🐈"
        elif re.search(r"سگ|\bdog\b", folded, re.I):
            subject, emoji = "سگ", "🐶"
        else:
            subject, emoji = "این حیوون", "🐾"
        if re.search(r"واکنش|عکس.?العمل|reaction|surprising|unexpected", folded, re.I):
            return f"واکنش {subject} رو ببین؛ آخرش جالبه 😂{emoji}"[:max_chars]
        return f"این لحظه با {subject} رو از دست نده {emoji}👀"[:max_chars]

    if "technology_demo" in lanes or re.search(r"هوش مصنوعی|\bAI\b|ربات|فناوری|technology demo", folded, re.I):
        return "این کاربرد هوش مصنوعی رو ببین؛ نتیجه جالبه 🤖👀"[:max_chars]
    if "transformation_or_satisfying" in lanes or re.search(r"قبل و بعد|تبدیل|ترمیم|بازسازی|satisfying|transformation", folded, re.I):
        return "قبل و بعدش رو ببین؛ تفاوتش خودش حرف می‌زنه ✨👀"[:max_chars]
    if "home_organization_or_cleaning" in lanes or re.search(r"تمیزکاری|نظافت|مرتب.?سازی|deep clean|declutter", folded, re.I):
        return "قبل و بعدش رو ببین؛ نتیجه دیدنیه ✨"[:max_chars]
    if "practical_tips_or_life_hacks" in lanes or re.search(r"ترفند کاربردی|ترفند خانه|life hack|useful tip", folded, re.I):
        return "این ترفند کوچیک رو یادت نگه دار 👀💡"[:max_chars]
    if "comedy" in lanes or re.search(r"طنز|خنده.?دار|سوتی|بامزه|funny|comedy|prank", folded, re.I):
        return "این لحظه رو ببین؛ احتمالاً لبخند می‌زنی 😂👀"[:max_chars]
    if "sports_or_athletic_skill" in lanes or re.search(r"فوتبال|بسکتبال|شوت تماشایی|ورزش|basketball|football|trick shot", folded, re.I):
        return "این حرکت رو ببین؛ چند ثانیهٔ آخرش مهمه 👀🔥"[:max_chars]
    if "nature_and_wildlife" in lanes or re.search(r"حیات وحش|طبیعت|آبشار|اقیانوس|wildlife|waterfall|ocean", folded, re.I):
        return "طبیعت هنوز بلدِ غافلگیرمون کنه 🌿👀"[:max_chars]
    if "science_curiosity" in lanes or re.search(r"آزمایش علمی|پدیده علمی|چطور کار می.?کند|science experiment|how it works", folded, re.I):
        return "این پدیده رو از نزدیک ببین؛ نکته‌ش جالبه 👀🔬"[:max_chars]
    if "diy_and_creativity" in lanes or re.search(r"کاردستی|ساخت دست.?ساز|نجاری|handmade|woodworking|DIY", folded, re.I):
        return "از ایده تا نتیجه؛ آخر کار رو ببین 🛠️👀"[:max_chars]
    if "music_or_dance" in lanes or re.search(r"رقص|نوازندگی|اجرای موسیقی|خوانندگی|music performance|dance", folded, re.I):
        return "این اجرا چند ثانیه مکث می‌ارزه 🎶✨"[:max_chars]
    if "fashion_or_cultural_showcase" in lanes or re.search(r"لباس ملی|لباس سنتی|فشن شو|runway|pageant", folded, re.I):
        return "جزئیات این اجرا روی صحنه رو ببین ✨👀"[:max_chars]
    if "wholesome_or_heartwarming" in lanes:
        return "این چند ثانیه واقعاً دلگرم‌کننده‌ست ❤️"[:max_chars]
    if "surprise_or_reveal" in lanes:
        return "آخرش رو از دست نده؛ غافلگیرکننده‌ست 👀😳"[:max_chars]
    if "human_reaction_or_relatable" in lanes:
        return "این واکنش رو ببین؛ خیلی آشناست 👀"[:max_chars]

    # Keep a genuinely short caption, but never forward long biography/description text.
    if cleaned and len(cleaned) <= max_chars and not CAPTION_LONG_FORM_RE.search(cleaned):
        return cleaned
    if CAPTION_LONG_FORM_RE.search(blob) or len(cleaned) > max_chars:
        return "نکتهٔ اصلی این ویدئو توی همین چند ثانیه‌ست 👀"[:max_chars]
    return cleaned[:max_chars].strip()


def render_caption(item):
    """Render a short hook from topic signals, not a source biography or ingredient list."""
    return build_short_review_hook(item)


def send_video(path, item):
    endpoint = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
    with open(path, "rb") as video:
        response = requests.post(
            endpoint,
            data={
                "chat_id": TARGET,
                "caption": render_caption(item),
                "supports_streaming": "true",
            },
            files={"video": (Path(path).name, video, "video/mp4")},
            timeout=(30, 150),
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendVideo returned ok=false")
    return (payload.get("result") or {}).get("message_id")


def main():
    if not TOKEN or not TARGET:
        print("MEDIA_REVIEW_SEND_SKIPPED: Telegram credentials not configured.")
        return 0

    data = load_json(INPUT, {})
    state = load_json(STATE, {"version": 1, "notified_ids": []})
    reviewed_ids = [str(x) for x in state.get("media_reviewed_ids", [])]
    reviewed_urls = [str(x) for x in state.get("media_reviewed_urls", [])]
    recent_topics = infer_recent_topic_history(state, data.get("items", []))
    candidates = select_media_candidates(
        data.get("items", []),
        reviewed_ids + reviewed_urls,
        recent_topics=recent_topics,
    )
    saved_fingerprints = state.get("media_reviewed_fingerprints", [])
    if not isinstance(saved_fingerprints, list):
        saved_fingerprints = []
    if not candidates:
        print("MEDIA_REVIEW_EMPTY: no new recent, qualified video candidates.")
        return 0

    sent_ids = []
    sent_urls = []
    sent_fingerprints = []
    sent_topic_records = []
    suppressed_ids = []
    suppressed_urls = []
    session_fingerprints = list(saved_fingerprints)
    with tempfile.TemporaryDirectory(prefix="short-review-") as workdir:
        for item in candidates:
            if len(sent_ids) >= MAX_SENDS:
                break
            url = str(item.get("url") or "")
            video_path = Path(workdir) / "telegram-upload.mp4"
            video_path.unlink(missing_ok=True)
            downloaded = False
            if str(item.get("platform") or "").lower() == "telegram":
                downloaded = download_direct_telegram_post(url, str(video_path), item)

            # Telegram candidates must pass the exact-post extractor above. Do not
            # fall back to a generic page extractor if that match failed.
            if not downloaded and str(item.get("platform") or "").lower() != "telegram":
                media_path = download_with_ytdlp(url, workdir)
                if media_path:
                    video_path = media_path
                    downloaded = True

            if not downloaded or not video_path.is_file():
                print("MEDIA_REVIEW_DOWNLOAD_SKIPPED", item.get("platform"), url[:140])
                # Avoid retrying the same inaccessible URL at every run; fresh discovery
                # will still supply other candidates and eventually newer replacements.
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(str(item.get("_canonical_media_url") or canonical_media_url(url)))
                continue
            final_caption = render_caption(item)
            if not final_caption or is_generic_greeting_caption(
                final_caption, set(item.get("hook_signals") or [])
            ):
                print("MEDIA_REVIEW_CAPTION_REJECT", json.dumps({
                    "reason": "empty_or_generic_after_source_caption_refresh",
                    "url": url,
                }, ensure_ascii=False))
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(str(item.get("_canonical_media_url") or canonical_media_url(url)))
                continue

            size = video_path.stat().st_size
            if size > MAX_VIDEO_BYTES:
                print("MEDIA_REVIEW_SIZE_GUARD", size)
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(str(item.get("_canonical_media_url") or canonical_media_url(url)))
                continue

            visual_qc = inspect_video_quality(video_path)
            print(json.dumps({
                "MEDIA_REVIEW_VISUAL_QC": visual_qc,
                "platform": item.get("platform"),
                "title": str(item.get("title") or "")[:150],
                "url": url,
            }, ensure_ascii=False))
            if not visual_qc.get("passed"):
                print("MEDIA_REVIEW_VISUAL_QC_REJECT", visual_qc.get("reason"), url[:140])
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(str(item.get("_canonical_media_url") or canonical_media_url(url)))
                continue

            duration_ok, duration_reason = duration_content_gate(item, visual_qc)
            if not duration_ok:
                print("MEDIA_REVIEW_DURATION_TOPIC_REJECT", json.dumps({
                    "reason": duration_reason,
                    "duration_seconds": visual_qc.get("duration_seconds"),
                    "score": item.get("follow_growth_score"),
                    "hook_signals": item.get("hook_signals"),
                    "url": url,
                }, ensure_ascii=False))
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(str(item.get("_canonical_media_url") or canonical_media_url(url)))
                continue

            current_fingerprint = {
                "url": str(item.get("_canonical_media_url") or canonical_media_url(url)),
                "sha256": sha256_file(video_path),
                "frame_hashes": visual_qc.get("frame_hashes") or [],
            }
            duplicate_of = next(
                (previous for previous in session_fingerprints
                 if isinstance(previous, dict) and media_fingerprints_match(current_fingerprint, previous)),
                None,
            )
            if duplicate_of:
                print("MEDIA_REVIEW_DUPLICATE_CONTENT_SKIPPED", json.dumps({
                    "url": url,
                    "duplicate_sha256": current_fingerprint["sha256"] == duplicate_of.get("sha256"),
                    "duplicate_source_url": current_fingerprint["url"] == duplicate_of.get("url"),
                }, ensure_ascii=False))
                suppressed_ids.append(item["_review_id"])
                suppressed_urls.append(current_fingerprint["url"])
                continue

            try:
                message_id = send_video(str(video_path), item)
                ident = item["_review_id"]
                sent_ids.append(ident)
                sent_urls.append(current_fingerprint["url"])
                sent_fingerprints.append(current_fingerprint)
                session_fingerprints.append(current_fingerprint)
                pillar, topic = content_pillar_profile(item)
                sent_topic_records.append({
                    "pillar": pillar,
                    "topic": topic,
                    "url": current_fingerprint["url"],
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                })
                print(json.dumps({
                    "MEDIA_REVIEW_VIDEO_SENT": True,
                    "message_id": message_id,
                    "platform": item.get("platform"),
                    "title": str(item.get("title") or "")[:150],
                    "url": url,
                    "bytes": size,
                }, ensure_ascii=False))
            except Exception as exc:
                print("MEDIA_REVIEW_TELEGRAM_SEND_FAIL", url[:140], type(exc).__name__, str(exc)[:240])
            finally:
                # Keep the workspace clean before the next source is downloaded.
                if video_path.exists():
                    video_path.unlink(missing_ok=True)

    if sent_ids or suppressed_ids:
        existing_ids = [str(x) for x in state.get("media_reviewed_ids", [])]
        existing_urls = [str(x) for x in state.get("media_reviewed_urls", [])]
        prior_fingerprints = [
            x for x in state.get("media_reviewed_fingerprints", [])
            if isinstance(x, dict)
        ]
        fingerprints_by_sha = {}
        for row in prior_fingerprints + sent_fingerprints:
            key = str(row.get("sha256") or row.get("url") or "")
            if key:
                fingerprints_by_sha[key] = row
        state.update({
            "version": 1,
            "media_review_updated_at": datetime.now(timezone.utc).isoformat(),
            "media_reviewed_ids": list(dict.fromkeys(existing_ids + reviewed_ids + sent_ids + suppressed_ids))[-1000:],
            "media_reviewed_urls": list(dict.fromkeys(existing_urls + reviewed_urls + sent_urls + suppressed_urls))[-1000:],
            "media_reviewed_fingerprints": list(fingerprints_by_sha.values())[-300:],
            "media_reviewed_topics": normalize_topic_history(recent_topics + sent_topic_records)[-20:],
            "media_review_last_count": len(sent_ids),
        })
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"MEDIA_REVIEW_TOTAL_SENT": len(sent_ids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
