"""Send a text-only public-trend shortlist to a configured private Telegram review chat."""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

QUEUE_PATH = Path("out/trend_review_queue.json")
DELIVERY_PATH = Path("out/trend_review_delivery.json")
CATEGORY_LABELS = {
    "tech": "فناوری",
    "animals": "حیات‌وحش و حیوانات",
    "sports": "ورزش",
    "food": "غذا",
    "human_funny": "طنز و واکنش",
    "wow": "شگفتی و مهارت",
    "satisfying": "فرایندهای رضایت‌بخش",
    "travel": "مکان‌ها و سفر",
    "cars": "خودرو",
    "talent": "استعداد و اجرا",
    "beauty_style": "مد و سبک",
}


def format_message(queue):
    items = queue.get("items") or []
    lines = [
        "🧭 صف بررسی ترندهای تازه",
        "این پیام فقط فهرست کشف و تحلیل است؛ هیچ ویدئویی دانلود یا تولید نشده و انتشار عمومی انجام نشده.",
        "",
    ]
    for i, item in enumerate(items, 1):
        category = CATEGORY_LABELS.get(str(item.get("category") or ""), str(item.get("category") or "نامشخص"))
        title = str(item.get("title") or "بدون عنوان").replace("\n", " ").strip()
        url = str(item.get("url") or "").strip()
        demand = item.get("demand_score", 0)
        views = item.get("views", 0)
        regions = item.get("chart_region_count", 0)
        age = item.get("age_hours", "?")
        lines.extend([
            f"{i}. {title[:200]}",
            f"دسته: {category} | تقاضا: {demand}/100 | بازدید: {views:,} | مناطق: {regions} | سن: {age} ساعت",
            url,
            "",
        ])
    lines.append("لینک‌ها فقط سیگنال ترند عمومی‌اند؛ مجوز بازنشر یا تأیید کیفیت نیستند.")
    message = "\n".join(lines).strip()
    # Telegram's sendMessage limit is 4096 characters. Keep a safety margin.
    return message[:3900]


def send_message(token, chat_id, message):
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "trends-uploader-text-review-queue/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        # Never include the bot token in errors/logs.
        raise RuntimeError("Telegram rejected the text-only review queue.")
    return result.get("result") or {}


def main():
    if not QUEUE_PATH.exists():
        print("TREND_REVIEW_QUEUE_NOT_FOUND")
        return 0
    try:
        queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("TREND_REVIEW_QUEUE_INVALID")
        return 0
    items = queue.get("items") or []
    if queue.get("status") != "ready" or not items:
        print("TREND_REVIEW_QUEUE_EMPTY; nothing sent.")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("PRIVATE_TELEGRAM_NOT_CONFIGURED; queue artifact will be retained.")
        return 0

    message = format_message(queue)
    sent = send_message(token, chat_id, message)
    DELIVERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "platform": "telegram_private_text_review",
        "message_id": sent.get("message_id"),
        "chat_id_present": bool(chat_id),
        "sent_at": sent.get("date"),
        "signal_ids": [str(x.get("signal_id")) for x in items if x.get("signal_id")],
        "urls": [str(x.get("url")) for x in items if x.get("url")],
        "item_count": len(items),
        "video_production": False,
        "media_downloaded": False,
        "public_publishing_attempted": False,
    }
    DELIVERY_PATH.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "sent_to_private_text_review",
        "item_count": len(items),
        "video_production": False,
        "media_downloaded": False,
        "public_publishing_attempted": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Keep failure diagnostics useful without printing a token or request payload.
        print("TREND_REVIEW_SEND_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
