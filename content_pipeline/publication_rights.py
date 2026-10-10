"""Conservative asset-rights manifest for generated videos.

This module records evidence for the media assets used in a video. It is not
legal advice and does not establish fair use or replace checking the source
license terms.
"""
import re
from typing import Any


CC0_IDS = {"cc0", "cczero", "publicdomainmark", "pdm", "pd", "publicdomain"}
CC_BY_IDS = {"by", "ccby"}
CC_BY_SA_IDS = {"bysa", "ccbysa"}


def normalize_license(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    if not compact:
        return "unknown"
    # Non-commercial and no-derivatives terms need separate legal review.
    if "nc" in compact or "nd" in compact:
        return "restricted_or_unclear"
    if compact in CC0_IDS or compact.startswith("cc0") or "publicdomain" in compact:
        return "CC0_or_public_domain"
    if compact in CC_BY_SA_IDS or "ccbysa" in compact or compact.startswith("bysa"):
        return "CC_BY_SA"
    if compact in CC_BY_IDS or compact.startswith("ccby") or re.fullmatch(r"by[0-9].*", compact):
        return "CC_BY"
    return "unknown"


def _has_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in {"unknown", "unspecified", "author not stated", "n/a", "none"})


def assess_asset(asset: dict, kind: str) -> dict:
    """Return a conservative asset-level rights status and attribution record."""
    asset = asset if isinstance(asset, dict) else {}
    license_raw = str(asset.get("license") or "")
    license_kind = normalize_license(license_raw)
    source_url = str(asset.get("page_url") or asset.get("source_url") or asset.get("page") or "").strip()
    license_url = str(asset.get("license_url") or "").strip()
    creator = str(asset.get("artist") or asset.get("author") or "").strip()
    title = str(asset.get("title") or asset.get("name") or kind).strip()

    if not source_url:
        status = "source_link_missing"
    elif license_kind in {"CC0_or_public_domain"}:
        status = "cleared_with_source_record"
    elif license_kind in {"CC_BY", "CC_BY_SA"}:
        if _has_text(creator) and license_url:
            status = "cleared_with_attribution"
        else:
            status = "attribution_details_incomplete"
    elif license_kind == "restricted_or_unclear":
        status = "license_requires_review"
    else:
        status = "license_unverified"

    return {
        "kind": kind,
        "title": title,
        "creator": creator,
        "source_url": source_url,
        "license_raw": license_raw,
        "license_class": license_kind,
        "license_url": license_url,
        "status": status,
        "cleared": status in {"cleared_with_source_record", "cleared_with_attribution"},
        "attribution_required": license_kind in {"CC_BY", "CC_BY_SA"},
    }


def build_rights_manifest(metadata: dict) -> dict:
    """Summarize media-license evidence separately from script originality."""
    metadata = metadata if isinstance(metadata, dict) else {}
    assets = []
    for item in metadata.get("video_assets") or []:
        assets.append(assess_asset(item, "video"))
    for item in metadata.get("visual_assets") or []:
        assets.append(assess_asset(item, "image"))

    # Locally generated illustrations have no third-party asset license to clear.
    if not assets:
        media_status = "original_generated_visuals"
        assets_cleared = True
    else:
        assets_cleared = all(a["cleared"] for a in assets)
        media_status = "all_assets_cleared" if assets_cleared else "review_required"

    script_mode = str(metadata.get("script_mode") or "").strip().lower()
    if script_mode == "curated":
        script_status = "original_curated_script"
    elif script_mode == "ai":
        script_status = "ai_generated_script_requires_editorial_review"
    elif script_mode == "argos_template":
        script_status = "translated_source_excerpt_requires_rewrite"
    else:
        script_status = "script_origin_unknown"

    # Being media-license-cleared alone is not sufficient: avoid auto-publication
    # of translated source excerpts or unreviewed generated scripts.
    automated_publishable = assets_cleared and script_mode == "curated"
    reasons = []
    if not assets_cleared:
        reasons.append("verify_media_license_and_attribution")
    if script_mode == "argos_template":
        reasons.append("rewrite_source-derived_script_in_original_words")
    elif script_mode == "ai":
        reasons.append("editorial_review_for_accuracy_and_originality")
    elif script_mode != "curated":
        reasons.append("verify_script_origin")
    if assets_cleared and script_mode == "curated":
        reasons.append("preserve_attribution_records_and_check_platform_rules")

    return {
        "schema_version": 1,
        "media_status": media_status,
        "assets_cleared": assets_cleared,
        "assets": assets,
        "script_mode": script_mode or "unknown",
        "script_status": script_status,
        "automated_publishable": automated_publishable,
        "recommended_action": reasons,
        "notice": "This manifest records source/license evidence only; it is not a legal determination.",
    }
