#!/usr/bin/env python3
import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import attention_engine as engine

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "attention_state"
OUT_DIR = ROOT / "out"
NATIVE_STATE = STATE_DIR / "native_posted.json"
TARGET_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ENABLE_LICENSED_NATIVE = os.environ.get("ENABLE_LICENSED_NATIVE", "0").strip() == "1"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

HOOKS = {
    "animals": [
        "صبر کن ببین آخرش چی میشه 😳",
        "این صحنه خیلی عادی شروع شد… 😂",
        "این یکی رو تا آخر ببین! 👀",
    ],
    "human_funny": [
        "اینو نمی‌شه جدی دید 😂",
        "فقط صبر کن ببین آخرش چی میشه 😳",
        "این پایان رو حدس نمی‌زدی 😂",
    ],
    "beauty_style": [
        "صبر کن قسمت آخر رو ببینی 😳",
        "این اجرا یه جای کارش عجیبه 👀",
        "تا آخرش ارزش دیدن داره!",
    ],
    "talent": [
        "تا ثانیه آخر صبر کن 😳",
        "این قسمت رو خیلی‌ها دوباره می‌بینن 👀",
        "صبر کن ببینی چی کار می‌کنه!",
    ],
    "wow": [
        "فکر نمی‌کنی آخرش این‌طوری بشه 😳",
        "صبر کن لحظه اصلی رو ببینی!",
        "این یکی واقعاً عجیبه 👀",
    ],
    "sports": [
        "لحظه اصلی آخرشه 😳",
        "این حرکت رو دوباره ببین!",
        "صبر کن ببینی چی میشه 👀",
    ],
    "food": [
        "قسمت آخرش عجیب رضایت‌بخشه 😳",
        "صبر کن نتیجه رو ببینی!",
        "این یکی رو تا آخر ببین 👀",
    ],
    "cars": [
        "صبر کن تغییر آخر رو ببینی 😳",
        "این قبل و بعد رو ببین!",
        "لحظه آخر ارزشش رو داره 👀",
    ],
    "satisfying": [
        "تا آخرش صبر کن 😳",
        "قسمت آخر فوق‌العاده‌ست 👀",
        "این یکی رو نمی‌تونی نصفه رها کنی!",
    ],
    "travel": [
        "صبر کن نمای آخر رو ببینی 😳",
        "این منظره واقعی به نظر نمیاد!",
        "تا آخرش ببین 👀",
    ],
    "tech": [
        "این دیگه گجت معمولی نیست 😳",
        "صبر کن ببینی چطور کار می‌کنه!",
        "قسمت آخر عجیبه 👀",
    ],
}

BLOCK = re.compile(
    r"\b(movie|trailer|official|music video|full episode|podcast|news|politics|election|minor|child|children|toddler|schoolgirl|schoolboy|explicit|gore|logo)\b",
    re.I,
)


def load_state():
    try:
        data = json.loads(NATIVE_STATE.read_text(encoding="utf-8"))
    except Exception:
        data = {"version": 1, "ids": [], "pages": []}
    if not isinstance(data, dict):
        data = {"version": 1, "ids": [], "pages": []}
    data.setdefault("ids", [])
    data.setdefault("pages", [])
    return data


def save_state(data):
    STATE_DIR.mkdir(exist_ok=True)
    data["ids"] = list(dict.fromkeys([str(x) for x in data.get("ids", [])]))[-300:]
    data["pages"] = list(dict.fromkeys([str(x) for x in data.get("pages", [])]))[-300:]
    NATIVE_STATE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def telegram_send_video(path, caption):
    import requests

    if not BOT_TOKEN or not TARGET_CHAT:
        raise RuntimeError("Telegram secrets missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    with open(path, "rb") as fh:
        resp = requests.post(
            url,
            data={
                "chat_id": TARGET_CHAT,
                "caption": caption[:1024],
                "supports_streaming": "true",
            },
            files={"video": (path.name, fh, "video/mp4")},
            timeout=120,
        )
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendVideo failed: {result}")
    return (result.get("result") or {}).get("message_id")


def rank_categories(signals):
    buckets = {}
    for s in signals:
        cat = str(s.get("category") or "")
        if cat in engine.DISCOVERY:
            buckets.setdefault(cat, []).append(s)
    ranked = []
    for cat, items in buckets.items():
        vals = sorted(
            [float(x.get("demand_score") or 0.0) for x in items],
            reverse=True,
        )
        if vals:
            regions = max(int(x.get("chart_region_count") or 0) for x in items)
            ranked.append(
                (0.72 * vals[0] + 0.18 * (sum(vals[:5]) / min(5, len(vals)))
                 + 2.5 * min(regions, 4), cat)
            )
    ranked.sort(reverse=True)
    return [x[1] for x in ranked[:3]]


def native_candidates(categories):
    found = []
    for category in categories:
        try:
            found.extend(engine.discover_pixabay(category, limit=8))
        except Exception as exc:
            print("Pixabay error", category, repr(exc))
        try:
            found.extend(
                [
                    x for x in engine.discover_sources(categories=[category])
                    if "cc0" in str(x.get("license") or "").lower()
                ][:8]
            )
        except Exception as exc:
            print("Commons error", category, repr(exc))

    dedup = {}
    for x in found:
        dedup[str(x.get("id"))] = x
    return list(dedup.values())


def evaluate_candidate(src):
    page = str(src.get("page") or "")
    sid = str(src.get("id") or "")
    if not sid or sid == "None":
        return None
    if BLOCK.search(
        " ".join(
            [
                str(src.get("title") or ""),
                str(src.get("description") or ""),
                page,
                str(src.get("tags") or ""),
            ]
        )
    ):
        return None
    if page in load_state().get("pages", []):
        return None

    path = engine.download_source(src)
    info = engine.probe(path)
    total = float(info.get("duration") or 0.0)
    if total < max(1.0, engine.MIN_TOTAL):
        return None

    visual = engine.visual_score(path)
    src = dict(src)
    src["start"] = 0.0
    src["duration"] = round(total, 3)
    hook = engine.source_hook_score(src, visual)
    rel = engine.source_relevance_score(src)
    orient = engine.orientation_score(info.get("width"), info.get("height"))
    pop = float(src.get("attention_score") or 65.0)

    # For a native post, hook and visual quality dominate. Popularity is only
    # a supporting signal because stock-library views are not platform virality.
    combined = (
        0.36 * hook
        + 0.26 * float(visual.get("score") or 0.0)
        + 0.18 * rel
        + 0.10 * pop
        + 0.06 * orient
        + 0.04 * min(100.0, 100.0 if total <= 30 else 75.0)
    )

    prepared = {
        **src,
        "_path": str(path),
        "_info": info,
        "hook_score": hook,
        "visual_score": float(visual.get("score") or 0.0),
        "relevance_score": rel,
        "orientation_score": orient,
        "combined_score": round(combined, 2),
        "visual_detail": visual,
    }

    print(
        "NATIVE EVAL",
        sid,
        "provider=", src.get("provider"),
        "combined=", round(combined, 2),
        "hook=", hook,
        "visual=", round(float(visual.get("score") or 0.0), 2),
        "relevance=", rel,
        "pop=", pop,
        "duration=", round(total, 2),
    )

    if hook < 62 or float(visual.get("score") or 0) < 55 or rel < 65 or combined < 74:
        return None
    return prepared


def main():
    if not ENABLE_LICENSED_NATIVE:
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "native_skip.json").write_text(
            json.dumps({"reason":"licensed_native_disabled_until_source_quality_is_proven"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("LICENSED NATIVE AUTO-PUBLISH DISABLED")
        return
    OUT_DIR.mkdir(exist_ok=True)
    state = load_state()

    signals = engine.discover_youtube_global_charts(limit_per_bucket=8)
    feed = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": "native_telegram_v1",
        "signals": signals[:40],
    }
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / "public_radar.json").write_text(
        json.dumps(feed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    cats = rank_categories(signals)
    print("Native target categories:", cats)
    pool = native_candidates(cats)
    print("Native acquisition pool:", len(pool))

    scored = []
    for src in pool[:12]:
        try:
            x = evaluate_candidate(src)
            if x:
                scored.append(x)
        except Exception as exc:
            print("NATIVE EVAL FAILED", src.get("id"), repr(exc))

    scored.sort(key=lambda x: float(x.get("combined_score") or 0), reverse=True)
    if not scored:
        (OUT_DIR / "native_skip.json").write_text(
            json.dumps(
                {
                    "reason": "no_native_candidate_passed",
                    "categories": cats,
                    "pool": len(pool),
                    "checked": min(12, len(pool)),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print("NO NATIVE CANDIDATE")
        save_state(state)
        return

    chosen = scored[0]
    src_path = Path(chosen["_path"])
    hook = random.choice(HOOKS.get(str(chosen.get("category")), HOOKS["wow"]))
    cap_path = OUT_DIR / "native_caption.png"
    engine.caption_png(hook, cap_path, big=False)
    rendered = OUT_DIR / "native_video.mp4"
    engine.render_scene(
        src_path,
        chosen,
        cap_path,
        rendered,
    )

    # Keep the final creative work under Telegram's current standard sendVideo
    # size limit. We reject rather than create a second clip or silently trim.
    if rendered.stat().st_size > 50_000_000:
        print("NATIVE REJECT SIZE", rendered.stat().st_size)
        (OUT_DIR / "native_skip.json").write_text(
            json.dumps(
                {"reason": "telegram_size_limit", "bytes": rendered.stat().st_size},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        save_state(state)
        return

    caption = (
        hook
        + "\n\n"
        + "منبع: "
        + str(chosen.get("provider") or "Commons")
        + (" — CC0" if "cc0" in str(chosen.get("license") or "").lower() else "")
    )
    message_id = telegram_send_video(rendered, caption)

    page = str(chosen.get("page") or "")
    sid = str(chosen.get("id") or "")
    state.setdefault("ids", []).append(sid)
    state.setdefault("pages", []).append(page)
    state["last_post"] = {
        "message_id": message_id,
        "source_id": sid,
        "provider": chosen.get("provider"),
        "page": page,
        "category": chosen.get("category"),
        "hook": hook,
        "combined_score": chosen.get("combined_score"),
        "hook_score": chosen.get("hook_score"),
        "visual_score": chosen.get("visual_score"),
        "relevance_score": chosen.get("relevance_score"),
        "duration": chosen.get("duration"),
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    Path("out/native_post.json").write_text(
        json.dumps(state["last_post"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("NATIVE TELEGRAM POSTED", state["last_post"])


if __name__ == "__main__":
    main()
