#!/usr/bin/env python3
"""Rights-gated YouTube upload of an original generated short; private visibility only."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
STATE = ROOT / "attention_state" / "youtube_upload_state.json"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

def load_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default

def find_latest_video(out_dir=OUT):
    videos = sorted(Path(out_dir).glob("**/video.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not videos:
        raise FileNotFoundError("No generated video.mp4 was found under out/")
    video = videos[0]
    metadata_path = video.parent / "metadata.json"
    rights_path = video.parent / "rights_manifest.json"
    if not metadata_path.is_file() or not rights_path.is_file():
        raise RuntimeError("Metadata and a separate rights_manifest.json are required.")
    return video, load_json(metadata_path, {}), load_json(rights_path, {})

def validate_upload(video, meta, rights):
    """Fail closed unless this is a cleared, curated, quality-gated original short."""
    errors = []
    if not Path(video).is_file() or Path(video).stat().st_size <= 0:
        errors.append("video_missing_or_empty")
    if str(meta.get("quality_gate") or "") != "passed_publish":
        errors.append("quality_gate_not_passed")
    if str(meta.get("script_quality") or "") != "passed":
        errors.append("script_quality_not_passed")
    if str(meta.get("script_mode") or "") != "curated":
        errors.append("script_not_curated_original")
    if float(meta.get("duration_seconds") or 0) < 16:
        errors.append("video_duration_below_16_seconds")
    if not bool(rights.get("assets_cleared")) or not bool(rights.get("automated_publishable")):
        errors.append("rights_manifest_not_cleared")
    if not str(meta.get("display_title_fa") or meta.get("title") or "").strip():
        errors.append("video_title_missing")
    return errors

def build_description(meta, rights_path):
    lines = []
    title = str(meta.get("display_title_fa") or meta.get("title") or "").strip()
    if title:
        lines.append(title)
    segments = meta.get("segments") or []
    script = " ".join(str(s.get("text") or "").strip() for s in segments if str(s.get("text") or "").strip())
    if script and script != title:
        lines.extend(["", script])
    source_url = str(meta.get("source_url") or "").strip()
    if source_url:
        lines.extend(["", "منبع زمینه/اطلاعات: " + source_url])
    attribution = Path(rights_path).parent / "attribution.txt"
    if attribution.is_file():
        text = attribution.read_text(encoding="utf-8").strip()
        if text:
            lines.extend(["", text])
    lines.extend(["", "این ویدئو محتوای اصیل تولیدشده برای این کانال است."] )
    return "\n".join(lines)[:4900]

def main():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    if not (client_id and client_secret and refresh_token):
        print("YOUTUBE_UPLOAD_SKIPPED: configure YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REFRESH_TOKEN in repository Actions secrets.")
        return 0

    video, meta, rights = find_latest_video()
    errors = validate_upload(video, meta, rights)
    if errors:
        print(json.dumps({"YOUTUBE_UPLOAD_BLOCKED_BY_GATES": errors}, ensure_ascii=False))
        return 2

    content_key = str(meta.get("content_key") or "").strip()
    state = load_json(STATE, {"version": 1, "uploads": []})
    uploads = state.get("uploads") if isinstance(state.get("uploads"), list) else []
    if content_key and any(str(x.get("content_key") or "") == content_key for x in uploads if isinstance(x, dict)):
        print("YOUTUBE_UPLOAD_SKIPPED_DUPLICATE_CONTENT_KEY")
        return 0

    # The initial integration deliberately permits private uploads only.
    privacy = "private"
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[UPLOAD_SCOPE],
    )
    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    title = str(meta.get("display_title_fa") or meta.get("title") or "ویدئوی جدید").strip()[:100]
    body = {
        "snippet": {
            "title": title,
            "description": build_description(meta, video.parent / "rights_manifest.json"),
            "categoryId": "22",
            "defaultLanguage": "fa",
            "defaultAudioLanguage": "fa",
            "tags": ["ویدئوی کوتاه", "هوش مصنوعی", "فارسی", "Shorts"],
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True),
    )
    response = request.execute(num_retries=3)
    video_id = str(response.get("id") or "").strip()
    if not video_id:
        raise RuntimeError("YouTube upload returned no video ID.")

    published = {
        "platform": "youtube",
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": privacy,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "content_key": content_key,
        "title": title,
        "video_path": str(video),
        "rights_status": rights.get("media_status"),
        "script_mode": meta.get("script_mode"),
    }
    (video.parent / "youtube_upload.json").write_text(json.dumps(published, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    uploads.append(published)
    state.update({"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "uploads": uploads[-500:]})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"YOUTUBE_PRIVATE_UPLOAD_OK": True, "video_id": video_id, "url": published["url"], "privacy_status": privacy}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
