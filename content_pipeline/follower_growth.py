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
    "skill_or_spectacle": re.compile(r"تردستی|مهارت|حرکت عجیب|شگفت.?انگیز|رکورد|پرش(?![\u0600-\u06FF])|ترفند|trick|skill|stunt|spectacular|impossible", re.I),
    "technology_demo": re.compile(r"هوش مصنوعی|\bAI\b|ربات|فناوری|تکنولوژی|Veo|تولید ویدیو|ساخت تصویر|robot|AI demo|technology demo|gadget|smart device", re.I),
    "human_reaction_or_relatable": re.compile(r"واکنش|عکس.?العمل|باور نمی.?کنه|زندگی روزمره|آشناست|relatable|reaction|caught on camera|everyday moment", re.I),
    "food_cooking": re.compile(r"غذا|آشپزی|آشپز|دستور پخت|طرز تهیه|غذای خیابانی|نان خانگی|پخت و پز|کیک|شیرینی|دسر|food|cooking|recipe|baking|dessert|street food|chef|cuisine", re.I),
    "practical_tips_or_life_hacks": re.compile(r"ترفندهای? کاربردی|ترفندهای? روزمره|هک زندگی|راهکار ساده|راه حل ساده|نکته کاربردی|ترفند خانه|ترفند|نکته مفید|روش ساده|life hack|useful tip|everyday hack|home hack|how to", re.I),
    "wholesome_or_heartwarming": re.compile(r"مهربان|مهربانی|کمک غیرمنتظره|دیدار دوباره|لحظه احساسی|آشتی کردن|خوشحال کردن|بغل کردن|نجات حیوان|پدر و دختر|مادر و فرزند|خواهر و برادر|heartwarming|wholesome|kindness|reunion|feel.?good|sweet moment|family moment|siblings|parent-child|cute baby", re.I),
    "sports_or_athletic_skill": re.compile(r"فوتبال|بسکتبال|والیبال|تنیس|پینگ.?پنگ|گل تماشایی|شوت تماشایی|دریبل|پارکور|ژیمناستیک|حرکت ورزشی|ورزشکار|ورزش|sports?|football|soccer|basketball|tennis|gymnastics|parkour|athletic|trick shot|amazing goal", re.I),
    "nature_and_wildlife": re.compile(r"حیات وحش|حیوانات وحشی|طبیعت بکر|منظره طبیعی|آبشار|اقیانوس|زیر آب|شفق قطبی|جنگل بارانی|جانوران وحشی|امواج عظیم|موج‌های عظیم|طوفان دریایی|دریای طوفانی|رعد و برق|کشتی باری|giant waves|massive waves|rough sea|thunderstorm|lightning|cargo ship|wildlife|nature|waterfall|ocean|underwater|aurora|rainforest|wild animals", re.I),
    "science_curiosity": re.compile(r"آزمایش علمی|تجربه علمی|چطور کار می.?کند|چرا این اتفاق|پدیده علمی|توضیح علمی|فیزیک|نجوم|کهکشان|سیاره|فرایند طبیعی|تشکیل تخم.?مرغ|تخم.?مرغ در بدن مرغ|فرایند تشکیل تخم.?مرغ|egg formation|how eggs form|science|experiment|physics|astronomy|space|planet|galaxy|how it works|scientific", re.I),
    "diy_and_creativity": re.compile(r"ساخت دست.?ساز|کار دستی|نجاری|سفالگری|نقاشی|طراحی|خوشنویسی|ساختن با دست|دست.?ساز|خلاقانه|DIY|craft|woodworking|drawing|painting|handmade|creative process", re.I),
    "home_organization_or_cleaning": re.compile(r"تمیزکاری عمیق|قبل و بعد نظافت|نظم.?دهی|مرتب کردن خانه|مرتب.?سازی|پاکسازی|تمیز کردن|سازماندهی خانه|cleaning|deep clean|home organization|declutter|organizing|tidying", re.I),
    "music_or_dance": re.compile(r"رقص|نوازندگی|نواختن|ساز زدن|خوانندگی|آواز|اجرای موسیقی|موسیقی زنده|حرکات موزون|dance|singing|music performance|playing an instrument|live music|dance move", re.I),
    "fashion_or_cultural_showcase": re.compile(r"لباس ملی|لباس سنتی|لباس ایرانی|مدل ایرانی|مسابقه زیبایی|میس گلوبال|فشن شو|مدلینگ|لباس الهام.?گرفته|national costume|traditional dress|fashion show|runway|pageant|beauty contest|beauty pageant", re.I),
    "travel_and_places": re.compile(r"جای دیدنی|جاهای دیدنی|مکان(?:های)? دیدنی|ایرانگردی|گردشگری|راهنمای سفر|سفر به|مسیر پیاده.?روی|جاذبه گردشگری|hidden places|places to visit|travel vlog|travel guide|tourism|hiking trail|beautiful places", re.I),
    "puzzles_and_visual_illusions": re.compile(r"خطای دید|معمای تصویری|بازی فکری|تست هوش|چیستان|پازل|تفاوت تصویر|optical illusion|visual illusion|brain teaser|riddle|spot the difference|puzzle challenge", re.I),
    "nostalgia_and_then_now": re.compile(r"نوستالژی|دهه [۴۵۶۷۸۹0-9]+|دوران قدیم|زمان قدیم|قدیم و جدید|گذشته تا امروز|خاطرات قدیمی|آن روزها|یادتونه قدیما|old vs new|nostalgia|throwback|then and now|back in the day", re.I),
}
NEWS_RISK = re.compile(
    r"جنگ|انفجار|پدافند|موشک|حمله نظامی|تیراندازی|کشته|مجروح|سلاح هسته|تجمع|اعتراض|شعار|سنگ.?پرونی|لغو.{0,12}(?:مسابقه|مراسم)|"
    r"ترامپ|رئیس.?جمهور|انتخابات|تحریم|حکومت|جمهوری اسلامی|سیاست|خاتمی|خامنه.?ای|روحانی|احمدی.?نژاد|پزشکیان|روسیه|اوکراین|اسرائیل|آمریکا|آمریکایی|پالایشگاه|"
    r"مرز(?:های)?|مرزی|افغان(?:ستان|ها|ی)?|مهاجر(?:ت|ان)?|پناهنده|ناامنی|ناامنه|تجاوز|جرم|قتل|ترور|قاچاق|"
    r"دستگیر|بازداشت|اتهام|درگیری|نزاع|حادثه تلخ|وکیل پایه.?یک|پرونده قضایی|آزار جنسی|آزار و اذیت|آزارواذیت|مرد.?هرزه|مورد تعرض|مزاحمت جنسی|تجاوز جنسی|اخراج مهاجر|"
    r"war|explosion|air.?defen[cs]e|missile|shooting|killed|injured|nuclear weapon|"
    r"president|election|sanction|government|politic|protest|demonstration|clash|cancelled after protest|"
    r"russia|ukraine|israel|united states|america|refinery|geopolitical|"
    r"afghanistan|afghan|immigration|immigrant|refugee|border|asylum|assault|sexual assault|crime|criminal|"
    r"arrested|detained|accused|rape|migration|deportation",
    re.I,
)
MULTI_CLIP_RISK = re.compile(
    r"\b(compilation|roundup|recap|best of|top\s*\d+|ranking|ranked|moments caught|caught moments|"
    r"best moments|greatest moments|most astonishing.{0,30}moments|viral moments|highlights|top moments)\b|"
    r"[0-9۰-۹]+\s*تا\s*(?:فان|کلیپ|اتفاق|لحظه|ویدیو|ویدئو|خنده|شوخی)|"
    r"(?:چندتا|چند)\s*(?:کلیپ|اتفاق|لحظه|ویدیو|ویدئو|فان|شوخی)|"
    r"گلچین|گزیده|مجموعه(?:ای)? از|تاپ\s*\d+|برترین لحظات|بهترین لحظات|لحظات برتر|"
    r"چند ماجرا|چند داستان|چند اتفاق|چند کلیپ|گلچین لحظات",
    re.I,
)
PROMOTIONAL_RISK = re.compile(r"تبلیغات|آگهی|اسپانسر|تخفیف|کد تخفیف|سفارش دهید|برای خرید|خرید از لینک|لینک خرید|ثبت.?نام کنید|به این لینک مراجعه|برای دریافت.{0,35}(?:وارد سایت|به سایت|روی لینک)|لینک در بیو|فروش ویژه|عضویت ویژه|افزایش فالوور|affiliate|sponsored|discount code|buy now|shop now|sign up now|link in bio|use my code|limited offer|promo code|visit our website", re.I)
SENSITIVE_RISK = re.compile(r"خون|لاشه|قطع عضو|خودکشی|پورن|جنسی صریح|آزار و اذیت|آزارواذیت|مرد.?هرزه|مورد تعرض|مزاحمت جنسی|gore|self.?harm|porn|explicit sexual", re.I)
MANUAL_SAFETY_REVIEW = re.compile(r"سلاح|اسلحه|تفنگ|جنگ.?افزار|آموزش.?های.?نظامی|نیروی.?نظامی|weapon|firearm|\bgun\b|military training", re.I)


# Guidance is destination-specific. It intentionally does not determine rights,
# approve publication, or remove a candidate because another platform has stricter rules.
PLATFORM_DISTRIBUTION_GUIDANCE = {
    "instagram": {
        "preferred_route": "native_repost_with_original_credit_when_available; otherwise rights-cleared original edit",
        "do_not_assume": "downloading and re-uploading a public Reel is equivalent to native reposting",
        "publishing_automation": "not_connected",
        "policy_reference": "https://about.fb.com/ltam/news/2024/05/ayudando-a-los-creadores-a-encontrar-nuevas-audiencias/",
    },
    "tiktok": {
        "preferred_route": "native reuse feature when available, or rights-cleared original/meaningfully edited post",
        "do_not_assume": "For You eligibility and permission to publish are the same thing",
        "publishing_automation": "not_connected; Content Posting API approval and audit may be required",
        "policy_reference": "https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content",
    },
    "youtube": {
        "preferred_route": "native Remix when available, or an accurately contextualized post using media cleared for the intended use",
        "do_not_assume": "YouTube Partner Program reused-content rules are a universal ban on posting; direct platform payouts are out of scope here",
        "publishing_automation": "not_connected; export bundle only; copyright, Community Guidelines, and spam rules still apply",
        "policy_reference": "https://support.google.com/youtube/answer/2801973",
    },
    "telegram": {
        "preferred_route": "native link/forward when appropriate, or rights-cleared original upload",
        "do_not_assume": "public availability means permission to copy the media file",
        "publishing_automation": "Bot API route is opt-in and disabled by default",
        "policy_reference": "https://core.telegram.org/bots/api#sendvideo",
    },
    "aparat": {
        "preferred_route": "rights-cleared original upload or a supported native share feature",
        "do_not_assume": "a stable authorized upload API exists until verified",
        "publishing_automation": "not_connected; export/manual fallback",
        "policy_reference": "",
    },
    "x": {
        "preferred_route": "native link/share with context, or a distinct localized post",
        "do_not_assume": "repeated identical automated cross-posts are safe",
        "publishing_automation": "not_connected",
        "policy_reference": "https://help.x.com/en/rules-and-policies/authenticity",
    },
}


def platform_distribution_guidance():
    """Return independent destination options without suppressing a candidate globally."""
    return {
        platform: {
            **settings,
            "rights_must_be_checked_for_file_reuse": True,
            "publishing_authorized": False,
        }
        for platform, settings in PLATFORM_DISTRIBUTION_GUIDANCE.items()
    }


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



PERSIAN_LANGUAGE_CODES = {"fa", "fas", "per", "persian", "farsi", "فارسی"}
PERSIAN_SCRIPT_RE = re.compile(r"[\u0600-\u06ff]")
PERSIAN_SPECIFIC_LETTERS_RE = re.compile(r"[پچژگک]")
PERSIAN_FUNCTION_WORDS_RE = re.compile(
    r"(?<![\w\u0600-\u06ff])(?:و|از|به|در|با|که|برای|این|اون|آن|یک|را|اما|چون|هم|است|شد|بود|رو|تو|تا)(?![\w\u0600-\u06ff])",
    re.I,
)


def persian_language_evidence(item):
    """Infer Persian-language priority from explicit audio/transcript metadata or cautious text proxies."""
    row = item if isinstance(item, dict) else {}

    # Prefer fields that describe spoken audio/subtitles over generic page locale.
    language_keys = (
        "audio_language", "spoken_language", "speech_language",
        "detected_audio_language", "transcript_language", "captions_language",
        "subtitle_language", "audio_lang", "transcript_lang",
    )
    for key in language_keys:
        value = str(row.get(key) or "").strip().casefold()
        if not value:
            continue
        normalized = re.sub(r"[ _]", "-", value)
        is_persian = (
            normalized in PERSIAN_LANGUAGE_CODES
            or normalized.startswith("fa-")
            or normalized.startswith("persian-")
            or normalized.startswith("farsi-")
        )
        return is_persian, "explicit_audio_or_transcript_language"

    # YouTube defaultLanguage describes the video metadata language, not proof of spoken audio.
    # Use Persian as a positive hint only; non-Persian metadata must not suppress Persian captions.
    for key in ("video_default_language", "default_language", "content_language"):
        value = str(row.get(key) or "").strip().casefold()
        if not value:
            continue
        normalized = re.sub(r"[ _]", "-", value)
        if (
            normalized in PERSIAN_LANGUAGE_CODES
            or normalized.startswith("fa-")
            or normalized.startswith("persian-")
            or normalized.startswith("farsi-")
        ):
            return True, "video_language_metadata"

    for key in ("transcript", "transcript_text", "speech_transcript", "captions_text", "subtitle_text"):
        transcript = str(row.get(key) or "").strip()
        if not transcript:
            continue
        script_chars = len(PERSIAN_SCRIPT_RE.findall(transcript))
        alphabetic_chars = len(re.findall(r"[^\W\d_]", transcript, re.UNICODE))
        function_words = len(PERSIAN_FUNCTION_WORDS_RE.findall(transcript))
        has_persian_shape = bool(PERSIAN_SPECIFIC_LETTERS_RE.search(transcript)) or function_words >= 2
        is_persian = (
            script_chars >= 8
            and script_chars / max(1, alphabetic_chars) >= 0.30
            and has_persian_shape
        )
        return is_persian, "transcript_text"

    text = " ".join(str(row.get(key) or "") for key in (
        "title", "description", "source_post_caption", "cleaned_review_caption",
    ))
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(?<!\w)#[\w\u0600-\u06ff]+", " ", text, flags=re.I)
    text = re.sub(r"(?<!\w)@[A-Za-z0-9_]+", " ", text)
    script_chars = len(PERSIAN_SCRIPT_RE.findall(text))
    alphabetic_chars = len(re.findall(r"[^\W\d_]", text, re.UNICODE))
    function_words = len(PERSIAN_FUNCTION_WORDS_RE.findall(text))
    has_persian_shape = bool(PERSIAN_SPECIFIC_LETTERS_RE.search(text)) or function_words >= 2
    if (
        script_chars >= 8
        and script_chars / max(1, alphabetic_chars) >= 0.20
        and has_persian_shape
    ):
        return True, "title_caption_text_proxy"

    # Existing collectors set this weak signal from script count alone. Retain it
    # only as low-confidence evidence; it never overrides explicit non-Persian audio.
    if row.get("persian_signal") is True:
        return True, "source_persian_metadata_signal"
    return False, "no_persian_language_evidence"


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
    if "facebook.com" in q or "fb.watch" in q:
        return "facebook"
    if "reddit.com" in q or "redd.it" in q:
        return "reddit"
    if "vimeo.com" in q:
        return "vimeo"
    if "dailymotion.com" in q or "dai.ly" in q:
        return "dailymotion"
    if "twitch.tv" in q:
        return "twitch"
    if "rumble.com" in q:
        return "rumble"
    return ""


def _freshness(age):
    if age is None:
        return 2.0
    if age <= 3:
        return 24.0
    if age <= 12:
        return 20.0
    if age <= 24:
        return 16.0
    if age <= 36:
        return 12.0
    if age <= 72:
        return 5.0
    if age <= 168:
        return 1.0
    return 0.0


def momentum_priority(age_hours, velocity_per_hour, acceleration_per_hour2=0.0,
                       views=0, source="", source_channel_priority=1,
                       velocity_kind="estimated_from_lifetime_views", content_score=0.0):
    """Rank primarily by recency and view velocity; lifetime reach is only supporting evidence."""
    try:
        age = max(0.0, float(age_hours))
    except (TypeError, ValueError, OverflowError):
        age = 999999.0
    try:
        velocity = max(0.0, float(velocity_per_hour))
    except (TypeError, ValueError, OverflowError):
        velocity = 0.0
    try:
        acceleration = float(acceleration_per_hour2 or 0)
    except (TypeError, ValueError, OverflowError):
        acceleration = 0.0
    try:
        reach = max(0, int(views or 0))
    except (TypeError, ValueError, OverflowError):
        reach = 0
    try:
        channel_tier = max(0, int(source_channel_priority))
    except (TypeError, ValueError, OverflowError):
        channel_tier = 1

    # Freshness and current velocity jointly dominate. The lifetime view total
    # cannot rescue a stale clip whose momentum has cooled.
    if age <= 1:
        freshness = 40.0
    elif age <= 3:
        freshness = 36.0
    elif age <= 6:
        freshness = 31.0
    elif age <= 12:
        freshness = 25.0
    elif age <= 18:
        freshness = 15.0
    elif age <= 24:
        freshness = 7.0
    else:
        freshness = 0.0

    velocity_score = min(45.0, math.log10(velocity + 1.0) / math.log10(20001.0) * 45.0)
    if age > 24:
        velocity_score = min(4.0, velocity_score)

    acceleration_score = max(-5.0, min(7.0, acceleration / 2500.0 * 7.0))
    reach_score = min(5.0, math.log10(reach + 1.0) / 6.0 * 5.0)
    measurement_score = 3.0 if str(velocity_kind) == "observed" else 0.0
    channel_score = 3.0 if str(source).lower() == "telegram_native_video" and channel_tier == 0 else (
        1.5 if str(source).lower() == "telegram_native_video" else 0.0
    )
    content_tiebreak = min(2.0, max(0.0, float(content_score or 0.0)) / 50.0)
    return round(max(0.0, freshness + velocity_score + acceleration_score + reach_score
                     + measurement_score + channel_score + content_tiebreak), 2)


def annotate_candidate(item):
    """Attach transparent heuristic fields; do not infer rights or guaranteed growth."""
    x = dict(item or {})
    title = str(x.get("title") or "")
    description = str(x.get("description") or "")
    platform = str(x.get("platform") or "").lower()
    source = str(x.get("source") or "").lower()
    age = _age_hours(x)
    stale = age is not None and age > 36.0

    # Hashtags, channel handles and URLs are distribution metadata, not proof
    # that the video itself contains comedy, surprise, or another desired hook.
    text = re.sub(r"https?://\S+", " ", f"{title} {description}")
    text = re.sub(r"(?<!\w)#[\w\u0600-\u06ff]+", " ", text, flags=re.I)
    text = re.sub(r"(?<!\w)@[A-Za-z0-9_]+", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()

    lanes = [name for name, pattern in LANES.items() if pattern.search(text)]
    is_news = bool(NEWS_RISK.search(title))
    if source != "youtube_public_api":
        is_news = is_news or bool(NEWS_RISK.search(description))
    is_sensitive = bool(SENSITIVE_RISK.search(text))
    safety_review = bool(MANUAL_SAFETY_REVIEW.search(text))
    compilation = bool(MULTI_CLIP_RISK.search(text))
    promotional = bool(PROMOTIONAL_RISK.search(text))
    persian_signal, language_signal_basis = persian_language_evidence(x)

    # Persian language is a ranking preference, not a substitute for freshness,
    # a strong visual hook, or safety/quality gates.
    language_score_bonus = 0.0
    language_priority_bonus = 0.0
    age_allows_language_bonus = age is not None and age <= 24.0
    quality_allows_language_bonus = not any(
        (is_news, is_sensitive, safety_review, compilation, promotional, stale)
    )
    if persian_signal and age_allows_language_bonus and quality_allows_language_bonus:
        if language_signal_basis == "explicit_audio_or_transcript_language":
            language_score_bonus, language_priority_bonus = 12.0, 10.0
        elif language_signal_basis == "video_language_metadata":
            language_score_bonus, language_priority_bonus = 9.0, 7.0
        elif language_signal_basis == "transcript_text":
            language_score_bonus, language_priority_bonus = 10.0, 8.0
        elif language_signal_basis == "title_caption_text_proxy":
            language_score_bonus, language_priority_bonus = 8.0, 6.0
        else:
            language_score_bonus, language_priority_bonus = 5.0, 3.0

    native_views = _int(x.get("views"))
    repost_views = _int(x.get("telegram_repost_views"))
    views = max(native_views, repost_views)
    demand = min(24.0, math.log10(views + 1) / 6.0 * 24.0) if views else 0.0

    velocity = 0.0
    if x.get("views_velocity_per_hour") not in (None, ""):
        try:
            views_per_hour = max(0.0, float(x.get("views_velocity_per_hour")))
        except (TypeError, ValueError, OverflowError):
            views_per_hour = views / max(age or 0.5, 0.5) if views else 0.0
    else:
        # First-seen estimates are explicitly labeled; once two snapshots exist,
        # the measured view delta takes precedence.
        views_per_hour = views / max(age or 0.5, 0.5) if views else 0.0
        x["views_velocity_per_hour"] = round(views_per_hour, 2)
        x["views_velocity_kind"] = "estimated_from_lifetime_views"
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
    score = _freshness(age) + demand + velocity + hook + engagement + repost_signal + native_platform_fit + language_score_bonus
    if is_news:
        score -= 15.0
    if is_sensitive:
        score -= 35.0
    if safety_review and not is_sensitive:
        score -= 10.0
    if compilation:
        score -= 16.0
    if promotional:
        score -= 24.0
    if stale:
        if age > 168:
            score -= 28.0
        elif age > 72:
            score -= 18.0
        else:
            score -= 10.0
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
    elif promotional:
        action = "deprioritize_promotional"
    elif stale:
        action = "deprioritize_stale_candidate"
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

    try:
        observed_velocity_for_rank = max(0.0, float(x.get("views_velocity_per_hour") or 0))
    except (TypeError, ValueError, OverflowError):
        observed_velocity_for_rank = 0.0
    x["momentum_priority_score"] = round(min(100.0, momentum_priority(
        age, observed_velocity_for_rank,
        x.get("views_acceleration_per_hour2", 0),
        views, source, x.get("telegram_channel_priority", 1),
        x.get("views_velocity_kind", "estimated_from_lifetime_views"), score,
    ) + language_priority_bonus), 2)

    x.update({
        "persian_language_signal": persian_signal,
        "language_signal_basis": language_signal_basis,
        "language_score_bonus": language_score_bonus,
        "language_priority_bonus": language_priority_bonus,
        "follow_growth_score": score,
        "score_confidence": confidence,
        "content_lane": lane,
        "hook_signals": lanes,
        "news_risk_signal": is_news,
        "sensitive_risk_signal": is_sensitive,
        "safety_review_signal": safety_review,
        "compilation_signal": compilation,
        "promotional_signal": promotional,
        "stale_signal": stale,
        "source_platform": platform,
        "candidate_action": action,
        "rights_status": "not_assessed",
        "score_note": "Heuristic triage only; actual follower growth and publication rights are not established by this score.",
        "distribution_guidance": platform_distribution_guidance(),
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
        float(x.get("momentum_priority_score") or 0),
        1 if str(x.get("views_velocity_kind") or "") == "observed" else 0,
        float(x.get("views_velocity_per_hour") or 0),
        -(_age_hours(x) if _age_hours(x) is not None else 999999.0),
        float(x.get("follow_growth_score") or 0),
        observed_at(x),
    ), reverse=True)
    return rows[:max(0, int(limit))]
