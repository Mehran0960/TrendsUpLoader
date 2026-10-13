#!/usr/bin/env python3
"""Forward one stronger, source-attributed Telegram video as a one-off quality sample."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "attention_state" / "social_discovery_notification_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PRIVATE_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
PUBLIC_TARGETS = ("@TapTerend", "@trendup_channel")

# This candidate was ranked highly for a comedy hook. Native forwarding preserves
# the source channel attribution, unlike downloading and re-uploading the file.
CANDIDATES = [
    {"source": "@keereker", "message_id": 256581, "url": "https://t.me/keereker/256581",
     "title": "کلیپ طنز با قلاب واکنشی", "score": 60.2, "lanes": ["comedy", "nature_and_wildlife"]},
]


def api(method, payload):
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok or not body.get("ok"):
        code = body.get("error_code", response.status_code)
        description = str(body.get("description") or "")[:200]
        raise RuntimeError(f"{method} failed code={code}: {description}")
    return body.get("result") or {}


def mark_reviewed(url):
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {"version": 1}
    for key in ("media_reviewed_ids", "media_reviewed_urls"):
        values = [str(x) for x in state.get(key, [])]
        state[key] = list(dict.fromkeys(values + [url]))[-1000:]
    state["media_review_updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if not TOKEN or not PRIVATE_CHAT:
    raise SystemExit("Telegram review credentials are not configured.")

delivered = []
errors = []
for candidate in CANDIDATES:
    # First prove the source is forwardable to the private review chat.
    try:
        result = api("forwardMessage", {
            "chat_id": PRIVATE_CHAT,
            "from_chat_id": candidate["source"],
            "message_id": candidate["message_id"],
        })
    except Exception as exc:
        errors.append({"source": candidate["url"], "target": "private_review", "error": str(exc)})
        print(f"QUALITY_SAMPLE_SOURCE_FORWARD_FAILED source={candidate['url']} error={type(exc).__name__}: {str(exc)[:200]}")
        continue

    private_id = result.get("message_id")
    delivered.append({"target": "private_review", "message_id": private_id})
    print(f"QUALITY_SAMPLE_FORWARDED target=private_review message_id={private_id} source={candidate['url']}")

    # Only distribute after private forwarding succeeds; native forwards retain attribution.
    public_ok = []
    for target in PUBLIC_TARGETS:
        try:
            message = api("forwardMessage", {
                "chat_id": target,
                "from_chat_id": candidate["source"],
                "message_id": candidate["message_id"],
            })
            public_ok.append(target)
            delivered.append({"target": target, "message_id": message.get("message_id")})
            print(f"QUALITY_SAMPLE_FORWARDED target={target} message_id={message.get('message_id')} source={candidate['url']}")
        except Exception as exc:
            errors.append({"source": candidate["url"], "target": target, "error": str(exc)})
            print(f"QUALITY_SAMPLE_FORWARD_FAILED target={target} source={candidate['url']} error={type(exc).__name__}: {str(exc)[:200]}")

    if len(public_ok) == len(PUBLIC_TARGETS):
        mark_reviewed(candidate["url"])
        print("QUALITY_SAMPLE_DELIVERY_SUMMARY", {
            "source": candidate["url"],
            "title": candidate["title"],
            "score": candidate["score"],
            "lanes": candidate["lanes"],
            "delivered": delivered,
            "errors": errors,
        })
        raise SystemExit(0)
    raise SystemExit(3)

print("QUALITY_SAMPLE_DELIVERY_SUMMARY", {"delivered": delivered, "errors": errors})
raise SystemExit(2)
