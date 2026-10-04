#!/usr/bin/env python3
"""Publish a validated comedy remix to Telegram."""
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

MAX_CAPTION = 1024

def find_video():
    videos = sorted(Path("out").glob("**/video.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not videos:
        return None
    video = videos[0]
    meta_path = video.parent / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return video, meta

def multipart(fields, file_field, filename, payload, content_type):
    boundary = "----ComedyRemix" + uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        chunks += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"), b"\r\n",
        ]
    chunks += [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        payload, b"\r\n", f"--{boundary}--\r\n".encode(),
    ]
    return boundary, b"".join(chunks)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Comedy Telegram publish skipped: secrets are not configured.")
        return 0

    found = find_video()
    if found is None:
        print("Comedy Telegram publish skipped: no video.")
        return 0

    video, meta = found
    if meta.get("content_type") != "comedy_remix":
        raise RuntimeError("Refusing to publish non-comedy content.")
    if meta.get("quality_gate") != "passed_publish":
        raise RuntimeError("Refusing to publish a failed quality gate.")
    if float(meta.get("duration_seconds") or 0) < 18:
        raise RuntimeError("Refusing to publish a short comedy video.")
    if video.stat().st_size > 50 * 1024 * 1024:
        raise RuntimeError("Video exceeds Telegram bot upload limit.")

    title = str(meta.get("display_title_fa") or "😂 یه ترکیب کاملاً عادی").strip()
    caption = (title + "\n\n" + "چند ثانیه بیشتر طول نمی‌کشه؛ ولی آخرش رو از دست نده 😂")[:MAX_CAPTION]

    boundary, body = multipart(
        {"chat_id": chat_id, "caption": caption},
        "video", video.name, video.read_bytes(), "video/mp4"
    )
    req = Request(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data=body, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "comedy-remix-telegram-publisher/1.0",
        },
    )
    with urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError("Telegram API error: " + json.dumps(result, ensure_ascii=False))

    publish = {
        "platform": "telegram",
        "message_id": (result.get("result") or {}).get("message_id"),
        "title": title,
        "content_key": meta.get("content_key"),
    }
    (video.parent / "publish.json").write_text(json.dumps(publish, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(publish, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
