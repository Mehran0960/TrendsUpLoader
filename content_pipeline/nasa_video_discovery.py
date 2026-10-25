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
STATUS_PATH = ROOT / "attention_state" / "nasa_video_discovery_status.json"
NASA_API = "https://images-api.nasa.gov"
NASA_POLICY_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
USER_AGENT = "TrendsUpLoader/1.0 (source metadata and attribution retained)"
MAX_ADDITIONS_PER_RUN = 15
MAX_SEARCH_RESULTS_TO_INSPECT = 60
MAX_QUEUE_ITEMS = 500
TIMEOUT = (10, 30)
ASSET_DIAGNOSTICS = {
    "manifest_mp4_found": 0,
    "fallback_mp4_verified": 0,
    "failures": {},
    "samples": [],
}


def record_asset_failure(nasa_id: str, reason: str, item_count: int = 0, href_samples: list[str] | None = None) -> None:
    failures = ASSET_DIAGNOSTICS["failures"]
    failures[reason] = failures.get(reason, 0) + 1
    if len(ASSET_DIAGNOSTICS["samples"]) < 5:
        ASSET_DIAGNOSTICS["samples"].append({
            "nasa_id": nasa_id,
            "reason": reason,
            "manifest_item_count": item_count,
            "href_samples": (href_samples or [])[:3],
        })


# NASA Image Library center codes; combined with an empty/NASA-only secondary creator,
# this avoids treating any arbitrary third-party center as NASA-owned material.
NASA_CENTER_VALUES = {
    "NASA", "HQ", "NASA HEADQUARTERS", "HEADQUARTERS",
    "JPL", "JET PROPULSION LABORATORY", "NASA JET PROPULSION LABORATORY",
    "GSFC", "GODDARD SPACE FLIGHT CENTER", "NASA GODDARD SPACE FLIGHT CENTER",
    "JSC", "JOHNSON SPACE CENTER", "NASA JOHNSON SPACE CENTER",
    "KSC", "KENNEDY SPACE CENTER", "NASA KENNEDY SPACE CENTER",
    "MSFC", "MARSHALL SPACE FLIGHT CENTER", "NASA MARSHALL SPACE FLIGHT CENTER",
    "GRC", "GLENN RESEARCH CENTER", "NASA GLENN RESEARCH CENTER",
    "ARC", "AMES RESEARCH CENTER", "NASA AMES RESEARCH CENTER",
    "LRC", "LARC", "LANGLEY RESEARCH CENTER", "NASA LANGLEY RESEARCH CENTER",
    "DFRC", "AFRC", "DRYDEN FLIGHT RESEARCH CENTER", "ARMSTRONG FLIGHT RESEARCH CENTER",
    "SSC", "STENNIS SPACE CENTER", "NASA STENNIS SPACE CENTER",
}

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
    """Return true for recognized NASA-center records with no visible rights/person/audio warnings."""
    center = squash(data.get("center"), 120)
    if center.strip().upper() not in NASA_CENTER_VALUES:
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


def search_slot(now: datetime) -> int:
    """Use two distinct source slots per hour so the independent collector doesn't repeat one query."""
    return now.timetuple().tm_yday * 48 + now.hour * 2 + now.minute // 30


def search_items(now: datetime | None = None) -> tuple[str, list[dict]]:
    current = now or datetime.now(timezone.utc)
    slot = search_slot(current)
    index = slot % len(SEARCHES)
    query, _lanes = SEARCHES[index]
    page = 1 + (slot // len(SEARCHES)) % 4
    response = _request_json(
        NASA_API + "/search",
        {"q": query, "media_type": "video", "page_size": 100, "page": page},
    )
    if not response:
        return query, []
    collection = response.get("collection")
    items = collection.get("items", []) if isinstance(collection, dict) else []
    return query, [x for x in items if isinstance(x, dict)]


def _verify_official_mp4_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname != "images-assets.nasa.gov"
        or not parsed.path.lower().endswith(".mp4")
        or parsed.username
        or parsed.password
    ):
        return False
    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
            stream=True,
            timeout=TIMEOUT,
        ) as response:
            final = urlparse(str(response.url or ""))
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if (
                response.status_code not in {200, 206}
                or final.scheme != "https"
                or final.hostname != "images-assets.nasa.gov"
                or not final.path.lower().endswith(".mp4")
                or (content_type and content_type not in {"video/mp4", "application/octet-stream", "binary/octet-stream"})
            ):
                return False
            return True
    except requests.RequestException:
        return False


def mp4_media_url(nasa_id: str) -> str:
    """Get a verified official MP4 from the NASA asset manifest, with a checked fallback."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", str(nasa_id or "")):
        record_asset_failure(str(nasa_id or "")[:100], "invalid_nasa_id")
        return ""

    manifest = _request_json(NASA_API + "/asset/" + nasa_id)
    collection = manifest.get("collection") if isinstance(manifest, dict) else None
    items = collection.get("items", []) if isinstance(collection, dict) else []
    urls = []
    href_samples = []
    for row in items if isinstance(items, list) else []:
        href = str(row.get("href") or "") if isinstance(row, dict) else ""
        parsed = urlparse(href)
        if href and len(href_samples) < 3:
            # Store only scheme, host, path, and suffix; never record query strings.
            href_samples.append(f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:220])
        if (
            parsed.scheme == "https"
            and parsed.hostname == "images-assets.nasa.gov"
            and parsed.path.lower().endswith(".mp4")
            and not parsed.username
            and not parsed.password
        ):
            urls.append(href)
    if urls:
        # Prefer a middle/small derivative to reduce bandwidth; fall back to the
        # official manifest's available MP4s. Verify that the media URL is live.
        preference = ("~medium.mp4", "~small.mp4", "~large.mp4", "~orig.mp4")
        ordered = []
        for suffix in preference:
            ordered.extend(url for url in urls if url.lower().endswith(suffix))
        ordered.extend(url for url in urls if url not in ordered)
        for url in ordered:
            if _verify_official_mp4_url(url):
                ASSET_DIAGNOSTICS["manifest_mp4_found"] += 1
                return url

    # NASA asset manifests normally list direct MP4 URLs. If an item manifest is
    # unavailable/incomplete, try only the documented official host/ID layout,
    # then verify the candidate with an actual ranged request before returning it.
    fallback_candidates = [
        f"https://images-assets.nasa.gov/video/{nasa_id}/{nasa_id}~{size}.mp4"
        for size in ("medium", "small", "orig")
    ]
    for candidate in fallback_candidates:
        if _verify_official_mp4_url(candidate):
            ASSET_DIAGNOSTICS["fallback_mp4_verified"] += 1
            return candidate

    reason = (
        "asset_manifest_unavailable" if manifest is None
        else "asset_manifest_no_mp4_url" if items
        else "asset_manifest_empty"
    )
    record_asset_failure(nasa_id, reason, len(items) if isinstance(items, list) else 0, href_samples)
    return ""


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
    # Reserve a bounded, dedicated NASA pool so social search volume cannot evict
    # every licensed candidate before the five-minute FiLo publisher can consume it.
    nasa_rows = [
        row for row in merged
        if str(row.get("source") or "").lower() == "nasa_public_domain_video"
    ]
    other_rows = [
        row for row in merged
        if str(row.get("source") or "").lower() != "nasa_public_domain_video"
    ]
    sort_key = lambda x: str(x.get("discovered_at") or x.get("observed_at") or x.get("published_at") or "")
    nasa_rows.sort(key=sort_key, reverse=True)
    other_rows.sort(key=sort_key, reverse=True)
    nasa_rows = nasa_rows[:200]
    other_rows = other_rows[:max(0, MAX_QUEUE_ITEMS - len(nasa_rows))]
    payload["items"] = nasa_rows + other_rows
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
    duplicate_records = 0
    invalid_records = 0
    metadata_screen_passes = 0
    metadata_rejections: dict[str, int] = {}
    post_screen_build_failures = 0
    lanes = SEARCHES[search_slot(now) % len(SEARCHES)][1]
    for api_item in api_items[:MAX_SEARCH_RESULTS_TO_INSPECT]:
        if len(additions) >= MAX_ADDITIONS_PER_RUN:
            break
        data_rows = api_item.get("data") or []
        if not data_rows or not isinstance(data_rows[0], dict):
            invalid_records += 1
            continue
        data = data_rows[0]
        nasa_id = squash(data.get("nasa_id"), 100)
        detail_url = "https://images.nasa.gov/details/" + nasa_id if nasa_id else ""
        if not detail_url:
            invalid_records += 1
            continue
        if detail_url in existing_urls:
            duplicate_records += 1
            continue
        allowed, reason = allowed_nasa_metadata(data)
        if not allowed:
            metadata_rejections[reason] = metadata_rejections.get(reason, 0) + 1
            continue
        metadata_screen_passes += 1
        candidate = build_candidate(api_item, query, lanes, timestamp)
        if candidate:
            additions.append(candidate)
            existing_urls.add(candidate["url"])
        else:
            post_screen_build_failures += 1

    merged = merge_candidates(payload, additions, timestamp)
    status = {
        "schema_version": 1,
        "updated_at": timestamp,
        "query": query,
        "api_results": len(api_items),
        "records_inspected": min(len(api_items), MAX_SEARCH_RESULTS_TO_INSPECT),
        "metadata_screen_passes": metadata_screen_passes,
        "metadata_rejections": metadata_rejections,
        "post_screen_build_failures": post_screen_build_failures,
        "duplicate_records": duplicate_records,
        "invalid_records": invalid_records,
        "new_candidates": len(additions),
        "total_queue_items": len(merged.get("items", [])),
        "nasa_queue_items": sum(
            1 for row in merged.get("items", [])
            if isinstance(row, dict) and str(row.get("source") or "").lower() == "nasa_public_domain_video"
        ),
        "asset_lookup_diagnostics": ASSET_DIAGNOSTICS,
        "source_policy_url": NASA_POLICY_URL,
        "screening_note": "Conservative metadata screen; not a guarantee about unlisted embedded rights or platform payout eligibility.",
    }
    merged["nasa_video_discovery"].update(status)
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("NASA_VIDEO_DISCOVERY", json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
