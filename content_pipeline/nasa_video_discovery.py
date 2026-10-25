#!/usr/bin/env python3
"""Discover a small queue of NASA-origin videos whose metadata passes a conservative reuse screen.

NASA material is generally reusable when not marked as third-party copyrighted.
This collector deliberately skips records with partner/secondary creators, identifiable-person
signals, music/interview signals, or copyright/permission warnings. It stores the NASA media-use
guidelines as evidence; review account/platform rules and each source item before public use.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "attention_state" / "social_discovery.json"
NASA_API = "https://images-api.nasa.gov"
NASA_POLICY_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
USER_AGENT = "TrendsUpLoader/1.0 (source metadata and attribution retained)"
MAX_ADDITIONS_PER_RUN = 15
MAX_SEARCH_RESULTS_TO_INSPECT = 60
MAX_QUEUE_ITEMS = 500
TIMEOUT = (10, 30)

SEARCHES = [
    ("Earth from space", ["science_curiosity", "nature_and_wildlife"]),
    ("aurora", ["science_curiosity", "nature_and_wildlife"]),
    ("Mars rover", ["science_curiosity", "technology_demo"]),
    ("solar flare", ["science_curiosity", "technology_demo"]),
    ("rocket launch", ["science_curiosity", "technology_demo"]),
    ("space telescope", ["science_curiosity", "technology_demo"]),
    ("International Space Station Earth", ["science_curiosity", "nature_and_wildlife"]),
    ("planetary science", ["science_curiosity", "technology_demo"]),
]

RIGHTS_WARNING_RE = re.compile(
    r"copyright(?:ed)?|all rights reserved|third.party|permission required|"
    r"not for commercial|courtesy of|licensed from|©|\bgetty\b|\breuters\b|"
    r"associated press|\bap video\b|\bstock footage\b|\bcommercial use prohibited\b",
    re.I,
)
PERSON_OR_AUDIO_RE = re.compile(
    r"interview|voice.?over|narrat(?:ed|ion|or)|music|soundtrack|singing|"
    r"astronaut|crew member|crew interview|person speaking|people speaking|"
    r"students? (?:at|from)|children|celebrity|actor|actress|\bportrait\b",
    re.I,
)
PARTNER_RE = re.compile(
    r"\b(?:ESA|JAXA|NOAA|USGS|U\.S\. Navy|SpaceX|Boeing|Lockheed Martin|"
    r"Getty|Reuters|Associated Press|AP Video)\b",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_payload() -> dict:
    try:
        payload = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload
    except Exception:
        pass
    return {"version": 2, "ranking_version": "nasa_licensed_video_v1", "items": []}


def squash(value: object, limit: int = 2200) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def allowed_nasa_metadata(data: dict) -> tuple[bool, str]:
    """Return true only for NASA-center records with no visible rights/person/audio warnings."""
    center = squash(data.get("center"), 120)
    if center.strip().casefold() != "nasa":
        return False, "non_nasa_center"

    secondary = squash(data.get("secondary_creator"), 500)
    if secondary and secondary.casefold() not in {"nasa", "national aeronautics and space administration"}:
        return False, "secondary_creator_present"

    title = squash(data.get("title"), 300)
    description = squash(data.get("description"), 2400)
    keywords = data.get("keywords") or []
    if isinstance(keywords, list):
        keyword_text = " ".join(squash(x, 80) for x in keywords)
    else:
        keyword_text = squash(keywords, 500)
    source_text = " ".join((title, description, keyword_text, secondary))

    if RIGHTS_WARNING_RE.search(source_text):
        return False, "rights_warning_in_metadata"
    if PERSON_OR_AUDIO_RE.search(source_text):
        return False, "person_or_audio_signal"
    if PARTNER_RE.search(source_text):
        return False, "third_party_partner_signal"
    if not title or not description:
        return False, "missing_metadata"
    return True, "metadata_passed_conservative_nasa_screen"


def _request_json(url: str, params: dict | None = None) -> dict | None:
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None
    except (requests.RequestException, ValueError, TypeError) as exc:
        print("NASA_SOURCE_REQUEST_FAILED", json.dumps({
            "endpoint": urlparse(url).path,
            "error_type": type(exc).__name__,
        }))
        return None


def search_items(now: datetime | None = None) -> tuple[str, list[dict]]:
    current = now or datetime.now(timezone.utc)
    index = (current.timetuple().tm_yday + current.hour) % len(SEARCHES)
    query, _lanes = SEARCHES[index]
    page = 1 + ((current.timetuple().tm_yday * 24 + current.hour) // len(SEARCHES)) % 4
    response = _request_json(
        NASA_API + "/search",
        {"q": query, "media_type": "video", "page_size": 100, "page": page},
    )
    if not response:
        return query, []
    collection = response.get("collection")
    items = collection.get("items", []) if isinstance(collection, dict) else []
    return query, [x for x in items if isinstance(x, dict)]


def mp4_media_url(nasa_id: str) -> str:
    manifest = _request_json(NASA_API + "/asset/" + nasa_id)
    collection = manifest.get("collection") if isinstance(manifest, dict) else None
    items = collection.get("items", []) if isinstance(collection, dict) else []
    urls = []
    for row in items:
        href = str(row.get("href") or "") if isinstance(row, dict) else ""
        parsed = urlparse(href)
        if (
            parsed.scheme == "https"
            and parsed.hostname == "images-assets.nasa.gov"
            and parsed.path.lower().endswith(".mp4")
            and not parsed.username
            and not parsed.password
        ):
            urls.append(href)
    if not urls:
        return ""
    # Prefer a middle/small derivative to reduce runner bandwidth; retain the original
    # only when the official manifest provides no smaller MP4 variant.
    preference = ("~medium.mp4", "~small.mp4", "~large.mp4", "~orig.mp4")
    for suffix in preference:
        for url in urls:
            if url.lower().endswith(suffix):
                return url
    return urls[0]


def build_candidate(api_item: dict, query: str, lanes: list[str], discovered_at: str) -> dict | None:
    data_rows = api_item.get("data") or []
    if not isinstance(data_rows, list) or not data_rows or not isinstance(data_rows[0], dict):
        return None
    data = data_rows[0]
    nasa_id = squash(data.get("nasa_id"), 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", nasa_id):
        return None
    if str(data.get("media_type") or "").lower() != "video":
        return None
    accepted, reason = allowed_nasa_metadata(data)
    if not accepted:
        return None

    media_url = mp4_media_url(nasa_id)
    if not media_url:
        return None

    title = squash(data.get("title"), 300)
    description = squash(data.get("description"), 1800)
    detail_url = "https://images.nasa.gov/details/" + nasa_id
    item_id = hashlib.sha256(detail_url.encode("utf-8")).hexdigest()
    return {
        "id": item_id,
        "url": detail_url,
        "platform": "nasa",
        "source": "nasa_public_domain_video",
        "title": title,
        "description": description,
        "source_post_caption": title,
        "published_at": str(data.get("date_created") or ""),
        "discovered_at": discovered_at,
        "age_hours": 0.0,
        "views": 0,
        "follow_growth_score": 62.0,
        "momentum_priority_score": 1.0,
        "score_confidence": "medium",
        "score_reason": "rights-screened NASA source; popularity metrics not available",
        "hook_signals": list(lanes),
        "language": "en",
        "query": query,
        "nasa_id": nasa_id,
        "direct_media_url": media_url,
        "rights_cleared_for_publication": True,
        "rights_evidence_url": NASA_POLICY_URL,
        "license_url": NASA_POLICY_URL,
        "license_name": "NASA media-use guidelines; item screened for NASA center and visible third-party/copyright signals",
        "rights_screen": {
            "basis": reason,
            "center": squash(data.get("center"), 100),
            "secondary_creator": squash(data.get("secondary_creator"), 200),
            "third_party_warning_found": False,
            "person_or_audio_signal_found": False,
        },
        "attribution_text": "Source: NASA — " + title + " (" + detail_url + ")",
        "views_velocity_kind": "unavailable",
    }


def merge_candidates(payload: dict, additions: list[dict], timestamp: str) -> dict:
    old_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    by_url = {}
    for row in old_items:
        if isinstance(row, dict) and str(row.get("url") or "").startswith(("https://", "http://")):
            by_url[str(row["url"])] = row
    added = 0
    for row in additions:
        url = str(row.get("url") or "")
        if url and url not in by_url:
            by_url[url] = row
            added += 1
    merged = list(by_url.values())
    # Preserve NASA candidates until their separate upload ledger marks an attempt.
    # Keep newest discovery records first without dropping existing discovery metadata.
    merged.sort(
        key=lambda x: str(x.get("discovered_at") or x.get("observed_at") or x.get("published_at") or ""),
        reverse=True,
    )
    payload["items"] = merged[:MAX_QUEUE_ITEMS]
    payload["count"] = len(payload["items"])
    payload["updated_at"] = timestamp
    payload.setdefault("version", 2)
    payload["nasa_video_discovery"] = {
        "updated_at": timestamp,
        "new_candidates": added,
        "rights_screen": "NASA center only; visible copyright/partner/person/audio signals excluded",
    }
    return payload


def main() -> int:
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    query, api_items = search_items(now)
    payload = load_payload()
    existing_urls = {
        str(row.get("url") or "")
        for row in payload.get("items", [])
        if isinstance(row, dict)
    }
    additions = []
    for api_item in api_items[:MAX_SEARCH_RESULTS_TO_INSPECT]:
        if len(additions) >= MAX_ADDITIONS_PER_RUN:
            break
        data_rows = api_item.get("data") or []
        nasa_id = squash(data_rows[0].get("nasa_id"), 100) if data_rows and isinstance(data_rows[0], dict) else ""
        detail_url = "https://images.nasa.gov/details/" + nasa_id if nasa_id else ""
        if not detail_url or detail_url in existing_urls:
            continue
        candidate = build_candidate(api_item, query, SEARCHES[(now.timetuple().tm_yday + now.hour) % len(SEARCHES)][1], timestamp)
        if candidate:
            additions.append(candidate)
            existing_urls.add(candidate["url"])

    merged = merge_candidates(payload, additions, timestamp)
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("NASA_VIDEO_DISCOVERY", json.dumps({
        "query": query,
        "api_results": len(api_items),
        "new_candidates": len(additions),
        "total_queue": len(merged.get("items", [])),
        "license_basis": "NASA published media-use guidelines; conservative metadata screening",
        "source_policy_url": NASA_POLICY_URL,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
