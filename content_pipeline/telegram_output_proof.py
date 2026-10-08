import os
import subprocess
from pathlib import Path
import requests

URL = "https://www.youtube.com/watch?v=bVZqPgWWx_4"
OUT = Path("/tmp/viral-proof.mp4")

subprocess.run([
    "yt-dlp", "--no-playlist", "--no-warnings",
    "--js-runtimes", "deno",
    "--max-filesize", "50M",
    "-f", "best[ext=mp4][height<=720]/best[ext=mp4]/best",
    "-o", str(OUT),
    URL,
], check=True, timeout=120)

if not OUT.exists() or OUT.stat().st_size < 100_000:
    raise RuntimeError("MP4 was not acquired")

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]
with OUT.open("rb") as fh:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data={
            "chat_id": chat_id,
            "supports_streaming": "true",
            "caption": "🔥 ویدئوی وایرال ایرانی | هلن خواننده ایرانی\n\n📊 242K بازدید • 2.6K لایک • 706 کامنت\n\n🎬 ارسال مستقیم به‌صورت ویدئو",
        },
        files={"video": ("viral.mp4", fh, "video/mp4")},
        timeout=120,
    )
response.raise_for_status()
data = response.json()
if not data.get("ok"):
    raise RuntimeError(data)
result = data["result"]
print("TELEGRAM_SENT", result["message_id"])
print("TELEGRAM_VIDEO_FILE_ID", result["video"]["file_id"])
