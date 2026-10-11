#!/usr/bin/env python3
"""Queue-aware preparation gate for short-video discovery.

This controls only the private review shortlist. It never marks discovered third-party
media as rights-cleared and never publishes publicly.
"""
import argparse
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from content_pipeline.send_review_videos import (
        INPUT, STATE, MAX_TRIES, infer_recent_topic_history,
        load_json, select_media_candidates,
    )
except ModuleNotFoundError:
    from send_review_videos import (
        INPUT, STATE, MAX_TRIES, infer_recent_topic_history,
        load_json, select_media_candidates,
    )

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "platform_peak_windows.json"
PLANNER_STATE = ROOT / "attention_state" / "peak_window_planner_state.json"
DEFAULT_CONFIG = {
    "schema_version": 1,
    "timezone": "Asia/Tehran",
    "preparation_ahead_minutes": 300,
    "review_queue_target": 6,
    "max_discovery_runs_per_local_day": 6,
    "windows": {},
}


def parse_timestamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def refresh_candidate_ages(items, now):
    """Increase stored ages while discovery is paused; old items must not look fresh."""
    now_utc = now.astimezone(timezone.utc)
    refreshed = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        nested = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        published = (
            item.get("published_at")
            or item.get("telegram_published_at")
            or nested.get("telegram_published_at")
            or nested.get("published_at")
            or item.get("created_at")
        )
        published_at = parse_timestamp(published)
        discovered_at = parse_timestamp(item.get("discovered_at"))
        if published_at:
            age = max(0.0, (now_utc - published_at).total_seconds() / 3600.0)
        else:
            try:
                age = float(item.get("age_hours"))
            except (TypeError, ValueError):
                age = 999999.0
            if discovered_at:
                age += max(0.0, (now_utc - discovered_at).total_seconds() / 3600.0)
            elif not item.get("age_hours"):
                age = 999999.0
        item["age_hours"] = round(age, 3)
        refreshed.append(item)
    return refreshed


def upcoming_windows(now, config, horizon_hours=48):
    """Return every configured platform window in the next 48 hours, soonest first."""
    timezone_name = str(config.get("timezone") or "Asia/Tehran")
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    rows = []
    windows = config.get("windows") if isinstance(config.get("windows"), dict) else {}
    for day_offset in range(0, 3):
        day = (local_now + timedelta(days=day_offset)).date()
        for platform, values in windows.items():
            for value in values if isinstance(values, list) else []:
                try:
                    hour, minute = str(value).split(":", 1)
                    starts = datetime.combine(
                        day,
                        time(hour=int(hour), minute=int(minute)),
                        tzinfo=tz,
                    )
                except (ValueError, TypeError):
                    continue
                delta = (starts - local_now).total_seconds() / 60.0
                if 0 <= delta <= horizon_hours * 60:
                    rows.append({
                        "platform": str(platform),
                        "starts_at": starts.isoformat(),
                        "minutes_until": int(round(delta)),
                    })
    rows.sort(key=lambda row: (row["minutes_until"], row["platform"]))
    return rows


def count_qualified_review_candidates(now=None, data=None, state=None):
    """Count the fresh, unique shortlist that the existing private reviewer can accept."""
    now = now or datetime.now(timezone.utc)
    data = data if isinstance(data, dict) else load_json(INPUT, {})
    state = state if isinstance(state, dict) else load_json(STATE, {})
    items = refresh_candidate_ages(data.get("items", []), now)
    reviewed = [
        str(value)
        for key in ("media_reviewed_ids", "media_reviewed_urls")
        for value in (state.get(key, []) if isinstance(state.get(key, []), list) else [])
    ]
    topics = infer_recent_topic_history(state, items)
    selected = select_media_candidates(
        items,
        reviewed_ids=reviewed,
        now=now,
        limit=MAX_TRIES,
        recent_topics=topics,
    )
    return len(selected)


def build_plan(now, config, queue_count, planner_state, force=False):
    """Plan whether another discovery run is warranted before an upcoming window."""
    timezone_name = str(config.get("timezone") or "Asia/Tehran")
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    local_day = local_now.date().isoformat()
    previous = planner_state if isinstance(planner_state, dict) else {}
    runs_today = 0
    if previous.get("local_day") == local_day:
        try:
            runs_today = max(0, int(previous.get("discovery_runs") or 0))
        except (TypeError, ValueError, OverflowError):
            runs_today = 0

    try:
        lead_minutes = max(0, int(config.get("preparation_ahead_minutes", 300)))
        target = max(1, int(config.get("review_queue_target", 6)))
        daily_cap = max(1, int(config.get("max_discovery_runs_per_local_day", 6)))
    except (TypeError, ValueError, OverflowError):
        lead_minutes, target, daily_cap = 300, 6, 6

    windows = upcoming_windows(now, config)
    next_window = windows[0] if windows else None
    in_prep_window = any(row["minutes_until"] <= lead_minutes for row in windows)
    queue_low = int(queue_count) < target
    under_daily_cap = runs_today < daily_cap

    if not under_daily_cap:
        should_discover, reason = False, "daily_discovery_cap_reached"
    elif force:
        should_discover, reason = True, "manual_dispatch"
    elif not queue_low:
        should_discover, reason = False, "review_queue_has_enough_candidates"
    elif not in_prep_window:
        should_discover, reason = False, "next_platform_window_not_close_enough"
    else:
        should_discover, reason = True, "replenish_review_queue_before_platform_window"

    updated_state = {
        "version": 1,
        "local_day": local_day,
        "discovery_runs": runs_today + (1 if should_discover else 0),
        "last_discovery_attempt_at": local_now.isoformat() if should_discover else previous.get("last_discovery_attempt_at"),
        "last_decision": reason,
        "last_review_queue_count": int(queue_count),
        "review_queue_target": target,
        "max_discovery_runs_per_local_day": daily_cap,
        "next_window": next_window,
    }
    return {
        "should_discover": should_discover,
        "reason": reason,
        "queue_count": int(queue_count),
        "queue_target": target,
        "daily_runs_used_after_decision": updated_state["discovery_runs"],
        "daily_runs_cap": daily_cap,
        "preparation_ahead_minutes": lead_minutes,
        "next_window": next_window,
        "upcoming_windows": windows,
        "state": updated_state,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Run discovery for manual dispatch, subject to the daily cap.")
    parser.add_argument("--now", help="Optional ISO timestamp for deterministic tests.")
    args = parser.parse_args(argv)

    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("Invalid --now timestamp")

    config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not isinstance(config, dict):
        config = DEFAULT_CONFIG
    data = load_json(INPUT, {})
    state = load_json(STATE, {})
    queue_count = count_qualified_review_candidates(now=now, data=data, state=state)
    previous = load_json(PLANNER_STATE, {"version": 1})
    plan = build_plan(now, config, queue_count, previous, force=args.force)

    # Persist only actual discovery decisions; hourly no-op checks must not create commits.
    if plan["should_discover"]:
        PLANNER_STATE.parent.mkdir(parents=True, exist_ok=True)
        PLANNER_STATE.write_text(
            json.dumps(plan["state"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    printable = {key: value for key, value in plan.items() if key not in {"state", "upcoming_windows"}}
    printable["upcoming_windows"] = plan["upcoming_windows"][:12]
    print("PEAK_WINDOW_PREPARATION_PLAN=" + json.dumps(printable, ensure_ascii=False))
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("should_discover=" + ("true" if plan["should_discover"] else "false") + "\n")
            handle.write("queue_count=" + str(plan["queue_count"]) + "\n")
            handle.write("reason=" + plan["reason"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
