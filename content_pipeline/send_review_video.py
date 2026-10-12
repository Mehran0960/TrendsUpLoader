#!/usr/bin/env python3
"""Send the rights-gated output to a private Telegram review chat, not the audience channel."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

MAX_CAPTION = 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024


def find_latest():
    videos = sorted(
        Path("out").glob("**/video.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not videos:
        raise FileNotFoundError("No video.mp4 found under out/")
    video = videos[0]
    metadata_path = video.parent / "metadata.json"
    rights_path = video.parent / "rights_manifest.json"
    if not metadata_path.exists() or not rights_path.exists():
        raise RuntimeError("Video metadata or rights manifest is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    rights = json.loads(rights_path.read_text(encoding="utf-8"))

    if metadata.get("script_mode") != "curated":
        raise RuntimeError("Review send blocked: script is not curated")
    if metadata.get("quality_gate") != "passed_publish":
        raise RuntimeError("Review send blocked: editorial quality gate did not pass")
    if metadata.get("script_quality") != "passed":
        raise RuntimeError("Review send blocked: script quality did not pass")
    try:
        duration = float(metadata.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration < 16:
        raise RuntimeError(f"Review send blocked: video duration is too short ({duration:.2f}s)")
    if rights.get("automated_publishable") is not True or rights.get("assets_cleared") is not True:
        raise RuntimeError("Review send blocked: rights/provenance gate did not pass")
    if rights.get("script_mode") != "curated":
        raise RuntimeError("Review send blocked: rights manifest does not attest curated script")
    if video.stat().st_size <= 0 or video.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("Review send blocked: video file is empty or exceeds Telegram's 50 MiB bot limit")
    return video, metadata


def multipart(fields, filename, payload):
    boundary = "----TrendRadarReview" + uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="video"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: video/mp4\r\n\r\n",
        payload,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(chunks)


def main():
    if os.environ.get("TELEGRAM_REVIEW_SEND_ENABLED", "").strip().lower() != "true":
        raise RuntimeError("Review send blocked: explicit TELEGRAM_REVIEW_SEND_ENABLED=true is required")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Private Telegram review credentials are missing; no video was sent")

    video, metadata = find_latest()
    title = str(metadata.get("display_title_fa") or metadata.get("title") or "ویدئوی آزمایشی").strip()
    trend = metadata.get("trend_inspiration") if isinstance(metadata.get("trend_inspiration"), dict) else None
    if trend:
        inspiration_title = str(trend.get("title") or "").strip()
        trend_url = str(trend.get("url") or "").strip()
        category = str(trend.get("category") or "").strip()
        caption = (
            "ویدئوی اصیل برای بازبینی خصوصی؛ انتشار عمومی انجام نشده است.\n\n"
            + title
        )
        if inspiration_title:
            caption += "\n\nدستهٔ ترند الهام‌بخش: " + category + "\n" + inspiration_title
        if trend_url:
            caption += "\n\nمرجع سیگنال ترند (فقط الهام؛ ویدئوی آن بازنشر نشده): " + trend_url
    else:
        caption = (
            "نسخهٔ آزمایشی برای بررسی کیفیت؛ این پیام برای بازبینی خصوصی است و انتشار عمومی انجام نمی‌دهد.\n\n"
            + title
        )
        source_url = str(metadata.get("source_url") or "").strip()
        if source_url:
            caption += "\n\nمرجع موضوع: " + source_url
    caption = caption[:MAX_CAPTION]

    boundary, body = multipart(
        {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"},
        video.name,
        video.read_bytes(),
    )
    request = Request(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "trends-uploader-private-review/1.0",
        },
    )
    with urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the review video: " + json.dumps(result, ensure_ascii=False))

    message = result.get("result") or {}
    receipt = {
        "platform": "telegram_private_review",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "message_id": message.get("message_id"),
        "video_path": str(video),
        "video_bytes": video.stat().st_size,
        "title": title,
        "content_key": metadata.get("content_key"),
        "public_channel_publish_attempted": False,
    }
    (video.parent / "telegram_review_delivery.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"TELEGRAM_PRIVATE_REVIEW_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
