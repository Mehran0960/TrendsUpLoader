"""Heuristic ranking for finding social videos with follower-growth potential.

This is a triage score, not a prediction of future follower counts. It never
clears copyright or authorizes automatic publication.
"""
import math
import re
from datetime import datetime, timezone


PLATFORM_SHORTFORM = {"instagram", "tiktok", "youtube", "aparat"}
PLATFORMS = PLATFORM_SHORTFORM | {"telegram", "x"}

LANES = {
    "comedy": re.compile(r"خنده|خنده.?دار|طنز|سوتی|بامزه|قهقهه|شوخی|fail|funny|comedy|prank", re.I),
    "animals": re.compile(r"گربه|سگ|حیوان|پرنده|گربه.?سان|cat|dog|animal|pet", re.I),
    "surprise_or_reveal": re.compile(r"غافلگیر|باورنکردنی|غیرمنتظره|آخرش|نتیجه.?اش|راز|معما|عجیب|reveal|unexpected|plot.?twist|mystery|wait until", re.I),
    "transformation_or_satisfying": re.compile(r"قبل و بعد|تبدیل|تغییر|ترمیم|بازسازی|رضایت.?بخش|satisfying|transformation|before.?after|restoration", re.I),
    "skill_or_spectacle": re.compile(r"تردستی|مهارت|حرکت عجیب|شگفت.?انگیز|رکورد|پرش|ترفند|trick|skill|stunt|spectacular|impossible", re.I),
    "technology_demo": re.compile(r"هوش مصنوعی|\bAI\b|ربات|فناوری|تکنولوژی|Veo|تولید ویدیو|ساخت تصویر|robot|AI demo|technology demo", re.I),
    "human_reaction_or_relatable": re.compile(r"واکنش|عکس.?العمل|باور نمی.?کنه|زندگی روزمره|آشناست|relatable|reaction|caught on camera", re.I),
}
NEWS_RISK = re.compile(
    r"جنگ|انفجار|پدافند|موشک|حمله نظامی|تیراندازی|کشته|مجروح|سلاح هسته|تجمع|اعتراض|شعار|سنگ.?پرونی|لغو.{0,12}(?:مسابقه|مراسم)|"
    r"ترامپ|رئیس.?جمهور|انتخابات|تحریم|حکومت|جمهوری اسلامی|سیاست|روسیه|اوکراین|اسرائیل|آمریکا|آمریکایی|پالایشگاه|"
    r"war|explosion|air.?defen[cs]e|missile|shooting|killed|injured|nuclear weapon|"
    r"president|election|sanction|government|politic|protest|demonstration|clash|cancelled after protest|"
    r"russia|ukraine|israel|united states|america|refinery|geopolitical",
    re.I,
)
MULTI_CLIP_RISK = re.compile(
    r"\b(compilation|roundup|recap|best of|top\s*\d+|ranking|ranked|moments caught|caught moments|"
    r"best moments|greatest moments|most astonishing.{0,30}moments|viral moments|highlights|top moments)\b|"
    r"گلچین|گزیده|مجموعه(?:ای)? از|تاپ\s*\d+|برترین لحظات|بهترین لحظات|لحظات برتر|"
    r"چند ماجرا|چند داستان|چند اتفاق|چند کلیپ|گلچین لحظات",
    re.I,
)
SENSITIVE_RISK = re.compile(r"خون|لاشه|قطع عضو|خودکشی|پورن|جنسی صریح|gore|self.?harm|porn|explicit sexual", re.I)
MANUAL_SAFETY_REVIEW = re.compile(r"سلاح|اسلحه|تفنگ|جنگ.?افزار|آموزش.?های.?نظامی|نیروی.?نظامی|weapon|firearm|\bgun\b|military training", re.I)


def _int(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _age_hours(item):
    raw = item.get("age_hours")
    if raw not in (None, ""):
        try:
            age = float(raw)
            if math.isfinite(age) and age >= 0:
                return age
        except (TypeError, ValueError):
            pass
    published = str(item.get("published_at") or "").strip()
    if published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600)
        except (TypeError, ValueError):
            pass
    return None


def expected_platform_for_query(query):
    """Infer platform constraint from a site-scoped public search query."""
    q = str(query or "").lower()
    if "instagram.com" in q:
        return "instagram"
    if "tiktok.com" in q:
        return "tiktok"
    if "youtube.com" in q or "youtu.be" in q:
        return "youtube"
    if "aparat.com" in q:
        return "aparat"
    if "x.com" in q or "twitter.com" in q:
        return "x"
    return ""


def _freshness(age):
    if age is None:
        return 4.0
    if age <= 3:
        return 20.0
    if age <= 12:
        return 17.0
    if age <= 24:
        return 14.0
    if age <= 72:
        return 9.0
    if age <= 168:
        return 3.0
    return 0.0


def annotate_candidate(item):
    """Attach transparent heuristic fields; do not infer rights or guaranteed growth."""
    x = dict(item or {})
    title = str(x.get("title") or "")
    description = str(x.get("description") or "")
    text = f"{title} {description}"
    platform = str(x.get("platform") or "").lower()
    source = str(x.get("source") or "").lower()
    age = _age_hours(x)

    lanes = [name for name, pattern in LANES.items() if pattern.search(text)]
    is_news = bool(NEWS_RISK.search(text))
    is_sensitive = bool(SENSITIVE_RISK.search(text))
    safety_review = bool(MANUAL_SAFETY_REVIEW.search(text))
    compilation = bool(MULTI_CLIP_RISK.search(text))

    native_views = _int(x.get("views"))
    repost_views = _int(x.get("telegram_repost_views"))
    views = max(native_views, repost_views)
    demand = min(24.0, math.log10(views + 1) / 6.0 * 24.0) if views else 0.0

    velocity = 0.0
    if views and age is not None:
        views_per_hour = views / max(age, 0.5)
        velocity = min(14.0, math.log10(views_per_hour + 1) / 6.0 * 14.0)

    likes = _int(x.get("likes"))
    comments = _int(x.get("comments"))
    shares = _int(x.get("shares"))
    engagement = 0.0
    if views and (likes or comments or shares):
        rate = (likes + comments + shares) / max(1, native_views or views)
        engagement = min(10.0, math.log10(1.0 + rate * 1000.0) * 3.3)

    hook = min(24.0, 8.0 * len(lanes))
    if len(lanes) >= 2:
        hook = min(24.0, 12.0 + 4.0 * (len(lanes) - 1))

    repost_channels = x.get("telegram_repost_channels") or []
    if isinstance(repost_channels, str):
        repost_channels = [repost_channels] if repost_channels else []
    repost_signal = min(6.0, math.log10(repost_views + 1) / 6.0 * 4.0 + min(2.0, len(repost_channels) * 0.5)) if repost_views else min(2.0, len(repost_channels) * 0.5)

    native_platform_fit = 2.0 if platform in PLATFORMS else 0.0
    score = _freshness(age) + demand + velocity + hook + engagement + repost_signal + native_platform_fit
    if is_news:
        score -= 15.0
    if is_sensitive:
        score -= 35.0
    if safety_review and not is_sensitive:
        score -= 10.0
    if compilation:
        score -= 16.0
    score = round(max(0.0, min(100.0, score)), 1)

    if native_views and age is not None and (likes or comments or shares):
        confidence = "high"
    elif views and age is not None:
        confidence = "medium"
    else:
        confidence = "low"

    if is_sensitive:
        action = "reject_sensitive_or_manual_safety_review"
    elif safety_review:
        action = "manual_safety_review"
    elif is_news:
        action = "manual_review_current_affairs"
    elif compilation:
        action = "deprioritize_multi_story_roundup"
    elif score >= 65 and confidence in {"medium", "high"}:
        action = "prioritize_for_visual_and_rights_review"
    elif score >= 42 or confidence == "low":
        action = "manual_review"
    else:
        action = "deprioritize"

    if is_news:
        lane = "news_or_current_affairs"
    elif compilation:
        lane = "multi_clip_roundup"
    elif "technology_demo" in lanes:
        lane = "technology_demo"
    elif lanes:
        lane = lanes[0]
    else:
        lane = "unclassified"

    x.update({
        "follow_growth_score": score,
        "score_confidence": confidence,
        "content_lane": lane,
        "hook_signals": lanes,
        "news_risk_signal": is_news,
        "sensitive_risk_signal": is_sensitive,
        "safety_review_signal": safety_review,
        "compilation_signal": compilation,
        "source_platform": platform,
        "candidate_action": action,
        "rights_status": "not_assessed",
        "score_note": "Heuristic triage only; actual follower growth and publication rights are not established by this score.",
    })
    return x


def rank_candidates(items, limit=500):
    """Return eligible video-post candidates ranked for review, never auto-publication."""
    rows = [annotate_candidate(x) for x in items if isinstance(x, dict) and str(x.get("url") or "").strip()]
    def observed_at(item):
        raw = str(item.get("discovered_at") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError):
            return 0.0
    rows.sort(key=lambda x: (
        float(x.get("follow_growth_score") or 0),
        observed_at(x),
        _int(x.get("views")) + _int(x.get("telegram_repost_views")),
    ), reverse=True)
    return rows[:max(0, int(limit))]
