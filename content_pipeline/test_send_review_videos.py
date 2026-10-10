"""Regression tests for source-post/media matching in private video review."""
import unittest

from content_pipeline.send_review_videos import (
    extract_telegram_post_video_urls,
    extract_telegram_post_caption,
    find_telegram_post_block,
    render_caption,
    evaluate_video_metrics,
    select_media_candidates,
    canonical_media_url,
    media_fingerprints_match,
    duration_content_gate,
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

    def test_rejects_pageant_fashion_clip_with_persia_in_title(self):
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
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

    def test_rejects_unrelated_runway_news_without_target_hook(self):
        candidate = {
            "id": "runway-news",
            "url": "https://t.me/gizmiztel/163079",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "مدل ایرانی در بخش لباس ملی با لباسی الهام‌گرفته از تاریخ روی صحنه رفت",
            "description": "",
            "views": 14000,
            "age_hours": 0.2,
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

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

    def test_accepts_strong_fresh_native_telegram_clip_without_view_counters(self):
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
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["score_confidence"], "low")
        self.assertTrue(
            set(selected[0]["hook_signals"]).intersection(
                {"animals", "comedy", "skill_or_spectacle", "technology_demo",
                 "food_cooking", "practical_tips_or_life_hacks",
                 "wholesome_or_heartwarming", "sports_or_athletic_skill",
                 "nature_and_wildlife", "science_curiosity", "diy_and_creativity",
                 "home_organization_or_cleaning", "music_or_dance"}
            )
        )

    def test_allows_high_scoring_specific_telegram_clip_up_to_72_hours(self):
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
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertGreaterEqual(selected[0]["follow_growth_score"], 50)
        self.assertIn("animals", selected[0]["hook_signals"])

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

    def test_skips_youtube_file_relay_while_runner_has_antibot_block(self):
        candidate = {
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
        }
        self.assertEqual(select_media_candidates([candidate], limit=3), [])

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

    def test_caption_prefers_exact_source_post_caption(self):
        caption = render_caption({
            "title": "عنوان نامرتبط از سرنخ قبلی",
            "source_post_caption": "عنوان واقعی از خود پست منبع",
            "url": "https://t.me/demo_channel/200",
            "platform": "telegram",
        })
        self.assertIn("عنوان ثبت‌شده در پست منبع: عنوان واقعی از خود پست منبع", caption)
        self.assertNotIn("عنوان نامرتبط از سرنخ قبلی", caption)

    def test_caption_labels_title_as_source_post_text(self):
        caption = render_caption({
            "title": "عنوان اصلی پست",
            "url": "https://t.me/demo_channel/200",
            "platform": "telegram",
            "follow_growth_score": 60,
            "age_hours": 2,
        })
        self.assertIn("عنوان ثبت‌شده در پست منبع: عنوان اصلی پست", caption)
        self.assertIn("https://t.me/demo_channel/200", caption)


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

    def test_video_longer_than_60_seconds_is_rejected_by_technical_gate(self):
        rows = [{"mean_luma": 80, "std_luma": 20} for _ in range(5)]
        result = evaluate_video_metrics(75, 720, 1280, rows)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "duration_out_of_range")


    def test_moderate_but_relevant_fresh_clip_is_kept_for_private_review(self):
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
        selected = select_media_candidates([candidate], limit=3)
        self.assertEqual(len(selected), 1)
        self.assertIn("animals", selected[0]["hook_signals"])
        self.assertGreaterEqual(selected[0]["follow_growth_score"], 42)
        self.assertLess(selected[0]["follow_growth_score"], 48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
