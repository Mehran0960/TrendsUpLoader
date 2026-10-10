#!/usr/bin/env python3
"""Zero-cost Telegram publisher for the generated video artifact."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

MAX_CAPTION = 1024

def find_latest():
    videos = sorted(Path("out").glob("**/video.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not videos:
        print("Telegram publish skipped: no video was generated.")
        return None
    video = videos[0]
    meta_path = video.parent / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return video, meta

def multipart(fields, file_field, filename, payload, content_type):
    boundary = "----TrendRadar" + uuid.uuid4().hex
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

def make_caption(meta):
    title = str(meta.get("display_title_fa") or meta.get("title") or "موضوع جدید").strip()
    body = " ".join(str(s.get("text") or "").strip() for s in meta.get("segments", []) if str(s.get("text") or "").strip())
    source_url = str(meta.get("source_url") or "").strip()
    out = title
    if body:
        out += "\n\n" + body
    if source_url:
        out += "\n\nمنبع: " + source_url
    return out[:MAX_CAPTION]

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram publish skipped: secrets are not configured.")
        return 0

    found = find_latest()
    if found is None:
        return 0
    video, meta = found
    if str(meta.get("quality_gate") or "") != "passed_publish":
        print("Telegram publish skipped: metadata quality gate not passed.")
        return 0
    rights_path = video.parent / "rights_manifest.json"
    try:
        rights = json.loads(rights_path.read_text(encoding="utf-8"))
    except Exception:
        rights = meta.get("rights_manifest") if isinstance(meta.get("rights_manifest"), dict) else {}
    if not rights or not bool(rights.get("automated_publishable")):
        print(json.dumps({
            "TELEGRAM_PUBLISH_SKIPPED_RIGHTS_GATE": True,
            "reason": "rights_manifest_missing_or_not_cleared",
            "rights_manifest": rights,
        }, ensure_ascii=False))
        return 0
    duration = float(meta.get("duration_seconds") or 0)
    if duration < 18:
        print(f"Telegram publish skipped: video too short ({duration:.2f}s).")
        return 0
    script_quality = str(meta.get("script_quality") or "")
    if script_quality != "passed":
        print("Telegram publish skipped: script quality gate not passed.")
        return 0
    size = video.stat().st_size
    if size > 50 * 1024 * 1024:
        raise RuntimeError(f"Video is {size} bytes; Telegram bot limit is 50 MB.")

    boundary, data = multipart(
        {"chat_id": chat_id, "caption": make_caption(meta)},
        "video", video.name, video.read_bytes(), "video/mp4"
    )
    req = Request(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data=data, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "trend-radar-telegram-publisher/1.0",
        },
    )
    with urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError("Telegram API error: " + json.dumps(result, ensure_ascii=False))

    publish = {
        "platform": "telegram",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "message_id": (result.get("result") or {}).get("message_id"),
        "video_path": str(video),
        "title": meta.get("display_title_fa") or meta.get("title"),
        "source_url": meta.get("source_url"),
    }
    (video.parent / "publish.json").write_text(json.dumps(publish, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(publish, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
