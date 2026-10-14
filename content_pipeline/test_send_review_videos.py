"""Regression tests for source-post/media matching in private video review."""
import unittest
from unittest.mock import patch

from content_pipeline.send_review_videos import (
    extract_telegram_post_video_urls,
    extract_telegram_post_caption,
    find_telegram_post_block,
    render_caption,
    clean_review_caption,
    is_generic_greeting_caption,
    evaluate_video_metrics,
    select_media_candidates,
    canonical_media_url,
    media_fingerprints_match,
    duration_content_gate,
    SPECIFIC_LONG_CLIP_LANES,
    balanced_candidate_shortlist,
    content_pillar_profile,
    infer_recent_topic_history,
    MAX_SENDS,
    run_fallback_discovery_sweep,
    viral_evidence_gate,
)


class TelegramPostMediaMatchingTests(unittest.TestCase):
    def setUp(self):
        self.page = """
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="demo_channel/100">
            <div class="tgme_widget_message_text">عنوان پست اول</div>
            <video src="https://cdn.example/first.mp4"></video>
          </div>
        </div>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="demo_channel/200">
            <div class="tgme_widget_message_text">عنوان پست دوم</div>
            <video src="https://cdn.example/second.mp4"></video>
            <a href="https://cdn.example/second-extra.mp4">video</a>
          </div>
        </div>
        """

    def test_only_exact_post_block_is_selected(self):
        block = find_telegram_post_block(self.page, "demo_channel", "200")
        self.assertIn('data-post="demo_channel/200"', block)
        self.assertNotIn('data-post="demo_channel/100"', block)

    def test_only_media_from_exact_post_is_extracted(self):
        block = find_telegram_post_block(self.page, "demo_channel", "200")
        urls = extract_telegram_post_video_urls(block)
        self.assertIn("https://cdn.example/second.mp4", urls)
        self.assertIn("https://cdn.example/second-extra.mp4", urls)
        self.assertNotIn("https://cdn.example/first.mp4", urls)

    def test_missing_target_fails_closed(self):
        block = find_telegram_post_block(self.page, "demo_channel", "999")
        self.assertEqual(block, "")
        self.assertEqual(extract_telegram_post_video_urls(block), [])



    def test_rejects_topic_signals_that_exist_only_in_hashtags(self):
        candidate = {
            "id": "generic-youtube",
            "url": "https://www.youtube.com/shorts/generic123",
            "platform": "youtube",
            "source": "youtube_public_api",
            "title": "Trend pada Bari 🥹🤣 #viralvideo #newviralreels #youtuber #shorts #comedy #funny",
            "description": "",
            "views": 18712,
            "likes": 500,
            "comments": 40,
            "age_hours": 13.4,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_accepts_pageant_fashion_clip_with_persia_in_title(self):
        candidate = {
            "id": "pageant-fashion-persia",
            "url": "https://t.me/keereker/256451",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "مدل ایرانی با لباسی به نام پرشیا روی صحنه رفت",
            "description": "",
            "views": 25000,
            "age_hours": 2,
        }
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertIn("fashion_or_cultural_showcase", selected[0]["hook_signals"])

    def test_accepts_cultural_runway_clip(self):
        candidate = {
            "id": "runway-cultural-showcase",
            "url": "https://t.me/gizmiztel/163079",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "مدل ایرانی در بخش لباس ملی با لباسی الهام‌گرفته از تاریخ روی صحنه رفت",
            "description": "",
            "views": 14000,
            "age_hours": 0.2,
        }
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertIn("fashion_or_cultural_showcase", selected[0]["hook_signals"])

    def test_keeps_a_substantive_funny_animal_clip(self):
        candidate = {
            "id": "funny-cat",
            "url": "https://t.me/funnycat/123",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A cat has a surprising funny reaction to a toy",
            "description": "Unexpected animal reaction caught on camera.",
            "views": 150000,
            "likes": 8000,
            "comments": 300,
            "age_hours": 2,
        }
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertIn("animals", selected[0]["hook_signals"])


    def test_rejects_fresh_reveal_only_telegram_clip_without_viral_evidence(self):
        candidate = {
            "id": "fresh-reveal-hook",
            "url": "https://t.me/revealclips/456",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "آخرش نباید اینجوری تموم میشد",
            "description": "یک اتفاق غیرمنتظره در پایان کلیپ",
            "views": 0,
            "age_hours": 2,
        }
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(selected, [])

    def test_rejects_fresh_single_lane_telegram_clip_without_view_evidence(self):
        candidate = {
            "id": "fresh-science-low-reach",
            "url": "https://t.me/scienceclips/456",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "آزمایش علمی ساده با آهنربا",
            "description": "آزمایش علمی کوتاه و روشن.",
            "views": 0,
            "age_hours": 2,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_rejects_fresh_native_telegram_clip_without_view_counters(self):
        candidate = {
            "id": "fresh-telegram-low-confidence",
            "url": "https://t.me/funnycat/456",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A funny cat has a surprising reaction to a toy",
            "description": "A clear, single animal moment.",
            "views": 0,
            "age_hours": 2,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_rejects_older_telegram_clip_without_recent_momentum(self):
        candidate = {
            "id": "strong-telegram-48h",
            "url": "https://t.me/funnycat/789",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A funny cat has a surprising reaction to a toy",
            "description": "One clear animal moment.",
            "views": 200000,
            "likes": 5000,
            "comments": 200,
            "age_hours": 48,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_rejects_older_telegram_clip_with_only_generic_reaction_hook(self):
        candidate = {
            "id": "vague-telegram-48h",
            "url": "https://t.me/example/790",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A surprising human reaction caught on camera",
            "description": "An unexpected reaction.",
            "views": 500000,
            "likes": 12000,
            "comments": 500,
            "age_hours": 48,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_rejects_search_result_without_native_engagement_evidence(self):
        candidate = {
            "id": "recent-firecrawl-home-tip",
            "url": "https://x.com/clipfeed/status/123456789",
            "platform": "x",
            "source": "firecrawl_search",
            "freshness_basis": "firecrawl_tbs_qdr_d_upper_bound",
            "age_hours": 24,
            "title": "A practical life hack to organize a tiny desk",
            "description": "A clear home organization tip.",
            "views": 0,
            "likes": 0,
            "comments": 0,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_skips_platforms_with_unavailable_public_file_extraction(self):
        candidates = [
            {
                "id": "youtube-animal",
                "url": "https://www.youtube.com/shorts/funnycat456",
                "platform": "youtube",
                "source": "youtube_public_api",
                "title": "A cat has a surprising funny reaction to a toy",
                "description": "Unexpected animal reaction caught on camera.",
                "views": 150000,
                "likes": 8000,
                "comments": 300,
                "age_hours": 2,
            },
            {
                "id": "instagram-animal",
                "url": "https://www.instagram.com/reel/funnycat456/",
                "platform": "instagram",
                "source": "firecrawl_search",
                "title": "A cat has a surprising funny reaction to a toy",
                "description": "Unexpected animal reaction caught on camera.",
                "views": 0,
                "age_hours": 24,
                "freshness_basis": "firecrawl_tbs_qdr_d_upper_bound",
            },
            {
                "id": "tiktok-animal",
                "url": "https://www.tiktok.com/@cat/video/1234567890",
                "platform": "tiktok",
                "source": "firecrawl_search",
                "title": "A cat has a surprising funny reaction to a toy",
                "description": "Unexpected animal reaction caught on camera.",
                "views": 0,
                "age_hours": 24,
                "freshness_basis": "firecrawl_tbs_qdr_d_upper_bound",
            },
            {
                "id": "aparat-unavailable",
                "url": "https://www.aparat.com/v/abc123",
                "platform": "aparat",
                "source": "firecrawl_search",
                "title": "A practical life hack to organize a tiny desk",
                "description": "A clear home organization tip.",
                "views": 0,
                "age_hours": 24,
                "freshness_basis": "firecrawl_tbs_qdr_d_upper_bound",
            },
        ]
        for candidate in candidates:
            with self.subTest(platform=candidate["platform"]):
                self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_viral_evidence_gate_requires_specific_hook_views_and_momentum(self):
        weak = {
            "follow_growth_score": 75,
            "score_confidence": "medium",
            "hook_signals": ["surprise_or_reveal"],
            "views": 80000,
            "age_hours": 2,
        }
        ok, reason = viral_evidence_gate(weak)
        self.assertFalse(ok)
        self.assertEqual(reason, "generic_or_weak_hook")

        strong = {
            "follow_growth_score": 75,
            "score_confidence": "medium",
            "hook_signals": ["animals"],
            "views": 80000,
            "age_hours": 2,
        }
        ok, reason = viral_evidence_gate(strong)
        self.assertTrue(ok)
        self.assertEqual(reason, "viral_evidence_gate_passed")

    def test_observed_view_velocity_overrides_high_lifetime_views(self):
        candidate = {
            "follow_growth_score": 90,
            "score_confidence": "high",
            "hook_signals": ["animals"],
            "views": 250000,
            "age_hours": 3,
            "views_velocity_per_hour": 50,
            "views_velocity_kind": "observed",
        }
        ok, reason = viral_evidence_gate(candidate)
        self.assertFalse(ok)
        self.assertEqual(reason, "insufficient_velocity")

    def test_stale_video_is_rejected_even_with_huge_total_views_and_velocity(self):
        candidate = {
            "follow_growth_score": 98,
            "score_confidence": "high",
            "hook_signals": ["animals"],
            "views": 900000,
            "age_hours": 30,
            "views_velocity_per_hour": 25000,
            "views_velocity_kind": "observed",
        }
        ok, reason = viral_evidence_gate(candidate)
        self.assertFalse(ok)
        self.assertEqual(reason, "outside_age_window")

    def test_fresh_fast_growing_clip_beats_larger_but_slower_clip(self):
        slower_historical_hit = {
            "id": "slower-historical-hit",
            "url": "https://t.me/gizmiztel/100",
            "platform": "telegram",
            "source": "telegram_native_video",
            "telegram_channel": "gizmiztel",
            "title": "A cat has a surprising reaction to a toy",
            "description": "One clear animal moment",
            "views": 250000,
            "age_hours": 5,
            "views_velocity_per_hour": 800,
            "views_velocity_kind": "observed",
            "score_confidence": "high",
        }
        faster_new_clip = {
            "id": "faster-new-clip",
            "url": "https://t.me/gizmiztel/200",
            "platform": "telegram",
            "source": "telegram_native_video",
            "telegram_channel": "gizmiztel",
            "title": "A simple science experiment with a magnet",
            "description": "A clear short science experiment",
            "views": 50000,
            "age_hours": 2,
            "views_velocity_per_hour": 10000,
            "views_velocity_kind": "observed",
            "score_confidence": "high",
        }
        selected = select_media_candidates([slower_historical_hit, faster_new_clip], limit=2)
        self.assertGreaterEqual(len(selected), 1)
        self.assertEqual(selected[0]["url"], faster_new_clip["url"])
        self.assertEqual(selected[0]["views_velocity_kind"], "observed")

    def test_exceptionally_fast_fresh_telegram_clip_can_pass_without_topic_keywords(self):
        candidate = {
            "id": "fast-uncategorized",
            "url": "https://t.me/gizmiztel/500001",
            "platform": "telegram",
            "source": "telegram_native_video",
            "telegram_channel": "gizmiztel",
            "title": "این اتفاق امروز افتاد",
            "description": "یک صحنه کوتاه در خیابان",
            "views": 25000,
            "age_hours": 0.8,
            "views_velocity_per_hour": 18000,
            "views_velocity_kind": "observed",
        }
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["url"], candidate["url"])
        self.assertEqual(selected[0]["views_velocity_kind"], "observed")

    def test_high_momentum_does_not_override_harassment_or_sensitive_content_gates(self):
        candidate = {
            "id": "harassment-news",
            "url": "https://t.me/gizmiztel/500002",
            "platform": "telegram",
            "source": "telegram_native_video",
            "telegram_channel": "gizmiztel",
            "title": "مرد هرزه چینی، زنی جوان را در وسط خیابان مورد آزار و اذیت قرار داد",
            "description": "گزارش یک حادثه خیابانی",
            "views": 90000,
            "age_hours": 1,
            "views_velocity_per_hour": 35000,
            "views_velocity_kind": "observed",
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_scheduler_sends_at_most_one_video_per_run(self):
        self.assertEqual(MAX_SENDS, 1)

    def test_empty_pool_invokes_one_public_source_fallback_sweep(self):
        previous = {"items": []}
        with patch("content_pipeline.social_discovery.main", return_value=0) as discover:
            self.assertTrue(run_fallback_discovery_sweep(previous))
        discover.assert_called_once_with(force_fallback=True, _previous_payload=previous)

    def test_fallback_sweep_failure_is_reported_without_raising(self):
        with patch("content_pipeline.social_discovery.main", side_effect=RuntimeError("temporary source failure")):
            self.assertFalse(run_fallback_discovery_sweep({"items": []}))

    def test_duration_content_gate_rejects_very_short_clip_even_when_technically_valid(self):
        item = {
            "follow_growth_score": 90,
            "hook_signals": ["surprise_or_reveal"],
            "title": "A teaser",
        }
        ok, reason = duration_content_gate(item, {"passed": True, "duration_seconds": 6.13})
        self.assertFalse(ok)
        self.assertEqual(reason, "clip_too_short_for_substantive_content")

    def test_duration_content_gate_rejects_channel_invite_cta(self):
        item = {
            "follow_growth_score": 90,
            "hook_signals": ["surprise_or_reveal"],
            "title": "اگه هر روز گلاب بخوری میدونی چه اتفاق شگفت انگیزی برای بدنت میفته 👇 https://t.me/+X38jh3w2_jI0ZDg0",
        }
        ok, reason = duration_content_gate(item, {"passed": True, "duration_seconds": 12})
        self.assertFalse(ok)
        self.assertEqual(reason, "cta_or_invite_heavy_source_caption")

    def test_video_quality_passes_for_decodable_varied_clip(self):
        rows = [
            {"mean_luma": 80, "std_luma": 20, "difference_from_previous": 0},
            {"mean_luma": 90, "std_luma": 22, "difference_from_previous": 12},
            {"mean_luma": 100, "std_luma": 25, "difference_from_previous": 18},
            {"mean_luma": 95, "std_luma": 21, "difference_from_previous": 10},
            {"mean_luma": 85, "std_luma": 19, "difference_from_previous": 8},
        ]
        result = evaluate_video_metrics(24, 720, 1280, rows)
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["score"], 60)
        self.assertEqual(result["reason"], "technical_checks_passed")

    def test_black_or_broken_video_is_rejected(self):
        rows = [{"mean_luma": 0, "std_luma": 0} for _ in range(5)]
        result = evaluate_video_metrics(24, 720, 1280, rows)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "mostly_black_frames")

    def test_too_short_video_is_rejected(self):
        rows = [{"mean_luma": 80, "std_luma": 20} for _ in range(5)]
        result = evaluate_video_metrics(2, 720, 1280, rows)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "duration_out_of_range")


    def test_source_caption_comes_from_selected_post_block(self):
        block = find_telegram_post_block(self.page, "demo_channel", "200")
        self.assertEqual(extract_telegram_post_caption(block), "عنوان پست دوم")

    def test_caption_is_only_the_exact_source_post_caption(self):
        caption = render_caption({
            "title": "عنوان نامرتبط از سرنخ قبلی",
            "source_post_caption": "  عنوان واقعی از خود پست منبع  ",
            "url": "https://t.me/demo_channel/200",
            "platform": "telegram",
            "follow_growth_score": 60,
            "age_hours": 2,
        })
        self.assertEqual(caption, "عنوان واقعی از خود پست منبع")
        self.assertNotIn("عنوان نامرتبط از سرنخ قبلی", caption)
        self.assertNotIn("https://t.me/demo_channel/200", caption)
        self.assertNotIn("منبع:", caption)
        self.assertNotIn("بازبینی خصوصی", caption)

    def test_recipe_caption_becomes_a_short_hook_not_an_ingredient_list(self):
        caption = render_caption({
            "title": "#اسنک شکلاتی 😘 مواد لازم: ۸۰ گرم شکلات تلخ و کینوا پف",
            "source_post_caption": "#اسنک شکلاتی 😘 مواد لازم: ۸۰ گرم شکلات تلخ و کینوا پف",
            "description": "طرز تهیه و مواد لازم برای اسنک شکلاتی؛ لینک در بیو",
            "hook_signals": ["food_cooking"],
        })
        self.assertEqual(caption, "این اسنک شکلاتی رو ببین؛ وسوسه‌کننده‌ست 🍫🤤")
        self.assertLess(len(caption), 90)
        self.assertNotIn("مواد لازم", caption)
        self.assertNotIn("۸۰ گرم", caption)
        self.assertNotIn("لینک", caption)

    def test_science_caption_becomes_a_curiosity_hook(self):
        caption = render_caption({
            "title": "فرایند تشکیل تخم مرغ در بدن مرغ",
            "source_post_caption": "فرایند تشکیل تخم مرغ در بدن مرغ؛ از شکل‌گیری تا خروج",
            "hook_signals": ["science_curiosity"],
        })
        self.assertEqual(caption, "تا حالا فکر کردی تخم‌مرغ چطور شکل می‌گیره؟ 🥚👀")
        self.assertNotIn("از شکل‌گیری تا خروج", caption)

    def test_biography_style_source_is_rewritten_as_a_topic_caption(self):
        caption = render_caption({
            "title": "بیوگرافی کامل خواننده؛ سوابق حرفه‌ای و زندگی‌نامه از کودکی تا امروز",
            "source_post_caption": "بیوگرافی کامل خواننده؛ سوابق حرفه‌ای و زندگی‌نامه از کودکی تا امروز",
            "description": "زندگی شخصی، سال تولد، پیشینه و فعالیت‌های حرفه‌ای",
            "hook_signals": ["music_or_dance"],
        })
        self.assertEqual(caption, "این اجرا چند ثانیه مکث می‌ارزه 🎶✨")
        self.assertNotIn("بیوگرافی", caption)
        self.assertNotIn("متولد", caption)

    def test_caption_falls_back_to_title_only_without_metadata(self):
        caption = render_caption({
            "title": "عنوان اصلی پست",
            "url": "https://t.me/demo_channel/200",
            "platform": "telegram",
            "follow_growth_score": 60,
            "age_hours": 2,
        })
        self.assertEqual(caption, "عنوان اصلی پست")


    def test_caption_sanitizer_removes_links_handles_promo_and_zero_width_padding(self):
        raw = (
            "#اسنک شکلاتی 😘 😘 😘 ✅ مواد لازم: ۸۰ گرم شکلات تلخ و ۱/۳ پیمانه کینوا پف "
            "#جادوی_تندیس #خلق_طعم_رؤیاها #لذت آشپزی "
            "لینک زیر را بزن ۱۰۰ سال عمر کن https://t.me/+invite @Lezath_Ashpazi "
            "شعبه جدید کانال دوم"
        )
        caption = clean_review_caption(raw)
        self.assertIn("اسنک شکلاتی", caption)
        self.assertIn("مواد لازم", caption)
        self.assertIn("۸۰ گرم شکلات تلخ", caption)
        for unwanted in ("https://", "t.me/", "@Lezath", "لینک زیر را بزن", "جادوی", "خلق طعم", "شعبه جدید"):
            with self.subTest(unwanted=unwanted):
                self.assertNotIn(unwanted, caption)
        self.assertLess(caption.count("😘"), 2)

    def test_caption_sanitizer_removes_zero_width_padding_but_preserves_persian_zwnj(self):
        caption = clean_review_caption(
            "از شکل‌گیری تا خروج؛ این فرآیند می‌کند یک مسیر طبیعی را نشان می‌دهد"
            + ("\u200c" * 40)
        )
        self.assertIn("می‌کند", caption)
        self.assertIn("از شکل‌گیری تا خروج", caption)
        self.assertNotIn("\u200c\u200c", caption)
        self.assertFalse(caption.endswith("\u200c"))

    def test_generic_day_greeting_is_not_a_video_caption_candidate(self):
        caption = clean_review_caption(
            "🗓 یک شنبه ۱۹ مهر ماه روز پاییزیتون عالی و بینظیر "
            "ان شاءالله امروز برای تک تک دوستانم پراز خیر و برکت و موفقیت "
            "روزتون زیبا و در پناه خدا"
        )
        self.assertTrue(is_generic_greeting_caption(caption, {"wholesome_or_heartwarming"}))
        self.assertFalse(
            is_generic_greeting_caption("طرز تهیه کیک شکلاتی؛ روزتون زیبا", {"food_cooking"})
        )

    def test_selector_rejects_generic_greeting_even_when_scored_as_wholesome(self):
        candidate = {
            "id": "generic-day-greeting",
            "url": "https://t.me/keereker/999999",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "روز پاییزیتون عالی و بینظیر؛ روزتون زیبا و در پناه خدا",
            "description": "",
            "age_hours": 2,
        }
        with patch(
            "content_pipeline.send_review_videos.annotate_candidate",
            side_effect=lambda item: {
                **item,
                "follow_growth_score": 60.0,
                "score_confidence": "high",
                "hook_signals": ["wholesome_or_heartwarming"],
                "news_risk_signal": False,
                "sensitive_risk_signal": False,
                "safety_review_signal": False,
                "compilation_signal": False,
                "promotional_signal": False,
            },
        ):
            self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_balanced_shortlist_limits_cooking_to_one_of_first_three_when_alternatives_exist(self):
        candidates = []
        examples = [
            ("food-1", "اسنک شکلاتی", "food_cooking"),
            ("food-2", "کیک شکلاتی", "food_cooking"),
            ("food-3", "کوبیده", "food_cooking"),
            ("food-4", "نان خانگی", "food_cooking"),
            ("funny", "یک اتفاق خنده‌دار", "comedy"),
            ("animal", "واکنش گربه", "animals"),
            ("skill", "حرکت تماشایی", "skill_or_spectacle"),
            ("science", "یک پدیده علمی جالب", "science_curiosity"),
        ]
        for index, (ident, title, lane) in enumerate(examples):
            candidates.append({
                "id": ident,
                "url": f"https://t.me/demo/{index + 1}",
                "platform": "telegram",
                "title": title,
                "hook_signals": [lane],
                "follow_growth_score": 80 - index,
                "age_hours": 2,
            })

        selected = balanced_candidate_shortlist(candidates, recent_topics=[], limit=3)
        food_count = sum(
            1 for item in selected if content_pillar_profile(item)[1] == "food_cooking"
        )
        self.assertEqual(len(selected), 3)
        self.assertLessEqual(food_count, 1)
        self.assertGreaterEqual(len(selected) - food_count, 2)

    def test_rolling_food_cap_prefers_non_food_after_six_recent_food_videos(self):
        history = [{"pillar": "adjacent", "topic": "food_cooking"} for _ in range(6)]
        candidates = [
            {
                "id": "food-next",
                "url": "https://t.me/demo/food-next",
                "platform": "telegram",
                "title": "اسنک شکلاتی",
                "hook_signals": ["food_cooking"],
                "follow_growth_score": 99,
                "age_hours": 1,
            },
            {
                "id": "fun-next",
                "url": "https://t.me/demo/fun-next",
                "platform": "telegram",
                "title": "یک اتفاق خنده‌دار",
                "hook_signals": ["comedy"],
                "follow_growth_score": 70,
                "age_hours": 1,
            },
            {
                "id": "skill-next",
                "url": "https://t.me/demo/skill-next",
                "platform": "telegram",
                "title": "حرکت تماشایی",
                "hook_signals": ["skill_or_spectacle"],
                "follow_growth_score": 65,
                "age_hours": 1,
            },
            {
                "id": "tips-next",
                "url": "https://t.me/demo/tips-next",
                "platform": "telegram",
                "title": "ترفند کاربردی",
                "hook_signals": ["practical_tips_or_life_hacks"],
                "follow_growth_score": 60,
                "age_hours": 1,
            },
        ]
        selected = balanced_candidate_shortlist(candidates, recent_topics=history, limit=3)
        self.assertTrue(selected)
        self.assertNotIn("food_cooking", [content_pillar_profile(item)[1] for item in selected])

    def test_pillar_mix_uses_core_adjacent_and_experimental_topics(self):
        lanes = [
            ("reaction1", "موقعیت بامزه", "comedy"),
            ("reaction2", "واکنش حیوان", "animals"),
            ("reaction3", "واکنش غیرمنتظره", "human_reaction_or_relatable"),
            ("visual1", "حرکت تماشایی", "skill_or_spectacle"),
            ("visual2", "شوت تماشایی", "sports_or_athletic_skill"),
            ("visual3", "قبل و بعد", "transformation_or_satisfying"),
            ("curiosity1", "آزمایش علمی", "science_curiosity"),
            ("curiosity2", "نمایش فناوری", "technology_demo"),
            ("adjacent1", "ترفند کاربردی", "practical_tips_or_life_hacks"),
            ("adjacent2", "آشپزی", "food_cooking"),
            ("adjacent3", "طبیعت", "nature_and_wildlife"),
            ("experiment1", "جای دیدنی", "travel_and_places"),
            ("experiment2", "خطای دید", "puzzles_and_visual_illusions"),
            ("experiment3", "نوستالژی", "nostalgia_and_then_now"),
        ]
        candidates = [
            {
                "id": ident,
                "url": f"https://t.me/mix/{index + 1}",
                "platform": "telegram",
                "title": title,
                "hook_signals": [lane],
                "follow_growth_score": 80 - index,
                "age_hours": 1,
            }
            for index, (ident, title, lane) in enumerate(lanes)
        ]
        selected = balanced_candidate_shortlist(candidates, recent_topics=[], limit=10)
        counts = {}
        for item in selected:
            pillar, _topic = content_pillar_profile(item)
            counts[pillar] = counts.get(pillar, 0) + 1
        core_count = sum(counts.get(key, 0) for key in (
            "reaction_and_comedy", "visual_wow", "curiosity_and_process"
        ))
        self.assertEqual(len(selected), 10)
        self.assertGreaterEqual(core_count, 6)
        self.assertLessEqual(core_count, 8)
        self.assertGreaterEqual(counts.get("adjacent", 0), 1)
        self.assertLessEqual(counts.get("adjacent", 0), 3)
        self.assertGreaterEqual(counts.get("experimental", 0), 1)
        self.assertLessEqual(counts.get("experimental", 0), 2)

    def test_bootstrap_topic_history_from_sent_media_fingerprints(self):
        state = {
            "media_reviewed_fingerprints": [
                {"url": "https://t.me/foodchannel/42", "sha256": "abc"}
            ]
        }
        items = [{
            "url": "https://t.me/foodchannel/42",
            "hook_signals": ["food_cooking"],
            "title": "آشپزی",
        }]
        history = infer_recent_topic_history(state, items)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["topic"], "food_cooking")
        self.assertEqual(history[0]["pillar"], "adjacent")
        self.assertTrue(history[0]["inferred"])

    def test_accepts_multiple_mainstream_evergreen_topics(self):
        examples = [
            ("food", "طرز تهیه نان خانگی با چند ماده ساده", "food_cooking"),
            ("tips", "یک ترفند کاربردی برای مرتب کردن وسایل خانه", "practical_tips_or_life_hacks"),
            ("heartwarming", "دیدار دوباره پدر و دختر پس از سال‌ها", "wholesome_or_heartwarming"),
            ("sports", "شوت تماشایی در بسکتبال", "sports_or_athletic_skill"),
            ("nature", "تصاویر شگفت‌انگیز حیات وحش", "nature_and_wildlife"),
            ("science", "یک آزمایش علمی ساده نشان می‌دهد چطور کار می‌کند", "science_curiosity"),
            ("diy", "ساخت یک وسیله چوبی دست‌ساز از صفر", "diy_and_creativity"),
            ("cleaning", "قبل و بعد نظافت عمیق آشپزخانه", "home_organization_or_cleaning"),
            ("music", "اجرای زنده نوازندگی پیانو", "music_or_dance"),
        ]
        for slug, title, expected_lane in examples:
            with self.subTest(topic=slug):
                candidate = {
                    "id": f"mainstream-{slug}",
                    "url": f"https://t.me/example/{slug}",
                    "platform": "telegram",
                    "source": "telegram_native_video",
                    "title": title,
                    "views": 90000,
                    "likes": 2500,
                    "comments": 120,
                    "age_hours": 2,
                }
                selected = select_media_candidates([candidate], limit=1)
                self.assertEqual(len(selected), 1)
                self.assertIn(expected_lane, selected[0]["hook_signals"])

    def test_current_affairs_cannot_pass_by_also_matching_a_target_lane(self):
        candidate = {
            "id": "comedy-current-affairs",
            "url": "https://t.me/example/comedy-current-affairs",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "طنز درباره درگیری مرزی و بازداشت افراد",
            "views": 90000,
            "likes": 2000,
            "comments": 100,
            "age_hours": 2,
        }
        self.assertEqual(select_media_candidates([candidate], limit=1), [])


    def test_same_source_url_with_tracking_variants_is_selected_once(self):
        base = {
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A funny cat has a surprising reaction",
            "description": "Unexpected animal reaction caught on camera.",
            "views": 100000,
            "age_hours": 2,
        }
        candidates = [
            {**base, "id": "duplicate-a", "url": "https://t.me/example/301?single"},
            {**base, "id": "duplicate-b", "url": "https://t.me/example/301/"},
        ]
        selected = select_media_candidates(candidates, limit=3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(canonical_media_url(candidates[0]["url"]), canonical_media_url(candidates[1]["url"]))


    def test_prefers_new_subject_lane_before_repeating_a_higher_scoring_topic(self):
        base = {
            "platform": "telegram",
            "source": "telegram_native_video",
            "likes": 12000,
            "comments": 500,
            "age_hours": 2,
        }
        candidates = [
            {
                **base, "id": "tech-a", "url": "https://t.me/example/401",
                "title": "A new AI robot technology demo", "views": 800000,
            },
            {
                **base, "id": "tech-b", "url": "https://t.me/example/402",
                "title": "AI technology demo from a new gadget", "views": 700000,
            },
            {
                **base, "id": "cat-video", "url": "https://t.me/example/403",
                "title": "A funny cat has a surprising reaction to a toy", "views": 100000,
            },
        ]
        selected = select_media_candidates(candidates, limit=2)
        self.assertEqual(len(selected), 2)
        selected_ids = {item["id"] for item in selected}
        self.assertIn("cat-video", selected_ids)
        self.assertEqual(len(selected_ids.intersection({"tech-a", "tech-b"})), 1)
        selected_lanes = [
            next(lane for lane in item["hook_signals"] if lane in {"technology_demo", "animals", "comedy"})
            for item in selected
        ]
        self.assertEqual(len(set(selected_lanes)), 2)

    def test_youtube_watch_and_short_urls_normalize_to_same_video(self):
        watch = canonical_media_url("https://www.youtube.com/watch?v=abc123&utm_source=feed")
        short = canonical_media_url("https://youtube.com/shorts/abc123?feature=share")
        self.assertEqual(watch, short)

    def test_perceptual_fingerprint_detects_same_clip_after_reencoding(self):
        frames = [
            "0000000000000000",
            "0000000000000001",
            "0000000000000003",
            "0000000000000007",
            "000000000000000f",
        ]
        self.assertTrue(media_fingerprints_match(
            {"sha256": "original-file", "frame_hashes": frames},
            {"sha256": "reencoded-file", "frame_hashes": frames},
        ))

    def test_perceptual_fingerprint_does_not_match_different_visuals(self):
        self.assertFalse(media_fingerprints_match(
            {"sha256": "file-a", "frame_hashes": ["0000000000000000"] * 5},
            {"sha256": "file-b", "frame_hashes": ["ffffffffffffffff"] * 5},
        ))

    def test_long_clip_needs_stronger_topic_fit(self):
        two_lanes = {"follow_growth_score": 70, "hook_signals": ["animals", "comedy"]}
        qc = {"passed": True, "duration_seconds": 50}
        self.assertEqual(duration_content_gate(two_lanes, qc), (True, "duration_and_topic_gate_passed"))
        one_lane = {"follow_growth_score": 80, "hook_signals": ["animals"]}
        self.assertEqual(duration_content_gate(one_lane, qc), (False, "long_clip_requires_stronger_topic_fit"))
        low_score = {"follow_growth_score": 52, "hook_signals": ["animals", "comedy"]}
        self.assertEqual(duration_content_gate(low_score, qc), (False, "long_clip_requires_stronger_topic_fit"))
        vague_topic = {
            "follow_growth_score": 80,
            "hook_signals": ["surprise_or_reveal", "human_reaction_or_relatable"],
        }
        self.assertEqual(duration_content_gate(vague_topic, qc), (False, "long_clip_requires_stronger_topic_fit"))

    def test_long_single_story_with_exceptional_recent_momentum_can_pass(self):
        item = {
            "follow_growth_score": 58.7,
            "hook_signals": ["animals"],
            "title": "ویدیوی وایرال شده از سگ فلجی که به کمک چند نفر توانست حرکت کند",
            "views": 73300,
            "age_hours": 10.5,
        }
        quality = {"passed": True, "duration_seconds": 54.9}
        self.assertEqual(
            duration_content_gate(item, quality),
            (True, "duration_and_topic_gate_passed_strong_momentum"),
        )

    def test_long_uncategorized_telegram_clip_needs_exceptional_momentum(self):
        item = {
            "follow_growth_score": 54.0,
            "hook_signals": [],
            "source": "telegram_native_video",
            "platform": "telegram",
            "title": "این اتفاق امروز افتاد",
            "views": 25000,
            "age_hours": 0.8,
            "views_velocity_per_hour": 18000,
            "views_velocity_kind": "observed",
        }
        quality = {"passed": True, "duration_seconds": 52.0}
        self.assertEqual(
            duration_content_gate(item, quality),
            (True, "duration_and_topic_gate_passed_strong_momentum"),
        )

    def test_long_single_story_without_momentum_still_fails(self):
        item = {
            "follow_growth_score": 58.7,
            "hook_signals": ["animals"],
            "title": "A quiet animal clip",
            "views": 1800,
            "age_hours": 10.5,
        }
        quality = {"passed": True, "duration_seconds": 54.9}
        self.assertEqual(
            duration_content_gate(item, quality),
            (False, "long_clip_requires_stronger_topic_fit"),
        )

    def test_video_longer_than_60_seconds_is_rejected_by_technical_gate(self):
        rows = [{"mean_luma": 80, "std_luma": 20} for _ in range(5)]
        result = evaluate_video_metrics(75, 720, 1280, rows)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "duration_out_of_range")


    def test_moderate_but_low_view_clip_is_held_back(self):
        candidate = {
            "id": "moderate-cat",
            "url": "https://t.me/funnycat/456",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "A cat sits quietly on a table",
            "description": "",
            "views": 1200,
            "age_hours": 20,
        }
        # A topic match and recent timestamp are insufficient without strong
        # observed reach/momentum: don't send a merely accessible video.
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
