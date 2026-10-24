#!/usr/bin/env python3
"""Independent FiLo publisher/probe for a top-ranked Telegram video candidate.

# Probe revision: authenticate with current repository secrets on each code-triggered safe run.

This adapter intentionally does not affect the existing discovery/review workflow.
The default mode only inspects the authenticated upload form; public upload needs
an explicit --publish flag. Login secrets are read from the environment and never
printed or written to the repository.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import html

import requests

try:
    from content_pipeline.send_review_videos import (
        INPUT as DISCOVERY_INPUT,
        MAX_VIDEO_BYTES,
        clean_review_caption,
        download_direct_telegram_post,
        duration_content_gate,
        inspect_video_quality,
        load_json,
        select_media_candidates,
    )
except ModuleNotFoundError:
    from send_review_videos import (
        INPUT as DISCOVERY_INPUT,
        MAX_VIDEO_BYTES,
        clean_review_caption,
        download_direct_telegram_post,
        duration_content_gate,
        inspect_video_quality,
        load_json,
        select_media_candidates,
    )

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "attention_state" / "ifilo_upload_state.json"
ARTIFACTS = ROOT / "artifacts" / "ifilo"
# Prefer the public canonical domain. The legacy dashboard host was observed
# redirecting its root to an unrelated parked domain, so do not use it for login.
BASE_URL = os.environ.get("IFILO_BASE_URL", "https://ifilo.net").strip().rstrip("/")
UPLOAD_URL = BASE_URL + "/upload"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
MAX_CANDIDATES_TO_TRY = 50

TITLE_SELECTORS = (
    'input[name*="title" i]',
    'textarea[name*="title" i]',
    'textarea[id*="title" i]',
    'input[id*="title" i]',
    'input[placeholder*="عنوان"]',
    'input[aria-label*="عنوان"]',
    'input[name*="subject" i]',
    'input[id*="subject" i]',
)
DESCRIPTION_SELECTORS = (
    'textarea[name*="description" i]',
    'textarea[id*="description" i]',
    'textarea[placeholder*="توضیحات"]',
    'textarea[aria-label*="توضیحات"]',
    'textarea[name*="summary" i]',
    'textarea[id*="summary" i]',
    'textarea[name*="body" i]',
    'textarea',
    '[contenteditable="true"]',
)
LOGIN_BUTTON_RE = re.compile(r"ورود|login|sign\s*in|ادامه", re.I)
SUBMIT_BUTTON_RE = re.compile(r"آپلود|بارگذاری|ارسال|ثبت|انتشار|ذخیره|upload|submit|publish|save", re.I)
SUCCESS_TEXT_RE = re.compile(
    r"با\s*موفقیت|آپلود\s*شد|بارگذاری\s*شد|ارسال\s*شد|ثبت\s*شد|منتشر\s*شد|"
    r"در\s*صف\s*(?:بررسی|پردازش)|در\s*انتظار\s*(?:بررسی|پردازش)|upload(?:ed)?\s*success|"
    r"successfully\s*(?:uploaded|submitted)|video\s*(?:was\s*)?uploaded",
    re.I,
)
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)
URL_RE = re.compile(r"https?://\S+", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\u0600-\u06ff]+", re.I)
SPACE_RE = re.compile(r"\s+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_text(value: object, limit: int = 1000) -> str:
    """Clean title/description text without changing the source video itself."""
    text = html.unescape(str(value or ""))
    text = URL_RE.sub(" ", text)
    text = HASHTAG_RE.sub(" ", text)
    text = EMOJI_RE.sub(" ", text)
    text = re.sub(r"[\u200b\u200e\u200f\ufeff]", "", text)
    text = re.sub(r"[|•]+", " ", text)
    text = SPACE_RE.sub(" ", text).strip(" \t\r\n-–—:؛،,.")
    return text[:limit].strip()


def make_title(item: dict) -> str:
    """Produce a concise descriptive title, not a verbatim copied headline."""
    caption = str(item.get("source_post_caption") or item.get("title") or "").strip()
    caption = clean_review_caption(caption, max_chars=500)
    title = normalized_text(caption, limit=120)
    if not title:
        title = normalized_text(item.get("title") or item.get("description") or "", limit=120)
    if not title:
        raise ValueError("No meaningful title could be derived from this candidate.")
    # Add context words rather than copying the source headline as-is.
    if not re.match(r"^(لحظه|ماجر|روایت|نگاه|ویدیو|اتفاق)", title, re.I):
        title = "لحظه‌ای دیدنی از " + title
    return title[:100].rstrip()


def make_description(item: dict, title: str) -> str:
    caption = str(item.get("source_post_caption") or item.get("description") or item.get("title") or "")
    body = clean_review_caption(caption, max_chars=1400)
    if not body or normalized_text(body).casefold() == normalized_text(title).casefold():
        body = title
    lane_labels = {
        "comedy": "طنز و سرگرمی",
        "animals": "حیوانات",
        "surprise_or_reveal": "اتفاق غیرمنتظره",
        "transformation_or_satisfying": "تغییر و فرایند دیدنی",
        "skill_or_spectacle": "مهارت و صحنه دیدنی",
        "technology_demo": "فناوری",
        "human_reaction_or_relatable": "واکنش انسانی",
        "food_cooking": "آشپزی و غذا",
        "practical_tips_or_life_hacks": "ترفند کاربردی",
        "wholesome_or_heartwarming": "لحظه دلنشین",
        "sports_or_athletic_skill": "ورزش",
        "nature_and_wildlife": "طبیعت و حیات وحش",
        "science_curiosity": "دانستنی و علم",
        "diy_and_creativity": "خلاقیت و ساخت",
        "home_organization_or_cleaning": "خانه و تمیزکاری",
        "music_or_dance": "موسیقی و رقص",
        "fashion_or_cultural_showcase": "فرهنگ و پوشش",
        "travel_and_places": "گردشگری",
        "puzzles_and_visual_illusions": "معما و خطای دید",
        "nostalgia_and_then_now": "نوستالژی",
    }
    lanes = [lane_labels[x] for x in item.get("hook_signals", []) if x in lane_labels]
    if lanes:
        body = body + "\n\nموضوع: " + "، ".join(dict.fromkeys(lanes[:3]))
    return body[:1800]


def source_key(item: dict) -> str:
    value = str(item.get("url") or item.get("id") or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def load_state(path: Path = STATE_PATH) -> dict:
    value = load_json(path, {})
    if not isinstance(value, dict):
        value = {}
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    return {"schema_version": 1, "updated_at": value.get("updated_at"), "attempts": attempts}


def has_prior_attempt(item: dict, state: dict) -> bool:
    key = source_key(item)
    url = str(item.get("url") or "")
    return bool(key) and any(
        isinstance(row, dict)
        and (
            str(row.get("source_key") or "") == key
            or (url and str(row.get("source_url") or "") == url)
        )
        for row in state.get("attempts", [])
    )


def write_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_storage_state(path: Path) -> dict | None:
    """Decode an optional Playwright storage state secret without logging it."""
    raw = os.environ.get("IFILO_STORAGE_STATE_B64", "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except Exception:
        decoded = raw
    state = json.loads(decoded)
    if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list):
        raise ValueError("IFILO_STORAGE_STATE_B64 is not a valid Playwright storage-state JSON.")
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def _locator_is_ready(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible() and locator.first.is_enabled()
    except Exception:
        return False


def _first_ready(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        if _locator_is_ready(locator):
            return locator
    return None


def _visible_control_summary(page) -> dict:
    """Return non-sensitive form metadata: never include input values or cookies."""
    controls = page.locator("input, textarea, select, [contenteditable='true']").evaluate_all(
        """els => els.map(el => {
          const label = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
          const parentLabel = el.closest('label');
          const style = getComputedStyle(el);
          return {
            tag: (el.tagName || '').toLowerCase(),
            type: (el.getAttribute('type') || '').toLowerCase(),
            name: el.getAttribute('name') || '',
            id: el.id || '',
            placeholder: el.getAttribute('placeholder') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            label: (label?.innerText || parentLabel?.innerText || '').trim().slice(0, 80),
            labelVisible: !!((label || parentLabel) && ((label || parentLabel).offsetWidth || (label || parentLabel).offsetHeight || (label || parentLabel).getClientRects().length)),
            checked: ['checkbox','radio'].includes((el.getAttribute('type') || '').toLowerCase()) ? !!el.checked : null,
            disabled: !!el.disabled,
            required: !!el.required,
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            display: style.display,
            visibility: style.visibility,
            opacity: style.opacity,
            parentClass: el.parentElement ? String(el.parentElement.className || '').slice(0, 100) : ''
          };
        }).slice(0, 60)"""
    )
    buttons = page.locator("button, input[type='submit'], [role='button']").evaluate_all(
        """els => els.map(el => ({
          tag: (el.tagName || '').toLowerCase(),
          type: el.getAttribute('type') || '',
          text: (el.innerText || el.getAttribute('value') || el.getAttribute('aria-label') || '').trim().slice(0, 90),
          disabled: !!el.disabled
        })).slice(0, 60)"""
    )
    forms = page.locator("form").evaluate_all(
        """els => els.map(el => ({
          method: (el.getAttribute('method') || 'get').toLowerCase(),
          actionPath: (() => { try { return new URL(el.action, location.href).pathname; } catch { return ''; } })(),
          enctype: el.getAttribute('enctype') || ''
        })).slice(0, 20)"""
    )
    parsed = urlparse(page.url)
    try:
        visible_text = page.locator("body").inner_text(timeout=3000)
        relevant_text = [
            line.strip()[:100] for line in visible_text.splitlines()
            if line.strip() and re.search(r"آپلود|ویدیو|عنوان|توضیح|قوانین|کسب درآمد|بازنشر|منتشر|فایل|تایید|تأیید|تصویر", line, re.I)
        ][:50]
    except Exception:
        relevant_text = []
    try:
        clickable = page.locator("button, label, [role='button'], [onclick], a[href]").evaluate_all(
            """els => els.map(el => {
              const style = getComputedStyle(el);
              return {
                tag: (el.tagName || '').toLowerCase(),
                id: el.id || '',
                cls: String(el.className || '').slice(0, 80),
                text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ').slice(0, 90),
                hrefPath: (() => { try { return new URL(el.href, location.href).pathname; } catch { return ''; } })(),
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                display: style.display,
                visibility: style.visibility
              };
            }).filter(el => /upload|video|file|publish|accept|rule|title|description|dontPublish|canMakeMoney|canRepublish|thumbnail|comment|madeForKids|over18|آپلود|ویدیو|فایل|عنوان|توضیح|انتشار|قوانین/i.test(el.id + ' ' + el.cls + ' ' + el.text)).slice(0, 80)"""
        )
    except Exception:
        clickable = []
    return {
        # Never store query strings, cookies, input values, or auth state in diagnostics.
        "host": parsed.netloc,
        "page_url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "page_path": parsed.path,
        "title": (page.title() or "")[:120],
        "inputs_and_fields": controls,
        "buttons": buttons,
        "forms": forms,
        "relevant_visible_text": relevant_text,
        "interactive_elements": clickable,
        "file_input_count": sum(1 for row in controls if row.get("type") == "file"),
        "password_input_present": any(row.get("type") == "password" for row in controls),
    }


def _find_login_input(page):
    selectors = (
        'input[type="email"]',
        'input[type="tel"]',
        'input[name*="phone" i]',
        'input[name*="mobile" i]',
        'input[name*="user" i]',
        'input[name*="email" i]',
        'input[placeholder*="موبایل"]',
        'input[placeholder*="شماره"]',
        'input[placeholder*="ایمیل"]',
        'input[type="text"]',
    )
    for selector in selectors:
        for locator in page.locator(selector).all():
            try:
                if not locator.is_visible() or not locator.is_enabled():
                    continue
                placeholder = str(locator.get_attribute("placeholder") or "").lower()
                name = str(locator.get_attribute("name") or "").lower()
                if "search" in name or "جستجو" in placeholder or "search" in placeholder:
                    continue
                return locator
            except Exception:
                continue
    return None


def ensure_authenticated(page, context) -> bool:
    """Use the site's normal login page; never bypass interactive verification."""
    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    current = urlparse(page.url)
    expected_host = urlparse(BASE_URL).netloc.lower()
    current_host = current.netloc.lower()
    if current_host != expected_host:
        print("IFILO_HOST_REDIRECTED " + json.dumps({
            "expected_host": expected_host,
            "actual_host": current_host,
            "path": current.path,
            "reason": "upload_route_redirected_away_from_configured_host",
        }, ensure_ascii=False))
        return False

    if page.locator('input[type="file"]').count() > 0:
        return True

    # FiLo currently responds to an unauthenticated /upload request with an
    # access-denied page that contains a normal /login link. Follow that link
    # instead of treating the 403 page as an upload form.
    if "/login" not in current.path.lower():
        login_link = page.locator('a[href="/login"], a[href^="/login"], a:has-text("ورود")')
        clicked = False
        try:
            for index in range(login_link.count()):
                candidate = login_link.nth(index)
                if candidate.is_visible() and candidate.is_enabled():
                    candidate.click(timeout=8000)
                    clicked = True
                    break
        except Exception:
            clicked = False
        if clicked:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1200)
        if "/login" not in urlparse(page.url).path.lower():
            page.goto(BASE_URL + "/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)

    current = urlparse(page.url)
    current_host = current.netloc.lower()
    if current_host != expected_host:
        print("IFILO_LOGIN_HOST_REDIRECTED " + json.dumps({
            "expected_host": expected_host,
            "actual_host": current_host,
            "path": current.path,
            "reason": "login_route_redirected_away_from_configured_host",
        }, ensure_ascii=False))
        return False

    username = os.environ.get("IFILO_LOGIN_ID", "").strip()
    password = os.environ.get("IFILO_PASSWORD", "")
    if "/login" not in current.path.lower():
        print("IFILO_LOGIN_ROUTE_NOT_REACHED " + json.dumps(
            _visible_control_summary(page), ensure_ascii=False
        ))
        probe_upload_form(page)
        return False

    body = ""
    try:
        body = page.locator("body").inner_text(timeout=5000)[:8000]
    except Exception:
        pass
    challenge = re.search(
        r"captcha|recaptcha|رمز\s*یکبار\s*مصرف|کد\s*(?:پیامک|امنیتی|تأیید|تایید)|"
        r"one.?time\s*password|two.?factor|\botp\b",
        body, re.I
    )
    if challenge:
        print("IFILO_INTERACTIVE_LOGIN_REQUIRED: Sign-in asks for a verification step; the automation will not bypass it.")
        probe_upload_form(page)
        return False

    if not username or not password:
        print("IFILO_AUTH_REQUIRED: Configure IFILO_LOGIN_ID and IFILO_PASSWORD as repository Actions secrets, or provide IFILO_STORAGE_STATE_B64.")
        probe_upload_form(page)
        return False

    user_field = _find_login_input(page)
    password_field = _first_ready(page, ('input[type="password"]',))
    if user_field is None or password_field is None:
        print("IFILO_LOGIN_FORM_UNSUPPORTED: Login page did not expose both an identifier and password field.")
        probe_upload_form(page)
        return False

    user_field.fill(username)
    password_field.fill(password)
    login_button = None
    for locator in page.locator("button, input[type='submit'], [role='button']").all():
        try:
            label = str(locator.inner_text(timeout=1000) or locator.get_attribute("value") or locator.get_attribute("aria-label") or "").strip()
            if locator.is_visible() and locator.is_enabled() and LOGIN_BUTTON_RE.search(label):
                login_button = locator
                break
        except Exception:
            continue
    if login_button is None:
        login_button = _first_ready(page, ('form button[type="submit"]', 'form input[type="submit"]'))
    if login_button is None:
        print("IFILO_LOGIN_SUBMIT_NOT_FOUND")
        probe_upload_form(page)
        return False

    try:
        login_button.click(timeout=20000)
        page.wait_for_timeout(2500)
    except Exception as exc:
        print("IFILO_LOGIN_SUBMIT_ERROR", type(exc).__name__)
        return False

    current = urlparse(page.url)
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=5000)[:8000]
    except Exception:
        pass
    if current.netloc.lower() != expected_host:
        print("IFILO_LOGIN_HOST_REDIRECTED " + json.dumps({
            "expected_host": expected_host,
            "actual_host": current.netloc.lower(),
            "path": current.path,
            "reason": "post_login_redirect_left_configured_host",
        }, ensure_ascii=False))
        return False
    if re.search(
        r"captcha|recaptcha|رمز\s*یکبار\s*مصرف|کد\s*(?:پیامک|امنیتی|تأیید|تایید)|"
        r"one.?time\s*password|two.?factor|\botp\b",
        body, re.I
    ):
        print("IFILO_INTERACTIVE_LOGIN_REQUIRED: Manual verification is required; automation stopped without bypass.")
        probe_upload_form(page)
        return False
    if "/login" in current.path.lower() or page.locator('input[type="password"]').count() > 0:
        print("IFILO_LOGIN_INCOMPLETE: Login did not finish; an extra step may be required.")
        probe_upload_form(page)
        return False

    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    current = urlparse(page.url)
    if current.netloc.lower() != expected_host:
        print("IFILO_HOST_REDIRECTED " + json.dumps({
            "expected_host": expected_host,
            "actual_host": current.netloc.lower(),
            "path": current.path,
            "reason": "post_login_upload_redirect_left_configured_host",
        }, ensure_ascii=False))
        return False
    if page.locator('input[type="file"]').count() == 0:
        print("IFILO_UPLOAD_FORM_NOT_FOUND_AFTER_LOGIN " + json.dumps(
            _visible_control_summary(page), ensure_ascii=False
        ))
        probe_upload_form(page)
        return False
    return True


def probe_upload_form(page) -> dict:
    summary = _visible_control_summary(page)
    print("IFILO_UPLOAD_FORM_PROBE " + json.dumps(summary, ensure_ascii=False))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "upload_form_probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def find_title_field(page):
    locator = _first_ready(page, TITLE_SELECTORS)
    if locator is not None:
        return locator
    # Do not guess at the generic first input (it can be the site-wide search).
    for locator in page.locator("input[type='text']").all():
        try:
            attrs = " ".join([
                str(locator.get_attribute("name") or ""),
                str(locator.get_attribute("id") or ""),
                str(locator.get_attribute("placeholder") or ""),
                str(locator.get_attribute("aria-label") or ""),
            ]).lower()
            if any(term in attrs for term in ("search", "جستجو", "username", "user", "نام کاربری")):
                continue
            if any(term in attrs for term in ("title", "عنوان", "subject", "video")) and locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def find_description_field(page):
    locator = _first_ready(page, DESCRIPTION_SELECTORS)
    if locator is not None:
        return locator
    return None


def fill_metadata(page, title: str, description: str) -> tuple[bool, bool]:
    title_field = find_title_field(page)
    desc_field = find_description_field(page)
    title_ok = False
    desc_ok = False
    if title_field is not None:
        try:
            title_field.fill(title)
            title_ok = True
        except Exception:
            title_ok = False
    if desc_field is not None:
        try:
            desc_field.fill(description)
            desc_ok = True
        except Exception:
            desc_ok = False
    return title_ok, desc_ok


def choose_submit_button(page):
    # Prefer form submit controls, then a button with a clearly relevant label.
    for locator in page.locator("form button[type='submit'], form input[type='submit']").all():
        try:
            if locator.is_visible() and locator.is_enabled():
                label = str(locator.inner_text(timeout=1000) or locator.get_attribute("value") or "").strip()
                if not label or SUBMIT_BUTTON_RE.search(label):
                    return locator
        except Exception:
            continue
    for locator in page.locator("button, input[type='submit'], [role='button']").all():
        try:
            label = str(locator.inner_text(timeout=1000) or locator.get_attribute("value") or locator.get_attribute("aria-label") or "").strip()
            if locator.is_visible() and locator.is_enabled() and SUBMIT_BUTTON_RE.search(label):
                if not re.search(r"انتخاب فایل|choose file|browse", label, re.I):
                    return locator
        except Exception:
            continue
    return None


def _upload_url_from_page(page, prior_url: str) -> str:
    current = str(page.url or "")
    parsed = urlparse(current)
    if (
        current
        and current != prior_url
        and (parsed.netloc.endswith("ifilo.net") or parsed.netloc.endswith("tablighgram.com"))
        and re.search(r"/(?:v|video|watch)/[^/]+", parsed.path, re.I)
        and "/login" not in parsed.path.lower()
    ):
        return current.split("?", 1)[0]
    try:
        anchors = page.locator("a[href]").evaluate_all(
            """els => els.map(a => ({href: a.href, text: (a.innerText || '').trim()}))
               .filter(a => {
                 try { return /\/(?:v|video|watch)\//i.test(new URL(a.href).pathname); }
                 catch { return false; }
               })
               .slice(0, 20)"""
        )
        for row in anchors:
            if row.get("href"):
                return str(row["href"]).split("?", 1)[0]
    except Exception:
        pass
    return ""

def rights_cleared_for_ifilo(item: dict) -> bool:
    """Public reposts need explicit rights evidence; popularity is never permission."""
    item = item if isinstance(item, dict) else {}
    evidence = str(item.get("rights_evidence_url") or item.get("license_url") or "").strip()
    return item.get("rights_cleared_for_publication") is True and bool(evidence)


def _eligible_candidates() -> list[dict]:
    payload = load_json(DISCOVERY_INPUT, {"items": []})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []
    # Keep this first adapter narrow: the current pipeline has a stable direct
    # media route for native Telegram videos, while other sources are discovery-only.
    # Filter to the only source family with a stable direct-file acquisition
    # path before ranking, so a different platform cannot crowd Telegram out.
    native_telegram_items = [
        row for row in items
        if isinstance(row, dict)
        and str(row.get("platform") or "").lower() == "telegram"
        and str(row.get("source") or "").lower() == "telegram_native_video"
        and rights_cleared_for_ifilo(row)
    ]
    candidates = select_media_candidates(
        native_telegram_items,
        reviewed_ids=set(),
        limit=MAX_CANDIDATES_TO_TRY,
    )
    state = load_state()
    eligible = [row for row in candidates if not has_prior_attempt(row, state)]
    return eligible


def prepare_candidate_video(item: dict, workdir: Path) -> tuple[Path | None, dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    url = str(item.get("url") or "").strip()
    if not url:
        return None, {"passed": False, "reason": "missing_source_url"}
    target = workdir / "selected-video.mp4"
    if not download_direct_telegram_post(url, target, item=item):
        return None, {"passed": False, "reason": "source_video_download_failed"}
    if not target.exists() or not 0 < target.stat().st_size <= MAX_VIDEO_BYTES:
        target.unlink(missing_ok=True)
        return None, {"passed": False, "reason": "downloaded_file_missing_or_too_large"}
    quality = inspect_video_quality(target)
    if not quality.get("passed"):
        target.unlink(missing_ok=True)
        return None, quality
    accepted, reason = duration_content_gate(item, quality)
    quality["content_gate_passed"] = accepted
    quality["content_gate_reason"] = reason
    if not accepted:
        target.unlink(missing_ok=True)
        return None, quality
    return target, quality


def probe_public_hosts() -> dict:
    """Record public host redirects/statuses before launching browser automation."""
    urls = [
        "https://ifilo.net/",
        "https://ifilo.net/upload",
        "https://ifilo.net/login",
        "https://www.ifilo.net/",
        "https://dashboard.tablighgram.com/",
        "https://dashboard.tablighgram.com/upload",
        "https://dashboard.tablighgram.com/login",
    ]
    rows = []
    for url in urls:
        row = {"requested_host": urlparse(url).netloc, "requested_path": urlparse(url).path}
        try:
            response = requests.get(url, headers={"User-Agent": UA}, timeout=(10, 18), allow_redirects=True)
            final = urlparse(response.url)
            body = response.text[:150_000] if "text/html" in str(response.headers.get("Content-Type") or "").lower() else ""
            title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:120] if title_match else ""
            plain = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
            plain = re.sub(r"<[^>]+>", " ", plain)
            plain = re.sub(r"\s+", " ", html.unescape(plain)).strip()
            anchors = [
                {"label": re.sub(r"\s+", " ", html.unescape(tag_text)).strip()[:60],
                 "path": urlparse(href).path}
                for href, tag_text in re.findall(
                    r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                    body, flags=re.I | re.S
                )[:80]
            ]
            scripts = [
                urlparse(src).path for src in re.findall(
                    r"<script\b[^>]*src=[\"']([^\"']+)[\"']",
                    body, flags=re.I | re.S
                )[:30]
            ]
            row.update({
                "status": response.status_code,
                "final_host": final.netloc,
                "final_path": final.path,
                "redirects": [
                    {"status": item.status_code, "host": urlparse(item.url).netloc, "path": urlparse(item.url).path}
                    for item in response.history
                ],
                "content_type": str(response.headers.get("Content-Type") or "")[:100],
                "html_bytes": len(body.encode("utf-8")),
                "title": title,
                "has_upload_word": bool(re.search(r"آپلود|بارگذاری|upload", plain + " " + title, re.I)),
                "body_excerpt": plain[:250],
                "anchor_paths": anchors,
                "script_paths": scripts,
            })
            response.close()
        except Exception as exc:
            row.update({"error_type": type(exc).__name__, "error": str(exc)[:160]})
        rows.append(row)
    report = {"checked_at": utc_now(), "hosts": rows}
    print("IFILO_PUBLIC_HOST_DIAGNOSTICS " + json.dumps(report, ensure_ascii=False))
    return report


def probe_upload_rules(page) -> dict:
    """Read the public upload rules for review before accepting any upload checkbox."""
    report = {"url": BASE_URL + "/userRules", "title": "", "text": "", "status": "unknown"}
    try:
        page.goto(BASE_URL + "/userRules", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
        report["title"] = str(page.title() or "")[:160]
        # Prefer the main content region to avoid logging account sidebar data.
        text_value = ""
        for selector in ("main", "article", ".page-content", ".content-page", "#content"):
            try:
                locator = page.locator(selector)
                if locator.count() and locator.first.is_visible():
                    candidate = locator.first.inner_text(timeout=5000).strip()
                    if len(candidate) > len(text_value):
                        text_value = candidate
            except Exception:
                continue
        if not text_value:
            text_value = page.locator("body").inner_text(timeout=5000)
            lines = [line.strip() for line in text_value.splitlines() if line.strip()]
            for idx, line in enumerate(lines):
                if re.search(r"قوانین کاربر|قوانین انتشار|قوانین آپلود|شرایط استفاده|قوانین فیلو", line):
                    text_value = "\n".join(lines[idx:])
                    break
        report["text"] = re.sub(r"\s+", " ", text_value).strip()[:6000]
        report["status"] = "read"
    except Exception as exc:
        report["status"] = "read_failed"
        report["error_type"] = type(exc).__name__
    print("IFILO_UPLOAD_RULES_PROBE " + json.dumps(report, ensure_ascii=False))
    return report


def inspect_upload_client(page) -> int:
    """Read-only inspect of same-origin upload-page JavaScript and form wiring."""
    expected_host = urlparse(BASE_URL).netloc.lower()
    # Dry-run checkbox changes in the browser only. No file is selected and no submit occurs.
    flag_toggles = {
        "dont_publish": _set_checkbox(page, "#dontPublish", True, allow_hidden=True),
        "monetization_off": _set_checkbox(page, "#canMakeMoney", False, allow_hidden=True),
        "redistribution_off": _set_checkbox(page, "#canRepublish", False, allow_hidden=True),
        "rules_accepted": _set_checkbox(page, "#acceptRules", True),
    }
    try:
        angular_flags = page.evaluate("""() => {
            const root = document.querySelector('[ng-controller="ctrl"]');
            if (!root || !window.angular) return {};
            const scope = window.angular.element(root).scope();
            if (!scope) return {};
            const item = scope.item || {};
            return {
                dontPublish: !!Number(item.dontPublish),
                canMakeMoney: !!item.canMakeMoney,
                canRepublish: !!item.canRepublish,
                acceptRules: !!scope.acceptRules
            };
        }""")
    except Exception:
        angular_flags = {}
    flags_consistent = (
        flag_toggles["dont_publish"] and flag_toggles["monetization_off"]
        and flag_toggles["redistribution_off"] and flag_toggles["rules_accepted"]
        and angular_flags.get("dontPublish") is True
        and angular_flags.get("canMakeMoney") is False
        and angular_flags.get("canRepublish") is False
        and angular_flags.get("acceptRules") is True
    )
    report = {
        "page_path": urlparse(str(page.url)).path,
        "page_title": str(page.title() or "")[:120],
        "flag_dry_run": {
            "dom_toggles": flag_toggles,
            "angular_model": angular_flags,
            "consistent": bool(flags_consistent),
            "file_selected": False,
            "submit_clicked": False,
        },
        "controls": [],
        "scripts_checked": [],
    }
    allowed_ids = {
        "fileInput", "acceptRule", "unixTime", "acceptRules", "canMakeMoney",
        "canRepublish", "dontPublish", "isMadeByMe", "title",
    }
    try:
        report["controls"] = page.locator("input,textarea,select,button").evaluate_all(
            """els => els.filter(el => {
                const id = el.id || '';
                const name = el.name || '';
                const ph = el.getAttribute('placeholder') || '';
                return ['fileInput','acceptRule','unixTime','acceptRules','canMakeMoney',
                        'canRepublish','dontPublish','isMadeByMe','madeForKids','over18'].includes(id)
                    || name === 'title' || ph === 'توضیحات ویدیو'
                    || ((el.innerText || '').trim() === 'انتشار ویدیو');
            }).slice(0, 35).map(el => ({
                id: el.id || '',
                name: el.name || '',
                type: el.type || el.tagName.toLowerCase(),
                class_name: String(el.className || '').slice(0, 100),
                checked: typeof el.checked === 'boolean' ? el.checked : null,
                disabled: !!el.disabled,
                required: !!el.required,
                value_present: String(el.value || '').length > 0,
                value_length: String(el.value || '').length,
                safe_value: el.id === 'acceptRule' && ['0','1'].includes(String(el.value)) ? String(el.value) : null,
                visible: !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'),
                attributes: Array.from(el.attributes || [])
                    .filter(a => /^(?:id|name|type|class|placeholder|required|disabled|onclick|ng-|data-ng-|aria-)/i.test(a.name))
                    .slice(0, 18).map(a => ({name: a.name, value: String(a.value || '').slice(0, 160)})),
                parent_text: String((el.closest('label') || el.parentElement || {}).innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 180)
            }))"""
        )
    except Exception as exc:
        report["controls_error_type"] = type(exc).__name__

    try:
        script_urls = page.locator("script[src]").evaluate_all(
            "els => els.map(el => el.src).filter(Boolean).slice(0, 30)"
        )
    except Exception:
        script_urls = []

    terms = [
        "fileInput", "acceptRule", "acceptRules", "canMakeMoney", "canRepublish",
        "dontPublish", "isMadeByMe", "madeForKids", "over18", "unixTime",
        "انتشار ویدیو", "startUpload", "uploadComplete", "uploadStarted",
        "fileSelected", "publishing", "messageIsError", "message", "videoCode",
        "scopeUpload", "publishVideo", "saveVideo", "uploadVideo", "startUrlUpload",
        "startPublish", "checkDontPublish", "checkMadeByMe",
    ]
    seen_paths = set()
    for script_url in script_urls:
        parsed = urlparse(str(script_url))
        if parsed.netloc.lower() != expected_host or parsed.path in seen_paths:
            continue
        seen_paths.add(parsed.path)
        if len(report["scripts_checked"]) >= 18:
            break
        row = {"path": parsed.path, "status": "not_checked"}
        try:
            response = requests.get(
                str(script_url), headers={"User-Agent": UA},
                timeout=(5, 12), allow_redirects=True,
            )
            final = urlparse(response.url)
            row["status_code"] = response.status_code
            if final.netloc.lower() != expected_host:
                row["status"] = "redirected_off_canonical_host"
            elif response.status_code != 200:
                row["status"] = "http_error"
            else:
                body = response.text[:2_000_000]
                row["status"] = "read"
                row["bytes_checked"] = len(body.encode("utf-8", errors="ignore"))
                matches = []
                seen_terms = set()
                lowered = body.casefold()
                for term in terms:
                    key = term.casefold()
                    if key in seen_terms:
                        continue
                    idx = 0
                    occurrences = 0
                    while occurrences < 3:
                        idx = lowered.find(key, idx)
                        if idx < 0:
                            break
                        snippet = re.sub(r"\\s+", " ", body[max(0, idx - 190):min(len(body), idx + len(term) + 260)]).strip()
                        matches.append({"term": term, "offset": idx, "snippet": snippet[:480]})
                        occurrences += 1
                        idx += max(1, len(term))
                        if len(matches) >= 24:
                            break
                    seen_terms.add(key)
                    if len(matches) >= 24:
                        break
                if matches:
                    row["matches"] = matches
                handler_start = body.find("$scope.startPublish=function")
                if handler_start < 0:
                    handler_start = body.find("$scope.startPublish = function")
                if handler_start >= 0:
                    handler = body[handler_start:handler_start + 3600]
                    row["publish_handler_snippet"] = re.sub(r"\\s+", " ", handler).strip()[:3600]
            response.close()
        except Exception as exc:
            row["status"] = "request_failed"
            row["error_type"] = type(exc).__name__
        report["scripts_checked"].append(row)

    # This is static code and UI metadata only; no account profile/body text is emitted.
    print("IFILO_UPLOAD_CLIENT_DIAGNOSTICS " + json.dumps(report, ensure_ascii=False))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "upload_client_diagnostics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"
    )
    return 0 if report["scripts_checked"] else 3


DRAFT_TEST_TITLE = "آزمایش فنی بارگذاری فیلو — پیش‌نویس آزمایشی ۲"
DRAFT_TEST_DESCRIPTION = (
    "ویدیوی آزمایشیِ ساخته‌شده برای بررسی فنی بارگذاری. "
    "این فایل پیش‌نویس است و نباید عمومی یا درآمدزا شود."
)
DRAFT_SUCCESS_RE = re.compile(
    r"با\s*موفقیت|آپلود\s*شد|بارگذاری\s*شد|ذخیره\s*شد|ثبت\s*شد|"
    r"در\s*صف\s*(?:بررسی|پردازش)|در\s*انتظار\s*(?:بررسی|پردازش)|"
    r"successfully\s*(?:uploaded|submitted)|draft\s*(?:saved|created)",
    re.I,
)


def create_draft_test_video(destination: Path) -> Path:
    """Create an original moving test pattern locally; it contains no third-party media."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=duration=17:size=720x1280:rate=25",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size < 100_000:
        raise RuntimeError("ffmpeg_could_not_create_original_test_video")
    if destination.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("generated_test_video_exceeds_size_limit")
    return destination


def _set_checkbox(page, selector: str, checked: bool, *, allow_hidden: bool = False) -> bool:
    """Set one checkbox and verify its final state, including hidden native form fields."""
    locator = page.locator(selector)
    if locator.count() == 0:
        return False
    try:
        current = locator.first.is_checked()
        if current == checked:
            return True
        # Prefer the visible associated label when the site exposes one.
        ident = selector.lstrip("#")
        label = page.locator(f'label[for="{ident}"]')
        if label.count() and label.first.is_visible() and label.first.is_enabled():
            label.first.click(timeout=8000)
        elif locator.first.is_visible() and locator.first.is_enabled():
            locator.first.set_checked(checked, timeout=8000)
        elif allow_hidden:
            # FiLo renders the monetization/redistribution checkboxes as hidden but
            # enabled native inputs. Trigger their normal click event and verify
            # the resulting checked property before any form submission.
            changed = locator.first.evaluate(
                """(el, desired) => {
                    if (el.disabled || (el.type || '').toLowerCase() !== 'checkbox') return false;
                    if (!!el.checked !== !!desired) el.click();
                    return !!el.checked === !!desired;
                }""",
                checked,
            )
            if not changed:
                return False
        else:
            return False
        return locator.first.is_checked() == checked
    except Exception:
        return False


def upload_unpublished_test_draft(page) -> int:
    """Upload one synthetic test clip only after checking Angular safety flags and guarding the POST."""
    from urllib.parse import parse_qs
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    with tempfile.TemporaryDirectory(prefix="ifilo-draft-smoke-") as temp_dir:
        video = Path(temp_dir) / "ifilo-upload-test-draft-v2.mp4"
        try:
            create_draft_test_video(video)
        except Exception as exc:
            print("IFILO_TEST_DRAFT_VIDEO_CREATE_FAILED", type(exc).__name__)
            return 3

        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        test_item = {"id": "ifilo-test-draft-v2-" + digest, "url": ""}
        state = load_state()
        if has_prior_attempt(test_item, state):
            print("IFILO_TEST_DRAFT_ALREADY_ATTEMPTED: Refusing to create duplicate drafts.")
            return 0

        file_input = page.locator("#fileInput")
        if file_input.count() == 0:
            print("IFILO_TEST_DRAFT_BLOCKED_NO_PRIMARY_FILE_INPUT")
            return 3
        try:
            file_input.set_input_files(str(video), timeout=30000)
        except Exception as exc:
            print("IFILO_TEST_DRAFT_FILE_SELECTION_FAILED", type(exc).__name__)
            return 3

        # FiLo sends the file in chunks after file selection. A 100% progress
        # indicator is not enough: wait for the Angular model's uploadComplete flag.
        try:
            page.wait_for_function(
                """() => {
                    const root = document.querySelector('[ng-controller="ctrl"]');
                    if (!root || !window.angular) return false;
                    const scope = window.angular.element(root).scope();
                    return !!scope && (
                        !!scope.uploadComplete
                        || (!!scope.messageIsError && !!String(scope.message || '').trim())
                    );
                }""",
                timeout=120000,
                polling=1000,
            )
        except PlaywrightTimeoutError:
            pass

        try:
            upload_state = page.evaluate("""() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return {};
                const scope = window.angular.element(root).scope();
                if (!scope) return {};
                const item = scope.item || {};
                return {
                    fileSelected: !!scope.fileSelected,
                    uploadStarted: !!scope.uploadStarted,
                    uploadComplete: !!scope.uploadComplete,
                    publishing: !!scope.publishing,
                    messageIsError: !!scope.messageIsError,
                    message: String(scope.message || '').slice(0, 500),
                    videoUrlPresent: !!String(item.videoUrl || '').trim(),
                    videoCodePresent: !!String(item.videoCode || '').trim(),
                    durationPresent: Number(item.duration || 0) > 0
                };
            }""")
        except Exception:
            upload_state = {}

        if not upload_state.get("uploadComplete"):
            message = str(upload_state.pop("message", "") or "")
            if message:
                if "فرمت" in message:
                    kind = "file_format_validation"
                elif "حجم" in message:
                    kind = "file_size_validation"
                elif "network" in message.casefold() or "ارتباط" in message:
                    kind = "network_or_connection_error"
                elif "خطا" in message or "ناموفق" in message:
                    kind = "upload_error"
                else:
                    kind = "other_upload_message"
            else:
                kind = "none"
            print("IFILO_TEST_DRAFT_UPLOAD_INCOMPLETE " + json.dumps({
                **upload_state,
                "message_kind": kind,
                "retry_policy": "no_publish_post_sent",
            }, ensure_ascii=False))
            return 3

        upload_state.pop("message", None)
        print("IFILO_TEST_DRAFT_FILE_TRANSFER_COMPLETE " + json.dumps(upload_state, ensure_ascii=False))

        title_field = find_title_field(page)
        description_field = find_description_field(page)
        try:
            if title_field is not None:
                title_field.fill(DRAFT_TEST_TITLE)
            if description_field is not None:
                description_field.fill(DRAFT_TEST_DESCRIPTION)
        except Exception as exc:
            print("IFILO_TEST_DRAFT_METADATA_FILL_FAILED", type(exc).__name__)
            return 3
        title_ok = title_field is not None
        description_ok = description_field is not None

        rules_ok = _set_checkbox(page, "#acceptRules", True)
        unpublished_ok = _set_checkbox(page, "#dontPublish", True)
        monetization_off = _set_checkbox(page, "#canMakeMoney", False, allow_hidden=True)
        redistribution_off = _set_checkbox(page, "#canRepublish", False, allow_hidden=True)

        try:
            model = page.evaluate("""() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return {};
                const scope = window.angular.element(root).scope();
                if (!scope) return {};
                const item = scope.item || {};
                return {
                    titleMatches: String(item.title || '') === 'آزمایش فنی بارگذاری فیلو — پیش‌نویس آزمایشی ۲',
                    descriptionMatches: String(item.description || '') === 'ویدیوی آزمایشیِ ساخته‌شده برای بررسی فنی بارگذاری. این فایل پیش‌نویس است و نباید عمومی یا درآمدزا شود.',
                    dontPublish: !!Number(item.dontPublish),
                    canMakeMoney: !!item.canMakeMoney,
                    canRepublish: !!item.canRepublish,
                    acceptRules: !!scope.acceptRules,
                    fileSelected: !!scope.fileSelected,
                    uploadComplete: !!scope.uploadComplete,
                    uploadStarted: !!scope.uploadStarted,
                    publishing: !!scope.publishing
                };
            }""")
        except Exception:
            model = {}

        model_safe = (
            model.get("titleMatches") is True
            and model.get("descriptionMatches") is True
            and model.get("dontPublish") is True
            and model.get("canMakeMoney") is False
            and model.get("canRepublish") is False
            and model.get("acceptRules") is True
            and model.get("uploadComplete") is True
        )
        dom_safe = (
            rules_ok and unpublished_ok and monetization_off and redistribution_off
            and title_ok and description_ok
            and page.locator("#dontPublish").is_checked()
            and not page.locator("#canMakeMoney").is_checked()
            and not page.locator("#canRepublish").is_checked()
        )
        if not dom_safe or not model_safe:
            print("IFILO_TEST_DRAFT_BLOCKED_ANGULAR_SAFETY_FLAGS " + json.dumps({
                "dom_safe": bool(dom_safe),
                "model_safe": bool(model_safe),
                "model": model,
                "rules_ok": bool(rules_ok),
                "title_field_present": bool(title_ok),
                "description_field_present": bool(description_ok),
            }, ensure_ascii=False))
            return 3

        button = None
        for locator in page.get_by_role("button", name=re.compile(r"انتشار ویدیو", re.I)).all():
            try:
                if locator.is_visible() and locator.is_enabled():
                    button = locator
                    break
            except Exception:
                continue
        if button is None:
            print("IFILO_TEST_DRAFT_BLOCKED_FINAL_ACTION_NOT_READY")
            return 3

        attempt = {
            "source_key": source_key(test_item),
            "source_url": "",
            "source_title": DRAFT_TEST_TITLE,
            "title": DRAFT_TEST_TITLE,
            "status": "pending",
            "mode": "test_draft_not_public",
            "started_at": utc_now(),
            "video_sha256": digest,
            "bytes": video.stat().st_size,
            "publication_checkbox": "dontPublish=true",
            "monetization_checkbox": "canMakeMoney=false",
            "redistribution_checkbox": "canRepublish=false",
            "submission_guard": "exact-title-and-safety-flags-one-post-only",
        }
        state.setdefault("attempts", []).append(attempt)
        write_state(state)

        request_state = {
            "matching_post_count": 0,
            "continued_count": 0,
            "blocked_count": 0,
            "first_post_safety_checks": {},
        }
        expected_host = urlparse(BASE_URL).netloc.lower()
        false_values = {"", "0", "false", "off", "no", "none"}
        true_values = {"1", "true", "on", "yes"}

        def guard_publish_route(route):
            request = route.request
            parsed = urlparse(request.url)
            if request.method.upper() != "POST" or parsed.path.rstrip("/") != "/video/publish":
                route.continue_()
                return

            request_state["matching_post_count"] += 1
            post_fields = {}
            try:
                post_fields = parse_qs(request.post_data or "", keep_blank_values=True)
            except Exception:
                post_fields = {}

            def value_of(name):
                values = post_fields.get(name, [""])
                return str(values[-1] if values else "").strip()

            dont_publish_value = value_of("dontPublish").casefold()
            make_money_value = value_of("canMakeMoney").casefold()
            republish_value = value_of("canRepublish").casefold()
            checks = {
                "canonical_host": parsed.netloc.lower() == expected_host,
                "title_matches": value_of("title") == DRAFT_TEST_TITLE,
                "description_matches": value_of("description") == DRAFT_TEST_DESCRIPTION,
                "dont_publish_true": dont_publish_value in true_values,
                "monetization_false": make_money_value in false_values,
                "redistribution_false": republish_value in false_values,
                "first_request_only": request_state["matching_post_count"] == 1,
            }
            if request_state["matching_post_count"] == 1:
                request_state["first_post_safety_checks"] = checks

            if all(checks.values()) and request_state["continued_count"] == 0:
                request_state["continued_count"] += 1
                route.continue_()
            else:
                request_state["blocked_count"] += 1
                route.abort("blockedbyclient")

        response_summary = {
            "response_received": False,
            "http_status": None,
            "payload_success": None,
            "server_reason_kind": "not_available",
            "server_reason_present": False,
        }
        submit_error_type = None
        clicked = False
        try:
            # This guard allows at most one request and only when the POST contains the
            # exact synthetic test metadata with do-not-publish enabled and monetization
            # and redistribution disabled. FiLo's JS retries rejected requests; retry #2
            # is deliberately aborted here.
            page.route("**/video/publish", guard_publish_route)
            try:
                with page.expect_response(
                    lambda response: (
                        response.request.method.upper() == "POST"
                        and urlparse(response.url).netloc.lower() == expected_host
                        and urlparse(response.url).path.rstrip("/") == "/video/publish"
                    ),
                    timeout=60000,
                ) as response_info:
                    button.click(timeout=15000)
                    clicked = True

                response = response_info.value
                response_summary["response_received"] = True
                response_summary["http_status"] = int(response.status)

                payload = None
                try:
                    payload = response.json()
                except Exception:
                    try:
                        payload = json.loads(response.text())
                    except Exception:
                        payload = None

                # Some site versions wrap the JSON payload in another JSON string.
                for _ in range(3):
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                            continue
                        except Exception:
                            break
                    if isinstance(payload, dict) and "s" not in payload and "data" in payload:
                        payload = payload.get("data")
                        continue
                    break

                if isinstance(payload, dict) and "s" in payload:
                    response_summary["payload_success"] = bool(payload.get("s"))
                    reason = str(payload.get("r") or "")
                    response_summary["server_reason_present"] = bool(reason)
                    if reason:
                        lowered = reason.casefold()
                        if "عنوان" in reason:
                            kind = "title_validation"
                        elif "توضیح" in reason:
                            kind = "description_validation"
                        elif "دسته" in reason:
                            kind = "category_validation"
                        elif "فرمت" in reason or "حجم" in reason or "ویدیو" in reason:
                            kind = "file_or_video_validation"
                        elif "قانون" in reason or "پذیرش" in reason:
                            kind = "rules_validation"
                        elif "موفق" in reason or "ثبت" in reason:
                            kind = "success_or_saved_message"
                        else:
                            kind = "other_server_rejection"
                        response_summary["server_reason_kind"] = kind
                    else:
                        response_summary["server_reason_kind"] = "none"
                else:
                    response_summary["server_reason_kind"] = "unparseable_response"

            except PlaywrightTimeoutError:
                submit_error_type = "response_timeout"
            except Exception as exc:
                submit_error_type = type(exc).__name__
            finally:
                try:
                    page.unroute("**/video/publish", guard_publish_route)
                except Exception:
                    pass
        except Exception as exc:
            submit_error_type = type(exc).__name__

        # Inspect the Angular model after the response. Never log raw page text,
        # full server response, identifiers, cookies, profile data, or account balance.
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        final_model = {}
        message = ""
        try:
            final_model = page.evaluate("""() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return {};
                const scope = window.angular.element(root).scope();
                if (!scope) return {};
                const item = scope.item || {};
                return {
                    published: !!scope.published,
                    publishing: !!scope.publishing,
                    uploadComplete: !!scope.uploadComplete,
                    uploadStarted: !!scope.uploadStarted,
                    dontPublish: !!Number(item.dontPublish),
                    canMakeMoney: !!item.canMakeMoney,
                    canRepublish: !!item.canRepublish,
                    acceptRules: !!scope.acceptRules,
                    messageIsError: !!scope.messageIsError,
                    titleCleared: String(item.title || '') === '',
                    descriptionCleared: String(item.description || '') === ''
                };
            }""")
            # Read the message separately then classify it without logging the actual text.
            message = page.evaluate("""() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return '';
                const scope = window.angular.element(root).scope();
                return scope ? String(scope.message || '').slice(0, 500) : '';
            }""")
        except Exception:
            final_model = {}

        if message:
            if "عنوان" in message:
                message_kind = "title_validation"
            elif "توضیح" in message:
                message_kind = "description_validation"
            elif "دسته" in message:
                message_kind = "category_validation"
            elif "فرمت" in message or "حجم" in message:
                message_kind = "file_or_video_validation"
            elif "قانون" in message or "پذیرش" in message:
                message_kind = "rules_validation"
            elif "با موفقیت" in message or "آپلود" in message or "ثبت شد" in message:
                message_kind = "success_or_saved_message"
            else:
                message_kind = "other_message"
        else:
            message_kind = "none"

        response_summary["second_or_blocked_posts"] = max(
            0, request_state["matching_post_count"] - request_state["continued_count"]
        )
        safe_report = {
            "http_response": response_summary,
            "request_guard": request_state,
            "final_angular_state": final_model,
            "page_path": urlparse(str(page.url)).path,
            "message_kind": message_kind,
            "message_present": bool(message),
            "submit_error_type": submit_error_type,
        }

        if response_summary["payload_success"] is True and request_state["continued_count"] == 1:
            attempt["status"] = "draft_uploaded"
            attempt["finished_at"] = utc_now()
            attempt["confirmation"] = "video_publish_response_s_true_with_safe_payload"
            attempt["result_path"] = urlparse(str(page.url)).path
            write_state(state)
            print("IFILO_TEST_DRAFT_SUCCESS " + json.dumps({
                "title": DRAFT_TEST_TITLE,
                "status": "draft_uploaded",
                "public_post": False,
                "monetization_enabled": False,
                "redistribution_enabled": False,
                "bytes": attempt["bytes"],
                "result_path": attempt["result_path"],
                "verification": safe_report,
            }, ensure_ascii=False))
            return 0

        if response_summary["payload_success"] is False:
            attempt["status"] = "test_draft_rejected"
            attempt["finished_at"] = utc_now()
            attempt["result_path"] = urlparse(str(page.url)).path
            attempt["server_reason_kind"] = response_summary["server_reason_kind"]
            write_state(state)
            print("IFILO_TEST_DRAFT_REJECTED " + json.dumps(safe_report, ensure_ascii=False))
            return 5

        attempt["status"] = "test_draft_uncertain"
        attempt["finished_at"] = utc_now()
        attempt["server_reason_kind"] = response_summary["server_reason_kind"]
        write_state(state)
        print("IFILO_TEST_DRAFT_UNCERTAIN " + json.dumps({
            **safe_report,
            "retry_policy": "no_retry_after_uncertain_result",
        }, ensure_ascii=False))
        return 4


def verify_unpublished_test_draft(page) -> int:
    """Read-only check for a previously attempted unpublished test draft; never submits."""
    state = load_state()
    attempts = [
        row for row in state.get("attempts", [])
        if isinstance(row, dict)
        and str(row.get("title") or row.get("source_title") or "") == DRAFT_TEST_TITLE
        and str(row.get("status") or "") in {
            "draft_uploaded", "test_draft_uncertain", "test_draft_unexpected_public_route",
            "pending", "submit_failed_before_click"
        }
    ]
    if not attempts:
        print("IFILO_TEST_DRAFT_VERIFY_NO_PRIOR_ATTEMPT")
        return 3

    start_url = BASE_URL + "/my"
    targets: list[str] = [start_url, BASE_URL + "/myVideos"]
    visited: set[str] = set()
    reports: list[dict] = []
    expected_host = urlparse(BASE_URL).netloc.lower()

    while targets and len(visited) < 8:
        target = targets.pop(0)
        parsed_target = urlparse(target)
        if parsed_target.netloc.lower() != expected_host:
            continue
        key = parsed_target.path
        if key in visited:
            continue
        visited.add(key)
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1000)
        except Exception as exc:
            reports.append({"requested_path": key, "navigation_error": type(exc).__name__})
            continue

        current = urlparse(str(page.url))
        if current.netloc.lower() != expected_host:
            reports.append({"requested_path": key, "result": "redirected_off_canonical_host"})
            continue
        if re.search(r"/login(?:/|$)", current.path, re.I):
            reports.append({"requested_path": key, "result": "login_required"})
            continue

        try:
            body = page.locator("body").inner_text(timeout=8000)
        except Exception:
            body = ""
        found = DRAFT_TEST_TITLE.casefold() in body.casefold()
        reports.append({
            "requested_path": key,
            "result_path": current.path,
            "expected_title_found": found,
        })
        if found:
            print("IFILO_TEST_DRAFT_VERIFIED " + json.dumps({
                "title": DRAFT_TEST_TITLE,
                "found_on_path": current.path,
                "public_post": False,
                "monetization_enabled": False,
                "verification": "title_found_in_authenticated_account_page_after_do_not_publish_submission",
            }, ensure_ascii=False))
            return 0

        # Follow only account-page links whose visible labels point to uploaded videos;
        # never log arbitrary link text, account details, or page body content.
        try:
            links = page.locator("a[href]").evaluate_all(
                """els => els.map(a => ({
                    href: a.href,
                    path: (() => { try { return new URL(a.href, location.href).pathname; } catch { return ''; } })(),
                    text: (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim()
                })).filter(a => /ویدیوهای من|مدیریت ویدیو|ویدیوهای آپلودشده|my videos|uploaded videos/i.test(a.text))
                  .map(a => ({href: a.href, path: a.path}))
                  .slice(0, 10)"""
            )
            for link in links:
                if link.get("href") and link.get("path"):
                    parsed_link = urlparse(str(link["href"]))
                    if parsed_link.netloc.lower() == expected_host and link["path"] not in visited:
                        targets.append(str(link["href"]))
        except Exception:
            pass

    print("IFILO_TEST_DRAFT_NOT_FOUND_ON_ACCOUNT_PAGES " + json.dumps({
        "title": DRAFT_TEST_TITLE,
        "checked_paths": reports,
        "conclusion": "Not found in checked account pages; this does not prove the upload failed, and no retry was made.",
    }, ensure_ascii=False))
    return 4


def run_browser(publish: bool, test_draft: bool = False, verify_test_draft: bool = False, inspect_client: bool = False) -> int:
    # A safe, no-auth network check reveals redirects/domain status; it never posts data.
    probe_public_hosts()
    # Import only when needed so the helper tests can run without browser binaries.
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print("IFILO_PLAYWRIGHT_IMPORT_ERROR", type(exc).__name__)
        return 2

    with tempfile.TemporaryDirectory(prefix="ifilo-auth-") as tmp:
        state_file = Path(tmp) / "storage-state.json"
        storage_state = None
        try:
            storage_state = load_storage_state(state_file)
        except Exception as exc:
            print("IFILO_STORAGE_STATE_INVALID", type(exc).__name__)
            return 2

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_args = {"user_agent": UA, "accept_downloads": False}
            if storage_state is not None:
                context_args["storage_state"] = str(state_file)
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.set_default_timeout(20000)

            try:
                if not ensure_authenticated(page, context):
                    return 2
                summary = probe_upload_form(page)
                if verify_test_draft:
                    return verify_unpublished_test_draft(page)
                if inspect_client:
                    return inspect_upload_client(page)
                if test_draft:
                    return upload_unpublished_test_draft(page)
                if not publish:
                    probe_upload_rules(page)
                    # Return to the authenticated upload form after inspecting rules.
                    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(800)
                    if summary.get("file_input_count", 0) > 0:
                        print("IFILO_PROBE_OK: Authenticated upload page exposes a file input. No video was uploaded.")
                        return 0
                    print("IFILO_PROBE_INCOMPLETE: Authenticated page did not expose a file input. No video was uploaded.")
                    return 3

                candidates = _eligible_candidates()
                if not candidates:
                    print("IFILO_NO_ELIGIBLE_NEW_CANDIDATE: No fresh, qualified, unattempted native Telegram video is ready.")
                    return 0

                candidate = None
                video_path = None
                quality = {}
                with tempfile.TemporaryDirectory(prefix="ifilo-media-") as media_tmp:
                    for row in candidates:
                        path, qc = prepare_candidate_video(row, Path(media_tmp))
                        if path is not None:
                            candidate, video_path, quality = row, path, qc
                            break
                    if candidate is None or video_path is None:
                        print("IFILO_NO_CANDIDATE_PASSED_MEDIA_QC")
                        return 0

                    title = make_title(candidate)
                    description = make_description(candidate, title)
                    # The upload form may reveal metadata fields only after a file is chosen.
                    filled_title, filled_desc = fill_metadata(page, title, description)
                    file_input = page.locator('input[type="file"]').first
                    if file_input.count() == 0:
                        print("IFILO_UPLOAD_BLOCKED_NO_FILE_INPUT")
                        return 3
                    try:
                        file_input.set_input_files(str(video_path), timeout=30000)
                        page.wait_for_timeout(1800)
                    except Exception as exc:
                        print("IFILO_FILE_SELECT_FAILED", type(exc).__name__)
                        return 3

                    title_after, desc_after = fill_metadata(page, title, description)
                    filled_title = filled_title or title_after
                    filled_desc = filled_desc or desc_after
                    probe_upload_form(page)
                    if not filled_title or not filled_desc:
                        print("IFILO_UPLOAD_BLOCKED_METADATA_FIELDS_NOT_IDENTIFIED " + json.dumps({
                            "title_field_found": filled_title,
                            "description_field_found": filled_desc,
                        }, ensure_ascii=False))
                        return 3

                    submit = choose_submit_button(page)
                    if submit is None:
                        print("IFILO_UPLOAD_BLOCKED_SUBMIT_BUTTON_NOT_IDENTIFIED")
                        return 3

                    state = load_state()
                    if has_prior_attempt(candidate, state):
                        print("IFILO_DUPLICATE_GUARD: Candidate was attempted by another run.")
                        return 0
                    attempt = {
                        "source_key": source_key(candidate),
                        "source_url": str(candidate.get("url") or ""),
                        "source_title": str(candidate.get("title") or "")[:300],
                        "title": title,
                        "status": "pending",
                        "started_at": utc_now(),
                        "video_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
                        "bytes": video_path.stat().st_size,
                        "quality": quality,
                    }
                    state.setdefault("attempts", []).append(attempt)
                    write_state(state)

                    prior_url = page.url
                    clicked = False
                    try:
                        submit.click(timeout=180000)
                        clicked = True
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=30000)
                        except Exception:
                            pass
                        page.wait_for_timeout(7000)
                    except Exception as exc:
                        attempt["status"] = "uncertain_after_submit" if clicked else "submit_failed_before_click"
                        attempt["finished_at"] = utc_now()
                        attempt["error_type"] = type(exc).__name__
                        write_state(state)
                        print("IFILO_SUBMIT_ERROR " + json.dumps({
                            "clicked": clicked,
                            "status": attempt["status"],
                            "error_type": type(exc).__name__,
                        }, ensure_ascii=False))
                        return 4

                    body_text = ""
                    try:
                        body_text = page.locator("body").inner_text(timeout=10000)
                    except Exception:
                        pass
                    success_text = bool(SUCCESS_TEXT_RE.search(body_text))
                    result_url = _upload_url_from_page(page, prior_url)
                    if success_text or result_url:
                        attempt["status"] = "uploaded"
                        attempt["finished_at"] = utc_now()
                        attempt["ifilo_url"] = result_url
                        attempt["confirmation"] = "success_text" if success_text else "redirect_or_video_link"
                        write_state(state)
                        print("IFILO_UPLOAD_SUCCESS " + json.dumps({
                            "title": title,
                            "ifilo_url": result_url,
                            "source_url": attempt["source_url"],
                            "bytes": attempt["bytes"],
                            "quality": quality,
                            "confirmation": attempt["confirmation"],
                        }, ensure_ascii=False))
                        return 0

                    # Never auto-retry an ambiguous submit: the server may have accepted the file.
                    attempt["status"] = "uncertain_after_submit"
                    attempt["finished_at"] = utc_now()
                    write_state(state)
                    print("IFILO_UPLOAD_UNCERTAIN: Submission was attempted, but success could not be verified; this source will not be retried automatically.")
                    print("IFILO_POST_SUBMIT_PROBE " + json.dumps(_visible_control_summary(page), ensure_ascii=False))
                    return 4
            finally:
                try:
                    context.close()
                finally:
                    browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Upload one top-ranked eligible video only when rights evidence is present.",
    )
    parser.add_argument(
        "--test-draft",
        action="store_true",
        help="Upload one original generated test clip as an unpublished, non-monetized draft.",
    )
    parser.add_argument(
        "--verify-test-draft",
        action="store_true",
        help="Read-only check for a prior unpublished test-draft attempt; never submits a video.",
    )
    parser.add_argument(
        "--inspect-upload-client",
        action="store_true",
        help="Read-only inspect of same-origin upload JavaScript and form controls; never submits a video.",
    )
    args = parser.parse_args()
    if sum(bool(x) for x in (args.publish, args.test_draft, args.verify_test_draft, args.inspect_upload_client)) > 1:
        parser.error("--publish, --test-draft, --verify-test-draft, and --inspect-upload-client are mutually exclusive")
    mode = "inspect_upload_client_read_only" if args.inspect_upload_client else (
        "verify_test_draft_read_only" if args.verify_test_draft else (
            "test_draft_not_public" if args.test_draft else ("publish_one" if args.publish else "probe_only")
        )
    )
    print(json.dumps({
        "IFILO_RUN": {
            "mode": mode,
            "at": utc_now(),
            "upload_url": UPLOAD_URL,
            "state_file": str(STATE_PATH.relative_to(ROOT)),
        }
    }, ensure_ascii=False))
    try:
        return run_browser(publish=args.publish, test_draft=args.test_draft, verify_test_draft=args.verify_test_draft, inspect_client=args.inspect_upload_client)
    except Exception as exc:
        # Avoid exception messages from browser/network layers because they can
        # include full request URLs, query tokens, or personal account details.
        print("IFILO_RUN_FAILED " + json.dumps({"exception_type": type(exc).__name__}, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
