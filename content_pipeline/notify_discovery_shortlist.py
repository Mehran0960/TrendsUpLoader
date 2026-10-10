#!/usr/bin/env python3
"""Send an internal curator shortlist; this does not publish candidate media."""
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "attention_state" / "social_discovery.json"
STATE = ROOT / "attention_state" / "social_discovery_notification_state.json"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TARGET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_AGE_HOURS = 36.0
MIN_SCORE = 48.0
MAX_ITEMS = 3

def load_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default

def select_shortlist(items, notified_ids=None, now=None):
    """Select recent, promising, non-sensitive leads without claiming rights."""
    notified = set(str(x) for x in (notified_ids or []))
    now = now or datetime.now(timezone.utc)
    selected = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or item.get("url") or "")
        if not ident or ident in notified:
            continue
        try:
            score = float(item.get("follow_growth_score") or 0)
        except (TypeError, ValueError):
            continue
        if score < MIN_SCORE or item.get("score_confidence") not in {"medium", "high"}:
            continue
        if item.get("news_risk_signal") or item.get("sensitive_risk_signal"):
            continue
        try:
            age = float(item.get("age_hours"))
        except (TypeError, ValueError):
            continue
        if age < 0 or age > MAX_AGE_HOURS:
            continue
        if not str(item.get("url") or "").startswith(("https://", "http://")):
            continue
        selected.append(item)
        if len(selected) >= MAX_ITEMS:
            break
    return selected

def render_message(items):
    parts = ["<b>سرنخ‌های تازه برای جذب دنبال‌کننده</b>"]
    for index, item in enumerate(items, 1):
        title = html.escape(str(item.get("title") or "ویدئوی بدون عنوان")[:160])
        url = html.escape(str(item.get("url") or ""), quote=True)
        platform = html.escape(str(item.get("platform") or "نامشخص"))
        score = float(item.get("follow_growth_score") or 0)
        views = int(float(item.get("views") or 0))
        age = float(item.get("age_hours") or 0)
        views_text = f"{views:,}" if views else "نامشخص"
        parts.append(
            f"\n\n<b>{index}. {title}</b>\n"
            f"پلتفرم منبع: {platform} · امتیاز بررسی: {score:.0f}/100\n"
            f"بازدید ثبت‌شده: {views_text} · سن: {age:.1f} ساعت\n"
            f"<a href=\"{url}\">مشاهدهٔ منبع</a>"
        )
    parts.append("\n\n⚠️ این‌ها فقط سرنخ‌اند؛ امتیاز، مجوز بازنشر یا تضمین رشد نیست. پیش از استفاده، ویدئو و حقوق آن جداگانه بررسی شوند.")
    return "".join(parts)[:3900]

def main():
    if not TOKEN or not TARGET:
        print("DISCOVERY_SHORTLIST_NOTIFY_SKIPPED: Telegram credentials not configured.")
        return 0
    data = load_json(INPUT, {})
    state = load_json(STATE, {"version": 1, "notified_ids": []})
    notified = [str(x) for x in state.get("notified_ids", [])]
    candidates = select_shortlist(data.get("items", []), notified)
    if not candidates:
        print("DISCOVERY_SHORTLIST_EMPTY: no new eligible leads.")
        return 0
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": TARGET, "text": render_message(candidates), "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram sendMessage returned ok=false")
    new_ids = [str(x.get("id") or x.get("url")) for x in candidates]
    state.update({
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_message_id": (result.get("result") or {}).get("message_id"),
        "notified_ids": list(dict.fromkeys(notified + new_ids))[-1000:],
        "last_count": len(candidates),
    })
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({"DISCOVERY_SHORTLIST_SENT": len(candidates), "ids": new_ids}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
