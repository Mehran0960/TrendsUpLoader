import html
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import requests

ARTICLE = "https://www.asianewsiran.com/fa/newsagency/41817/helen-singer-video"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"

s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6"})

r = s.get(ARTICLE, timeout=30, allow_redirects=True)
r.raise_for_status()
text = r.text

routes = []
seen = set()

def add(u):
    u = html.unescape(str(u or "")).replace("\\/", "/").strip()
    if not u:
        return
    u = urljoin(r.url, u)
    if u not in seen:
        seen.add(u)
        routes.append(u)

for pat in [
    r'<meta[^>]+(?:property|name)=[\"\']og:video(?::url)?[\"\'][^>]+content=[\"\']([^\"\']+)',
    r'<(?:source|video)[^>]+(?:src|data-src|data-video)=["\']([^"\']+)',
]:
    for m in re.finditer(pat, text, re.I | re.S):
        add(m.group(1))

for m in re.finditer(r'https?://[^"\'<>\s]+\.(?:mp4|webm|m3u8)(?:\?[^"\'<>\s]*)?', text, re.I):
    add(m.group(0))

# Some players escape JSON URLs.
for m in re.finditer(r'["\'](https?:\\?/\\?/[^"\']+\.(?:mp4|webm|m3u8)(?:\\?[^"\']*)?)["\']', text, re.I):
    add(m.group(1))

print("ARTICLE_STATUS", r.status_code)
print("VIDEO_ROUTES", len(routes))
for u in routes[:20]:
    print("ROUTE", u)

def direct_download(u):
    fd, p = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    total = 0
    try:
        with s.get(u, stream=True, timeout=45, allow_redirects=True) as rr:
            rr.raise_for_status()
            ctype = (rr.headers.get("content-type") or "").lower()
            if "text/html" in ctype:
                return None
            with open(p, "wb") as f:
                for chunk in rr.iter_content(128 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 52 * 1024 * 1024:
                        return None
                    f.write(chunk)
        if total < 100_000:
            return None
        return p
    except Exception:
        try:
            os.unlink(p)
        except Exception:
            pass
        return None

path_out = None
for u in routes:
    low = u.lower().split("?", 1)[0]
    if low.endswith(".m3u8"):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "video.mp4")
            p = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", u, "-c", "copy", out],
                timeout=90,
            )
            if p.returncode == 0 and Path(out).exists() and Path(out).stat().st_size > 100_000:
                final = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                final.close()
                Path(final.name).write_bytes(Path(out).read_bytes())
                path_out = final.name
                print("M3U8_ACQUIRED", u)
                break
    else:
        path_out = direct_download(u)
        if path_out:
            print("DIRECT_ACQUIRED", u)
            break

if not path_out:
    raise RuntimeError("No usable video route found on article page")

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat = os.environ["TELEGRAM_CHAT_ID"]
caption = (
    "🔥 ویدئوی وایرال ایرانی | هلن\n\n"
    "📊 حدود 242K بازدید • 2.6K لایک • 706 کامنت در منبع کشف\n\n"
    "🎬 فایل ویدئو به‌صورت Native ارسال شد"
)

with open(path_out, "rb") as fh:
    tr = requests.post(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data={"chat_id": chat, "supports_streaming": "true", "caption": caption},
        files={"video": ("viral.mp4", fh, "video/mp4")},
        timeout=120,
    )
tr.raise_for_status()
data = tr.json()
if not data.get("ok"):
    raise RuntimeError(data)
print("TELEGRAM_SENT", data["result"]["message_id"])
print("TELEGRAM_VIDEO_FILE_ID", data["result"]["video"]["file_id"])
