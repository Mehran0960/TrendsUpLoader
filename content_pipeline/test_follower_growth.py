"""Tests for follower-growth-first discovery scoring."""
import unittest

from content_pipeline.follower_growth import (
    annotate_candidate,
    expected_platform_for_query,
    rank_candidates,
)


class FollowerGrowthTests(unittest.TestCase):
    def test_fresh_shareable_video_outranks_stale_hard_news(self):
        rows = [
            {
                "url": "https://www.instagram.com/reel/news123/",
                "platform": "instagram",
                "source": "telegram_native_video",
                "title": "گزارش حمله نظامی و انفجار در منطقه",
                "views": 900000,
                "age_hours": 90,
                "discovered_at": "2026-10-10T10:00:00+00:00",
            },
            {
                "url": "https://www.instagram.com/reel/fun123/",
                "platform": "instagram",
                "source": "telegram_native_video",
                "title": "یه شوخی خنده‌دار؛ آخرش غافلگیر می‌شی!",
                "views": 80000,
                "age_hours": 2,
                "discovered_at": "2026-10-10T10:00:00+00:00",
            },
        ]
        ranked = rank_candidates(rows)
        self.assertEqual(ranked[0]["url"], "https://www.instagram.com/reel/fun123/")
        self.assertGreater(ranked[0]["follow_growth_score"], ranked[1]["follow_growth_score"])
        self.assertEqual(ranked[0]["candidate_action"], "prioritize_for_visual_and_rights_review")
        self.assertEqual(ranked[0]["rights_status"], "not_assessed")

    def test_unknown_metrics_are_low_confidence_and_never_auto_publishable(self):
        result = annotate_candidate({
            "url": "https://www.tiktok.com/@example/video/12345",
            "platform": "tiktok",
            "title": "گربه‌ای که یک حرکت عجیب انجام داد",
            "description": "ویدئوی بامزه",
        })
        self.assertEqual(result["score_confidence"], "low")
        self.assertEqual(result["candidate_action"], "manual_review")
        self.assertEqual(result["rights_status"], "not_assessed")
        self.assertIn("animals", result["hook_signals"])

    def test_protest_driven_current_affairs_is_flagged_as_news(self):
        result = annotate_candidate({
            "url": "https://t.me/example/124",
            "platform": "telegram",
            "title": "مسابقات لغو شد؛ تجمع، شعار و اعتراض شرکت‌کنندگان",
            "views": 90000,
            "age_hours": 2,
        })
        self.assertTrue(result["news_risk_signal"])
        self.assertEqual(result["content_lane"], "news_or_current_affairs")

    def test_persian_numbered_funny_clips_are_marked_as_roundup(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/watch?v=persianroundup",
            "platform": "youtube",
            "title": "8 تا فان🤣🤣#viral #خندارترین #طنز",
            "views": 9000,
            "likes": 500,
            "age_hours": 4,
        })
        self.assertTrue(result["compilation_signal"])
        self.assertEqual(result["candidate_action"], "deprioritize_multi_story_roundup")

    def test_explicit_api_offer_is_flagged_as_promotional(self):
        result = annotate_candidate({
            "url": "https://t.me/thezoomit/103620",
            "platform": "telegram",
            "title": "دسترسی رایگان به API مدل هوش مصنوعی؛ برای استفاده به این لینک مراجعه کنید",
            "description": "Qwen model API offer https://example.com/models",
            "views": 6000,
            "age_hours": 1,
        })
        self.assertTrue(result["promotional_signal"])
        self.assertEqual(result["candidate_action"], "deprioritize_promotional")

    def test_stale_video_is_deprioritized_even_with_strong_metrics(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/watch?v=staleexample",
            "platform": "youtube",
            "title": "Unexpected funny animal reaction",
            "views": 150000,
            "likes": 8000,
            "comments": 400,
            "age_hours": 60,
        })
        self.assertTrue(result["stale_signal"])
        self.assertEqual(result["candidate_action"], "deprioritize_stale_candidate")

    def test_compilation_roundups_are_downranked(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/watch?v=example123",
            "platform": "youtube",
            "title": "Ranking Best Eagle Moments",
            "views": 7000,
            "likes": 150,
            "age_hours": 20,
        })
        self.assertTrue(result["compilation_signal"])
        self.assertEqual(result["content_lane"], "multi_clip_roundup")
        self.assertEqual(result["candidate_action"], "deprioritize_multi_story_roundup")

    def test_youtube_channel_description_does_not_false_flag_single_event_as_news(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/watch?v=singleevent123",
            "platform": "youtube",
            "source": "youtube_public_api",
            "title": "This Little Girl Made One Mistake Then Her Brother Rushed In #shorts #wholesome",
            "description": "American politics, political analysis, government and election discussion in other uploads.",
            "views": 186000,
            "likes": 5000,
            "comments": 100,
            "age_hours": 29,
        })
        self.assertFalse(result["news_risk_signal"])
        self.assertNotEqual(result["content_lane"], "news_or_current_affairs")
        self.assertNotEqual(result["candidate_action"], "manual_review_current_affairs")

    def test_geopolitical_current_affairs_is_flagged_as_news(self):
        result = annotate_candidate({
            "url": "https://t.me/example/125",
            "platform": "telegram",
            "title": "رائفی‌پور: آمریکا را پایین کشیدن کاری ندارد؛ روسیه باید پالایشگاه‌های آمریکا را هدف بگیرد تا حملات اوکراین متوقف شود",
            "views": 102000,
            "age_hours": 6,
        })
        self.assertTrue(result["news_risk_signal"])
        self.assertEqual(result["content_lane"], "news_or_current_affairs")
        self.assertEqual(result["candidate_action"], "manual_review_current_affairs")


    def test_border_immigration_and_crime_rhetoric_is_flagged_as_current_affairs(self):
        result = annotate_candidate({
            "url": "https://t.me/example/777",
            "platform": "telegram",
            "source": "telegram_native_video",
            "title": "ندا شمس وکیل پایه یک دادگستری: مرزای شرق رو ولش کن تهران ناامنه؛ بیشترین تجاوز توسط افغانها انجام میشه",
            "views": 140000,
            "age_hours": 1,
        })
        self.assertTrue(result["news_risk_signal"])
        self.assertEqual(result["content_lane"], "news_or_current_affairs")
        self.assertEqual(result["candidate_action"], "manual_review_current_affairs")

    def test_sensitive_risk_is_rejected_for_review(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/shorts/abcdef",
            "platform": "youtube",
            "title": "ویدئوی خون و خشونت شدید",
            "views": 100000,
            "age_hours": 1,
        })
        self.assertEqual(result["candidate_action"], "reject_sensitive_or_manual_safety_review")

    def test_weapon_training_requires_manual_safety_review(self):
        result = annotate_candidate({
            "url": "https://t.me/example/123",
            "platform": "telegram",
            "title": "مهارت در باز و بسته کردن سلاح در آموزش نظامی",
            "views": 80000,
            "age_hours": 2,
        })
        self.assertTrue(result["safety_review_signal"])
        self.assertEqual(result["candidate_action"], "manual_safety_review")

    def test_search_domain_mismatch_can_be_filtered(self):
        self.assertEqual(expected_platform_for_query('site:instagram.com/reel/ funny video'), "instagram")
        self.assertEqual(expected_platform_for_query('site:youtube.com/shorts amazing'), "youtube")
        self.assertEqual(expected_platform_for_query('cute animals viral video'), "")

    def test_actual_interactions_raise_confidence(self):
        result = annotate_candidate({
            "url": "https://www.youtube.com/shorts/abcdef",
            "platform": "youtube",
            "title": "ترفند عجیب و باورنکردنی",
            "views": 50000,
            "likes": 2500,
            "comments": 150,
            "age_hours": 4,
        })
        self.assertEqual(result["score_confidence"], "high")
        self.assertIn(result["candidate_action"], {"prioritize_for_visual_and_rights_review", "manual_review"})


    def test_mainstream_evergreen_interests_are_classified(self):
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
                result = annotate_candidate({
                    "url": f"https://t.me/example/{slug}",
                    "platform": "telegram",
                    "title": title,
                    "views": 50000,
                    "age_hours": 3,
                })
                self.assertIn(expected_lane, result["hook_signals"])
                self.assertFalse(result["news_risk_signal"])

    def test_persia_name_does_not_trigger_jump_skill_lane(self):
        result = annotate_candidate({
            "url": "https://t.me/example/persia",
            "platform": "telegram",
            "title": "مدل ایرانی با لباسی به نام پرشیا روی صحنه رفت",
            "views": 25000,
            "age_hours": 2,
        })
        self.assertNotIn("skill_or_spectacle", result["hook_signals"])

    def test_current_affairs_still_flagged_even_when_title_uses_comedy_hook(self):
        result = annotate_candidate({
            "url": "https://t.me/example/comedy-news",
            "platform": "telegram",
            "title": "طنز درباره درگیری مرزی و بازداشت افراد",
            "views": 90000,
            "age_hours": 2,
        })
        self.assertIn("comedy", result["hook_signals"])
        self.assertTrue(result["news_risk_signal"])
        self.assertEqual(result["content_lane"], "news_or_current_affairs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
