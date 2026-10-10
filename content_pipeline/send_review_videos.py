#!/usr/bin/env python3
"""Download a small, private-review shortlist and send the actual video files to Telegram.

This is a curator-only step. It does not publish to public social platforms, and a
successful download does not grant rights to repost the original creator's work.
"""
import html
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "attention_state" / "social_discovery.json"
STATE = ROOT / "attention_state" / "social_discovery_notification_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_AGE_HOURS = 36.0
MIN_SCORE = 48.0
MAX_VIDEO_BYTES = 48 * 1024 * 1024
MAX_SENDS = 3
MAX_TRIES = 12
SUPPORTED = {"telegram", "youtube", "tiktok", "instagram", "aparat", "x"}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def candidate_id(item):
    return str(item.get("id") or item.get("url") or "").strip()


def select_media_candidates(items, reviewed_ids=None, now=None, limit=MAX_TRIES):
    """Return recent, score-qualified individual clips, favoring platform diversity."""
    now = now or datetime.now(timezone.utc)
    reviewed = {str(x) for x in (reviewed_ids or [])}
    eligible = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ident = candidate_id(item)
        url = str(item.get("url") or "").strip()
        platform = str(item.get("platform") or "").strip().lower()
        if not ident or ident in reviewed or not url.startswith(("https://", "http://")):
            continue
        if platform not in SUPPORTED:
            continue
        try:
            score = float(item.get("follow_growth_score") or 0)
            age = float(item.get("age_hours"))
        except (TypeError, ValueError):
            continue
        if score < MIN_SCORE or age < 0 or age > MAX_AGE_HOURS:
            continue
        if item.get("score_confidence") not in {"medium", "high"}:
            continue
        if any(item.get(k) for k in (
            "news_risk_signal", "sensitive_risk_signal", "safety_review_signal",
            "compilation_signal", "promotional_signal",
        )):
            continue
        item = dict(item)
        item["_review_id"] = ident
        eligible.append(item)

    eligible.sort(key=lambda x: (
        float(x.get("follow_growth_score") or 0),
        -float(x.get("age_hours") or 0),
    ), reverse=True)

    selected, seen_platforms, seen_ids = [], set(), set()
    # First pass: get a mix of sources when there are enough eligible candidates.
    for item in eligible:
        platform = str(item.get("platform") or "unknown").lower()
        if platform in seen_platforms:
            continue
        selected.append(item)
        seen_platforms.add(platform)
        seen_ids.add(item["_review_id"])
        if len(selected) >= limit:
            return selected
    for item in eligible:
        if item["_review_id"] in seen_ids:
            continue
        selected.append(item)
        seen_ids.add(item["_review_id"])
        if len(selected) >= limit:
            break
    return selected


def find_telegram_post_block(page, channel, post_id):
    """Return only the HTML block for the exact Telegram post, never a nearby post."""
    expected = f"{channel}/{post_id}".strip("/")
    blocks = re.split(r'(?=<div\b[^>]*class="[^"]*\btgme_widget_message_wrap\b)', page, flags=re.I)
    for block in blocks:
        marker = re.search(r"""data-post=["']([^"']+)["']""", block[:30000], flags=re.I)
        if marker and marker.group(1).strip("/") == expected:
            return block
    return ""


def extract_telegram_post_video_urls(post_block):
    """Extract video URLs only from a verified single-post HTML block."""
    if not post_block:
        return []
    media_urls = []
    for tag in re.findall(r"<video\b[^>]*>", post_block, flags=re.I | re.S):
        for attr in ("src", "data-src"):
            found = re.search(rf"""{attr}\s*=\s*["']([^"']+)""", tag, flags=re.I)
            if found:
                media_url = html.unescape(found.group(1)).replace("&amp;", "&")
                if media_url.startswith("https://"):
                    media_urls.append(media_url)
    # Some Telegram layouts expose the media URL elsewhere inside the same post.
    # Deliberately never search the surrounding channel page.
    for value in re.findall(
        r"""https://[^\\\"'<> ]+\.(?:mp4|m4v)(?:\?[^\\\"'<> ]*)?""",
        post_block,
        flags=re.I,
    ):
        media_urls.append(html.unescape(value).replace("&amp;", "&"))
    return list(dict.fromkeys(media_urls))


def evaluate_video_metrics(duration, width, height, frame_metrics):
    """Fail closed on broken/black videos; score only observable technical signals."""
    try:
        duration = float(duration)
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return {"passed": False, "score": 0, "reason": "invalid_metadata"}

    if duration < 4 or duration > 120:
        return {"passed": False, "score": 0, "reason": "duration_out_of_range"}
    if width < 320 or height < 240 or width > 7680 or height > 7680:
        return {"passed": False, "score": 0, "reason": "resolution_out_of_range"}
    ratio = width / max(1, height)
    if ratio < 0.25 or ratio > 3.0:
        return {"passed": False, "score": 0, "reason": "aspect_ratio_out_of_range"}
    if len(frame_metrics) < 3:
        return {"passed": False, "score": 0, "reason": "insufficient_decodable_frames"}

    black_count = sum(1 for row in frame_metrics if row.get("mean_luma", 0) < 12 and row.get("std_luma", 0) < 7)
    black_ratio = black_count / len(frame_metrics)
    if black_ratio >= 0.6:
        return {
            "passed": False, "score": 0, "reason": "mostly_black_frames",
            "duration_seconds": round(duration, 2), "width": width, "height": height,
            "sampled_frames": len(frame_metrics), "black_frame_ratio": round(black_ratio, 2),
        }

    differences = []
    for index in range(1, len(frame_metrics)):
        try:
            differences.append(float(frame_metrics[index].get("difference_from_previous", 0)))
        except (TypeError, ValueError):
            pass
    mean_difference = sum(differences) / len(differences) if differences else 0.0

    if width >= 720 and height >= 720:
        resolution_points = 25
    elif width >= 480 and height >= 480:
        resolution_points = 18
    else:
        resolution_points = 10
    diversity_points = min(15, mean_difference / 5)
    score = round(max(0, min(100, 60 + resolution_points + diversity_points - black_ratio * 30)), 1)
    return {
        "passed": True,
        "score": score,
        "reason": "technical_checks_passed",
        "duration_seconds": round(duration, 2),
        "width": width,
        "height": height,
        "sampled_frames": len(frame_metrics),
        "black_frame_ratio": round(black_ratio, 2),
        "mean_frame_difference": round(mean_difference, 2),
        "note": "Technical screen only; does not judge humor, topic relevance, or repost rights.",
    }


def inspect_video_quality(video_path):
    """Use ffprobe/ffmpeg samples to reject technically broken media before Telegram upload."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration:format=duration",
                "-of", "json", str(video_path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        if probe.returncode != 0:
            return {"passed": False, "score": 0, "reason": "ffprobe_failed"}
        payload = json.loads(probe.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return {"passed": False, "score": 0, "reason": "no_video_stream"}
        stream = streams[0]
        duration_raw = (payload.get("format") or {}).get("duration") or stream.get("duration")
        duration = float(duration_raw)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"passed": False, "score": 0, "reason": f"metadata_error:{type(exc).__name__}"}

    frame_metrics = []
    # Uniformly inspect the beginning, middle, and end rather than just the opening.
    with tempfile.TemporaryDirectory(prefix="video-frames-") as frame_dir:
        for index, fraction in enumerate((0.08, 0.28, 0.50, 0.72, 0.92)):
            frame_path = Path(frame_dir) / f"frame-{index}.jpg"
            timestamp = max(0.05, min(duration - 0.05, duration * fraction))
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{timestamp:.3f}", "-i", str(video_path),
                        "-frames:v", "1", "-vf",
                        "scale=96:96:force_original_aspect_ratio=decrease,"
                        "pad=96:96:(ow-iw)/2:(oh-ih)/2",
                        "-update", "1", str(frame_path),
                    ],
                    capture_output=True, text=True, timeout=25,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0 or not frame_path.is_file() or frame_path.stat().st_size == 0:
                continue
            try:
                with Image.open(frame_path) as opened:
                    image = opened.convert("RGB")
                gray_stats = ImageStat.Stat(image.convert("L"))
                row = {"mean_luma": gray_stats.mean[0], "std_luma": gray_stats.stddev[0]}
                if frame_metrics:
                    previous_path = Path(frame_dir) / "previous-frame.jpg"
                    # Keep the previous decoded frame in memory for exact same-size comparison.
                    with Image.open(previous_path) as previous_opened:
                        previous = previous_opened.convert("RGB")
                    row["difference_from_previous"] = sum(ImageStat.Stat(
                        ImageChops.difference(previous, image)
                    ).mean) / 3
                frame_metrics.append(row)
                image.save(Path(frame_dir) / "previous-frame.jpg", format="JPEG")
            except (OSError, ValueError):
                continue

    return evaluate_video_metrics(duration, width, height, frame_metrics)


def download_direct_telegram_post(url, destination):
    """Download video media only when it belongs to the exact Telegram post URL."""
    parsed = urlparse(url)
    match = re.match(r"^/([A-Za-z0-9_]+)/([0-9]+)/?$", parsed.path or "")
    if not match or parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me"}:
        return False
    channel, post_id = match.groups()
    page_url = f"https://t.me/s/{channel}/{post_id}"
    try:
        response = requests.get(page_url, headers={"User-Agent": UA}, timeout=25)
        response.raise_for_status()
        page = response.text[:8_000_000]
    except Exception as exc:
        print("TELEGRAM_SOURCE_PAGE_FAIL", channel, type(exc).__name__)
        return False

    post_block = find_telegram_post_block(page, channel, post_id)
    if not post_block:
        # Fail closed rather than risk sending the media from an adjacent post.
        print("TELEGRAM_TARGET_POST_NOT_FOUND", f"{channel}/{post_id}")
        return False
    media_urls = extract_telegram_post_video_urls(post_block)
    if not media_urls:
        print("TELEGRAM_TARGET_POST_HAS_NO_EXTRACTABLE_VIDEO", f"{channel}/{post_id}")
        return False

    for media_url in media_urls:
        try:
            with requests.get(media_url, headers={"User-Agent": UA}, stream=True, timeout=40) as response:
                response.raise_for_status()
                length = int(response.headers.get("Content-Length") or 0)
                if length and length > MAX_VIDEO_BYTES:
                    print("VIDEO_TOO_LARGE_SKIP", channel, length)
                    continue
                total = 0
                with open(destination, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            output.close()
                            Path(destination).unlink(missing_ok=True)
                            print("VIDEO_TOO_LARGE_SKIP", channel, total)
                            break
                        output.write(chunk)
                if Path(destination).is_file() and Path(destination).stat().st_size > 100_000:
                    return True
        except Exception as exc:
            print("TELEGRAM_MEDIA_URL_FAIL", type(exc).__name__)
            Path(destination).unlink(missing_ok=True)
    return False

def download_with_ytdlp(url, workdir):
    """Use yt-dlp for supported public individual-post URLs; never downloads playlists."""
    output_template = str(Path(workdir) / "%(id)s.%(ext)s")
    command = [
        "yt-dlp", "--no-warnings", "--no-progress", "--no-playlist",
        "--retries", "2", "--fragment-retries", "2", "--socket-timeout", "20",
        "--max-filesize", "48M",
        "-f",
        "bv*[height<=720][ext=mp4][filesize<48000000]+ba[ext=m4a]/"
        "b[height<=720][ext=mp4][filesize<48000000]/b[ext=mp4][filesize<48000000]/"
        "best[filesize<48000000]",
        "--merge-output-format", "mp4", "-o", output_template, url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print("YTDLP_DOWNLOAD_FAIL", url[:120], (result.stderr or result.stdout or "")[-500:].replace("\n", " "))
        return None
    files = [p for p in Path(workdir).glob("*") if p.is_file() and p.suffix.lower() == ".mp4"]
    files = [p for p in files if p.stat().st_size > 100_000 and p.stat().st_size <= MAX_VIDEO_BYTES]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def render_caption(item):
    title = re.sub(r"\s+", " ", str(item.get("title") or "ویدئوی منتخب").strip())[:240]
    url = str(item.get("url") or "")
    platform = str(item.get("platform") or "نامشخص")
    try:
        score = float(item.get("follow_growth_score") or 0)
        age = float(item.get("age_hours") or 0)
    except (TypeError, ValueError):
        score, age = 0, 0
    caption = (
        f"عنوان ثبت‌شده در پست منبع: {title}\n"
        f"منبع: {platform}\n"
        f"لینک اصلی: {url}\n"
        "عنوان، نقل از منبع است؛ توضیح محتواییِ تولیدشده نیست. بازبینی خصوصی."
    )
    return caption[:1024]


def send_video(path, item):
    endpoint = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
    with open(path, "rb") as video:
        response = requests.post(
            endpoint,
            data={
                "chat_id": TARGET,
                "caption": render_caption(item),
                "supports_streaming": "true",
            },
            files={"video": (Path(path).name, video, "video/mp4")},
            timeout=(30, 150),
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendVideo returned ok=false")
    return (payload.get("result") or {}).get("message_id")


def main():
    if not TOKEN or not TARGET:
        print("MEDIA_REVIEW_SEND_SKIPPED: Telegram credentials not configured.")
        return 0

    data = load_json(INPUT, {})
    state = load_json(STATE, {"version": 1, "notified_ids": []})
    reviewed = [str(x) for x in state.get("media_reviewed_ids", [])]
    candidates = select_media_candidates(data.get("items", []), reviewed)
    if not candidates:
        print("MEDIA_REVIEW_EMPTY: no new recent, qualified video candidates.")
        return 0

    sent_ids = []
    with tempfile.TemporaryDirectory(prefix="short-review-") as workdir:
        for item in candidates:
            if len(sent_ids) >= MAX_SENDS:
                break
            url = str(item.get("url") or "")
            video_path = Path(workdir) / "telegram-upload.mp4"
            video_path.unlink(missing_ok=True)
            downloaded = False
            if str(item.get("platform") or "").lower() == "telegram":
                downloaded = download_direct_telegram_post(url, str(video_path))

            # Telegram candidates must pass the exact-post extractor above. Do not
            # fall back to a generic page extractor if that match failed.
            if not downloaded and str(item.get("platform") or "").lower() != "telegram":
                media_path = download_with_ytdlp(url, workdir)
                if media_path:
                    video_path = media_path
                    downloaded = True

            if not downloaded or not video_path.is_file():
                print("MEDIA_REVIEW_DOWNLOAD_SKIPPED", item.get("platform"), url[:140])
                continue
            size = video_path.stat().st_size
            if size > MAX_VIDEO_BYTES:
                print("MEDIA_REVIEW_SIZE_GUARD", size)
                continue

            visual_qc = inspect_video_quality(video_path)
            print(json.dumps({
                "MEDIA_REVIEW_VISUAL_QC": visual_qc,
                "platform": item.get("platform"),
                "title": str(item.get("title") or "")[:150],
                "url": url,
            }, ensure_ascii=False))
            if not visual_qc.get("passed"):
                print("MEDIA_REVIEW_VISUAL_QC_REJECT", visual_qc.get("reason"), url[:140])
                continue

            try:
                message_id = send_video(str(video_path), item)
                ident = item["_review_id"]
                sent_ids.append(ident)
                print(json.dumps({
                    "MEDIA_REVIEW_VIDEO_SENT": True,
                    "message_id": message_id,
                    "platform": item.get("platform"),
                    "title": str(item.get("title") or "")[:150],
                    "url": url,
                    "bytes": size,
                }, ensure_ascii=False))
            except Exception as exc:
                print("MEDIA_REVIEW_TELEGRAM_SEND_FAIL", url[:140], type(exc).__name__, str(exc)[:240])
            finally:
                # Keep the workspace clean before the next source is downloaded.
                if video_path.exists():
                    video_path.unlink(missing_ok=True)

    if sent_ids:
        state.update({
            "version": 1,
            "media_review_updated_at": datetime.now(timezone.utc).isoformat(),
            "media_reviewed_ids": list(dict.fromkeys(reviewed + sent_ids))[-1000:],
            "media_review_last_count": len(sent_ids),
        })
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"MEDIA_REVIEW_TOTAL_SENT": len(sent_ids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
