#!/usr/bin/env python3
"""Build a platform-neutral distribution plan for one generated video.

This manifest is a readiness checklist, not permission to reuse third-party media
and not a claim that any destination will accept or recommend the content.
Every destination still needs its own up-to-date policy and account-access check.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

# These statuses describe this repository's integration state, not a platform's
# general capabilities or current policy.
TARGETS = {
    "telegram": {
        "adapter": "official_bot_api",
        "integration_status": "implemented_but_opt_in",
        "required_configuration": [
            "TELEGRAM_AUDIENCE_BOT_TOKEN secret",
            "TELEGRAM_AUDIENCE_CHAT_ID variable",
            "TELEGRAM_AUDIENCE_PUBLISH_ENABLED variable = true",
            "workflow_dispatch publish_to_telegram = true",
        ],
    },
    "youtube": {
        "adapter": "official_data_api",
        "integration_status": "not_connected",
        "required_configuration": ["authorized channel route verified separately"],
    },
    "tiktok": {
        "adapter": "official_content_posting_api",
        "integration_status": "not_connected",
        "required_configuration": ["approved app/scope and creator authorization if available"],
    },
    "instagram": {
        "adapter": "official_content_publishing_api",
        "integration_status": "not_connected",
        "required_configuration": ["eligible professional account and approved permissions if available"],
    },
    "aparat": {
        "adapter": "official_route_to_be_verified",
        "integration_status": "not_connected",
        "required_configuration": ["verify current supported upload route"],
    },
    "x": {
        "adapter": "official_api_or_manual_export",
        "integration_status": "not_connected",
        "required_configuration": ["verify current authorized route"],
    },
}

def create_distribution_plan(metadata: dict, rights: dict) -> dict:
    """Return destination-specific readiness without ever authorizing a publish."""
    metadata = metadata if isinstance(metadata, dict) else {}
    rights = rights if isinstance(rights, dict) else {}
    quality_ok = (
        metadata.get("quality_gate") == "passed_publish"
        and metadata.get("script_quality") == "passed"
        and float(metadata.get("duration_seconds") or 0) >= 16
    )
    rights_ok = (
        rights.get("automated_publishable") is True
        and rights.get("assets_cleared") is True
        and rights.get("script_mode") == "curated"
    )

    results = {}
    for platform, target in TARGETS.items():
        if not quality_ok:
            status = "blocked_editorial_quality"
        elif not rights_ok:
            status = "blocked_rights_or_provenance"
        elif platform == "telegram":
            status = "opt_in_route_available_not_enabled_by_manifest"
        else:
            status = "adapter_not_connected"
        results[platform] = {
            **target,
            "status": status,
            "must_recheck_destination_policy": True,
            "publishing_authorized": False,
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_goal": "audience_acquisition_and_indirect_monetization",
        "direct_platform_ad_revenue_is_a_goal": False,
        "content_key": str(metadata.get("content_key") or ""),
        "content_title": str(metadata.get("display_title_fa") or metadata.get("title") or ""),
        "editorial_quality_passed": quality_ok,
        "rights_gate_passed": rights_ok,
        "important_note": (
            "This is a routing checklist only. It does not grant rights, infer platform policy, "
            "or publish anything. Recheck the current rules and permissions separately for every destination."
        ),
        "targets": results,
    }


def write_latest_plan(output_root="out") -> Path:
    root = Path(output_root)
    metadata_files = sorted(root.glob("**/metadata.json"))
    if not metadata_files:
        raise FileNotFoundError(f"No metadata.json found under {root}")
    metadata_path = metadata_files[-1]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    rights_path = metadata_path.parent / "rights_manifest.json"
    rights = json.loads(rights_path.read_text(encoding="utf-8")) if rights_path.exists() else {}
    plan = create_distribution_plan(metadata, rights)
    destination = metadata_path.parent / "distribution_manifest.json"
    destination.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "DISTRIBUTION_PLAN_CREATED": str(destination),
        "rights_gate_passed": plan["rights_gate_passed"],
        "editorial_quality_passed": plan["editorial_quality_passed"],
        "target_statuses": {key: value["status"] for key, value in plan["targets"].items()},
    }, ensure_ascii=False))
    return destination


if __name__ == "__main__":
    write_latest_plan()
