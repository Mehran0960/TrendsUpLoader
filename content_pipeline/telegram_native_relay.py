#!/usr/bin/env python3
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "attention_state" / "telegram_relay_state.json"
POLICY = ROOT / "config" / "telegram_source_policy.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
AUDIENCE_ENABLED = os.environ.get("TELEGRAM_AUDIENCE_PUBLISH_ENABLED", "").strip().lower() == "true"
AUDIENCE_TOKEN = os.environ.get("TELEGRAM_AUDIENCE_BOT_TOKEN", "").strip()
AUDIENCE_TARGET = os.environ.get("TELEGRAM_AUDIENCE_CHAT_ID", "").strip()
MAX_PENDING = 200
MAX_SOURCE_AGE_HOURS = 24.0

CUES = re.compile(
    r"\b(caught|catch|eating|eats|fails|failed|fall|falls|fell|"
    r"surprise|surprising|unexpected|impossible|amazing|crazy|"
    r"reaction|reacts|wins|won|custom|artificial|transformation|"
    r"before|after|rescue|rescued|fight|fighting|chase|chasing|"
    r"jumpscare|save|saved|wow)\b",
    re.I,
)

BLOCK = re.compile(r"\b(gore|explicit|nsfw|porn|self-harm)\b", re.I)

def api(method, payload=None, token=None):
    active_token = str(token or TOKEN).strip()
    if not active_token:
        raise RuntimeError("Telegram bot token missing for requested destination.")
    url = f"https://api.telegram.org/bot{active_token}/{method}"
    r = requests.post(url, json=payload or {}, timeout=35)
    data = r.json()
    if not data.get("ok"):
        # Do not log URL/token values or raw API responses that may contain sensitive data.
        raise RuntimeError(f"Telegram {method} failed (HTTP {r.status_code}).")
    return data.get("result")

def load_state():
    try:
        x = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(x, dict):
            return x
    except Exception:
        pass
    return {"version": 1, "offset": 0, "seen": []}

def save_state(s):
    STATE.parent.mkdir(exist_ok=True)
    s["seen"] = list(dict.fromkeys([str(x) for x in s.get("seen", [])]))[-500:]
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def load_policy():
    try:
        value = json.loads(POLICY.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _listed_source(chat, usernames, chat_ids):
    username = str((chat or {}).get("username") or "").lstrip("@").lower()
    cid = str((chat or {}).get("id") or "")
    return bool((username and username in usernames) or (cid and cid in chat_ids))


def allowed_source(chat):
    policy = load_policy()
    if bool(policy.get("allow_all_channels_bot_can_receive")):
        return True
    usernames = {str(x).lstrip("@").lower() for x in policy.get("allowed_usernames", []) if str(x).strip()}
    chat_ids = {str(x) for x in policy.get("allowed_chat_ids", []) if str(x).strip()}
    return _listed_source(chat, usernames, chat_ids)


def audience_republication_allowed(chat, policy=None):
    """Only source channels explicitly authorized for public republication can reach an audience target."""
    policy = policy if isinstance(policy, dict) else load_policy()
    usernames = {
        str(x).lstrip("@").lower()
        for x in policy.get("authorized_public_repost_usernames", [])
        if str(x).strip()
    }
    chat_ids = {
        str(x)
        for x in policy.get("authorized_public_repost_chat_ids", [])
        if str(x).strip()
    }
    return _listed_source(chat, usernames, chat_ids)


def candidate(update):
    msg = update.get("channel_post") or update.get("message") or {}
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id or chat_id == str(TARGET) or (AUDIENCE_TARGET and chat_id == str(AUDIENCE_TARGET)):
        return None
    if not allowed_source(chat):
        return None

    media = msg.get("video") or msg.get("animation") or msg.get("document")
    if msg.get("document") and not str(media.get("mime_type") or "").startswith("video/"):
        media = None
    if not media:
        return None

    if bool(msg.get("has_protected_content")):
        return None

    caption = str(msg.get("caption") or "")
    if BLOCK.search(caption):
        return None

    views = int(msg.get("views") or 0)
    reactions = msg.get("reactions") or {}
    reaction_count = 0
    for rr in reactions.get("results") or []:
        reaction_count += int(rr.get("count") or 0)

    age_hours = 999.0
    if msg.get("date"):
        try:
            age_hours = max(
                0.0,
                (datetime.now(timezone.utc) -
                 datetime.fromtimestamp(int(msg["date"]), tz=timezone.utc)
                ).total_seconds() / 3600.0,
            )
        except Exception:
            pass

    age_h = max(0.25, age_hours)
    velocity = views / age_h
    reaction_rate = reaction_count / max(1, views)

    # Demand-first ranking. Topic, duration and "funny" cues never affect rank.
    # Reactions are supporting evidence because Telegram does not expose share count.
    import math
    view_score = min(50.0, 7.4 * math.log10(views + 1))
    velocity_score = min(30.0, 6.4 * math.log10(velocity + 1))
    engagement_score = min(12.0, 350.0 * reaction_rate)
    freshness_score = 8.0 * max(0.0, 1.0 - min(age_h / 24.0, 1.0))
    score = min(100.0, view_score + velocity_score + engagement_score + freshness_score)

    source_name = str(chat.get("title") or chat.get("username") or chat_id)
    return {
        "score": round(score, 3),
        "chat_id": chat_id,
        "message_id": int(msg.get("message_id") or 0),
        "caption": caption,
        "views": views,
        "reactions": reaction_count,
        "source_name": source_name,
        "source_username": str(chat.get("username") or ""),
        "video_duration": int(media.get("duration") or 0),
        "age_hours": round(age_hours, 3),
        "update_id": int(update.get("update_id") or 0),
        "media_type": "video" if msg.get("video") else "animation",
    }

def make_caption(x):
    caption = x["caption"].strip()
    if len(caption) > 700:
        caption = caption[:697] + "..."
    if caption:
        return "🔥 وایرال تازه\n\n" + caption
    return "🔥 یک ویدئوی تازه و داغ"

def main():
    if not TOKEN or not TARGET:
        raise RuntimeError("Private Telegram review credentials are not configured.")
    if AUDIENCE_ENABLED and (not AUDIENCE_TOKEN or not AUDIENCE_TARGET):
        raise RuntimeError(
            "Public Telegram relay is enabled but the separate audience bot token/target is missing."
        )

    state = load_state()
    offset = int(state.get("offset") or 0)
    seen = set(str(x) for x in state.get("seen", []))
    now = datetime.now(timezone.utc)

    updates = api(
        "getUpdates",
        {
            "offset": offset,
            "limit": 100,
            "timeout": 0,
            "allowed_updates": ["channel_post", "message"],
        },
    ) or []

    max_update = offset
    pending_by_key = {}
    for row in state.get("pending", []) if isinstance(state.get("pending"), list) else []:
        if isinstance(row, dict):
            key = str(row.get("key") or "")
            if key and key not in seen:
                pending_by_key[key] = row

    for update in updates:
        uid = int(update.get("update_id") or 0)
        max_update = max(max_update, uid + 1)
        item = candidate(update)
        if not item:
            continue
        key = f'{item["chat_id"]}:{item["message_id"]}'
        if key in seen or key in pending_by_key:
            continue
        if float(item.get("age_hours") or 999) > MAX_SOURCE_AGE_HOURS:
            continue
        if not (
            int(item.get("views") or 0) >= 1000
            or int(item.get("reactions") or 0) >= 25
            or float(item.get("score") or 0) >= 72.0
        ):
            continue
        item["key"] = key
        item["queued_at"] = now.isoformat()
        pending_by_key[key] = item

    pending = list(pending_by_key.values())
    # Expire old queued items and keep a bounded queue to avoid replaying stale clips.
    fresh_pending = []
    for item in pending:
        try:
            queued_at = datetime.fromisoformat(str(item.get("queued_at") or "").replace("Z", "+00:00"))
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=timezone.utc)
            age_now = float(item.get("age_hours") or 0) + max(
                0.0, (now - queued_at.astimezone(timezone.utc)).total_seconds() / 3600.0
            )
        except Exception:
            age_now = 999.0
        if age_now <= MAX_SOURCE_AGE_HOURS:
            item["current_age_hours"] = round(age_now, 2)
            fresh_pending.append(item)

    fresh_pending.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    fresh_pending = fresh_pending[:MAX_PENDING]

    if not fresh_pending:
        state["offset"] = max_update
        state["pending"] = []
        save_state(state)
        print("No eligible source-channel video.")
        return

    chosen = fresh_pending.pop(0)
    policy = load_policy()
    public_route = bool(
        AUDIENCE_ENABLED
        and audience_republication_allowed(
            {"id": chosen.get("chat_id"), "username": chosen.get("source_username")},
            policy,
        )
    )
    destination_token = AUDIENCE_TOKEN if public_route else TOKEN
    destination_chat = AUDIENCE_TARGET if public_route else TARGET

    result = api(
        "copyMessage",
        {
            "chat_id": destination_chat,
            "from_chat_id": int(chosen["chat_id"]) if str(chosen["chat_id"]).lstrip("-").isdigit() else chosen["chat_id"],
            "message_id": chosen["message_id"],
            "caption": make_caption(chosen),
            "disable_notification": False,
        },
        token=destination_token,
    )

    sent_id = int((result or {}).get("message_id") or 0)
    seen.add(chosen["key"])
    state["offset"] = max_update
    state["pending"] = fresh_pending
    state["last_copy"] = {
        **chosen,
        "sent_message_id": sent_id,
        "destination": "public_audience" if public_route else "private_review",
        "copied_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)
    print(
        "NATIVE TELEGRAM VIDEO TRANSFER:",
        json.dumps(
            {
                "delivered": True,
                "destination": "public_audience" if public_route else "private_review",
                "sent_message_id": sent_id,
                "source": chosen["source_name"],
                "source_username": chosen["source_username"],
                "source_message_id": chosen["message_id"],
                "views": chosen["views"],
                "reactions": chosen["reactions"],
                "score": chosen["score"],
                "duration": chosen["video_duration"],
                "pending_queue_size": len(fresh_pending),
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
