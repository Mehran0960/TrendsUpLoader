"""Regression tests for source-post/media matching in private video review."""
import unittest

from content_pipeline.send_review_videos import (
    extract_telegram_post_video_urls,
    find_telegram_post_block,
    render_caption,
    evaluate_video_metrics,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
