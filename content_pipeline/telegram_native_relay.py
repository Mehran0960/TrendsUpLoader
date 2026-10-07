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

CUES = re.compile(
    r"\b(caught|catch|eating|eats|fails|failed|fall|falls|fell|"
    r"surprise|surprising|unexpected|impossible|amazing|crazy|"
    r"reaction|reacts|wins|won|custom|artificial|transformation|"
    r"before|after|rescue|rescued|fight|fighting|chase|chasing|"
    r"jumpscare|save|saved|wow)\b",
    re.I,
)

BLOCK = re.compile(
    r"\b(politics|election|news|war|gore|explicit|nsfw|porn|"
    r"child|children|minor|toddler|schoolgirl|schoolboy)\b",
    re.I,
)

def api(method, payload=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, json=payload or {}, timeout=35)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data}")
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

def allowed_source(chat):
    try:
        policy=json.loads(POLICY.read_text(encoding="utf-8"))
    except Exception:
        policy={}
    if bool(policy.get("allow_all_channels_bot_can_receive")):
        return True
    usernames={str(x).lstrip("@").lower() for x in policy.get("allowed_usernames",[]) if str(x).strip()}
    chat_ids={str(x) for x in policy.get("allowed_chat_ids",[]) if str(x).strip()}
    username=str(chat.get("username") or "").lstrip("@").lower()
    cid=str(chat.get("id") or "")
    return (username and username in usernames) or (cid and cid in chat_ids)


def candidate(update):
    msg = update.get("channel_post") or {}
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id or chat_id == str(TARGET):
        return None
    if not allowed_source(chat):
        return None

    media = msg.get("video") or msg.get("animation")
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

    cue = 1 if CUES.search(caption) else 0
    freshness = max(0.0, 24.0 - min(age_hours, 24.0)) / 24.0

    # Within-source ranking: views dominate, then reactions/recency/curiosity.
    score = (
        12.0 * min(1.0, views / 100000.0)
        + 6.0 * min(1.0, reaction_count / 1000.0)
        + 4.0 * freshness
        + 4.0 * cue
        + min(4.0, len(caption) / 80.0)
    )

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
        raise RuntimeError("Telegram secrets missing.")

    s = load_state()
    offset = int(s.get("offset") or 0)

    updates = api(
        "getUpdates",
        {
            "offset": offset,
            "limit": 100,
            "timeout": 0,
            "allowed_updates": ["channel_post"],
        },
    ) or []

    candidates = []
    max_update = offset
    seen = set(str(x) for x in s.get("seen", []))
    for u in updates:
        uid = int(u.get("update_id") or 0)
        max_update = max(max_update, uid + 1)
        c = candidate(u)
        if not c:
            continue
        key = f'{c["chat_id"]}:{c["message_id"]}'
        if key in seen:
            continue
        c["key"] = key
        candidates.append(c)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    if not candidates:
        s["offset"] = max_update
        save_state(s)
        print("No eligible source-channel video.")
        return

    chosen = candidates[0]
    result = api(
        "copyMessage",
        {
            "chat_id": TARGET,
            "from_chat_id": int(chosen["chat_id"]) if chosen["chat_id"].lstrip("-").isdigit() else chosen["chat_id"],
            "message_id": chosen["message_id"],
            "caption": make_caption(chosen),
            "disable_notification": False,
        },
    )

    sent_id = int((result or {}).get("message_id") or 0)
    print(
        "NATIVE TELEGRAM COPY:",
        json.dumps(
            {
                "sent_message_id": sent_id,
                "source": chosen["source_name"],
                "source_username": chosen["source_username"],
                "source_message_id": chosen["message_id"],
                "views": chosen["views"],
                "reactions": chosen["reactions"],
                "score": chosen["score"],
                "duration": chosen["video_duration"],
            },
            ensure_ascii=False,
        ),
    )

    seen.add(chosen["key"])
    s["offset"] = max_update
    s["last_copy"] = {
        **chosen,
        "sent_message_id": sent_id,
        "copied_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(s)

if __name__ == "__main__":
    main()
