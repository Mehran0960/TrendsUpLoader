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
        download_with_ytdlp,
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
        download_with_ytdlp,
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
NASA_MEDIA_POLICY_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
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


def safe_provider_message_excerpt(value: object, limit: int = 180) -> str:
    """Keep a short generic site error for debugging while redacting private fields."""
    text = str(value or "").replace("\\n", " ").replace("\\r", " ")
    text = re.sub(
        r"(?i)(token|cookie|password|authorization|secret|authv1)\s*[=:]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"https?://[^\s\"']+", "[url]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"\b\d{5,}\b", "[number]", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return " ".join(text.split())[:limit]


def make_title(item: dict) -> str:
    """Produce a concise descriptive title, not a verbatim copied headline."""
    if str(item.get("source") or "").lower() == "nasa_public_domain_video":
        localized = normalized_text(item.get("title_fa") or "", limit=70)
        original = normalized_text(item.get("title") or "", limit=150)
        if localized:
            # Keep some source-title text for distinction so unrelated clips don't
            # collide in FiLo's account-side duplicate guard.
            if original and original.casefold() not in localized.casefold():
                return (localized + " | " + original)[:100].rstrip(" |")
            return localized[:100].rstrip()

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
    if str(item.get("source") or "").lower() == "nasa_public_domain_video":
        localized_summary = normalized_text(item.get("title_fa") or "", limit=220)
        if localized_summary and localized_summary.casefold() not in normalized_text(body, limit=1400).casefold():
            body = localized_summary + "\n\n" + body
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
    if str(item.get("source") or "").lower() == "nasa_public_domain_video":
        source_page = str(item.get("url") or "").strip()
        evidence_url = str(item.get("rights_evidence_url") or item.get("license_url") or "").strip()
        body += "\n\nمنبع ویدئو: NASA"
        if source_page:
            body += "\nصفحهٔ منبع: " + source_page
        if evidence_url:
            body += "\nراهنمای استفادهٔ رسانه‌ای: " + evidence_url
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
    if not key:
        return False
    now = datetime.now(timezone.utc)
    for row in state.get("attempts", []):
        if not isinstance(row, dict):
            continue
        matches = (
            str(row.get("source_key") or "") == key
            or (url and str(row.get("source_url") or "") == url)
        )
        if not matches:
            continue
        # A temporary direct-download failure may be retried after six hours.
        # Accepted/uncertain submissions and deterministic media rejections never auto-retry.
        if str(row.get("status") or "") == "download_failed":
            stamp = str(row.get("finished_at") or row.get("started_at") or "")
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if (now - parsed.astimezone(timezone.utc)).total_seconds() >= 6 * 3600:
                    continue
            except (TypeError, ValueError):
                pass
        return True
    return False


def record_queue_media_failure(item: dict, reason: str) -> None:
    """Avoid re-downloading files that deterministically fail FiLo's media gates."""
    if str(item.get("source") or "").lower() != "nasa_public_domain_video":
        return
    state = load_state()
    if has_prior_attempt(item, state):
        return
    retryable = reason == "nasa_download_failed"
    attempt = {
        "source_key": source_key(item),
        "source_url": str(item.get("url") or ""),
        "source_title": str(item.get("title") or "")[:300],
        "status": "download_failed" if retryable else "media_rejected",
        "reason_code": str(reason or "media_gate_rejected")[:80],
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "mode": "nasa_rights_screened_queue",
    }
    state.setdefault("attempts", []).append(attempt)
    write_state(state)


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
    """Public reposts need a source-specific rights basis; popularity is never permission."""
    item = item if isinstance(item, dict) else {}
    evidence = str(item.get("rights_evidence_url") or item.get("license_url") or "").strip()
    if item.get("rights_cleared_for_publication") is not True or not evidence:
        return False
    if str(item.get("source") or "").lower() == "nasa_public_domain_video":
        screen = item.get("rights_screen") if isinstance(item.get("rights_screen"), dict) else {}
        return (
            evidence == NASA_MEDIA_POLICY_URL
            and str(item.get("rights_evidence_url") or "").strip() == NASA_MEDIA_POLICY_URL
            and str(item.get("license_url") or "").strip() == NASA_MEDIA_POLICY_URL
            and str(screen.get("basis") or "") == "metadata_passed_conservative_nasa_screen"
            and str(screen.get("center") or "").strip().upper() in NASA_CENTER_VALUES
            and str(screen.get("secondary_creator") or "").strip().casefold() in {
                "", "nasa", "national aeronautics and space administration"
            }
            and screen.get("third_party_warning_found") is False
            and screen.get("person_or_audio_signal_found") is False
        )
    return True


def _eligible_candidates() -> list[dict]:
    payload = load_json(DISCOVERY_INPUT, {"items": []})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []

    # Telegram candidates keep the existing viral-evidence review selector.
    native_telegram_items = [
        row for row in items
        if isinstance(row, dict)
        and str(row.get("platform") or "").lower() == "telegram"
        and str(row.get("source") or "").lower() == "telegram_native_video"
        and rights_cleared_for_ifilo(row)
    ]
    telegram_candidates = select_media_candidates(
        native_telegram_items,
        reviewed_ids=set(),
        limit=MAX_CANDIDATES_TO_TRY,
    )

    # NASA candidates use a separate conservative metadata/license screen and
    # direct official MP4 URLs. They do not pass through Telegram's viral-evidence
    # selector because the NASA API does not provide comparable view/velocity data.
    nasa_candidates = []
    for row in items:
        if not isinstance(row, dict):
            continue
        if str(row.get("platform") or "").lower() != "nasa":
            continue
        if str(row.get("source") or "").lower() != "nasa_public_domain_video":
            continue
        if not rights_cleared_for_ifilo(row):
            continue
        if not _normalize_queue_candidate_url(str(row.get("url") or ""), row):
            continue
        media = urlparse(str(row.get("direct_media_url") or "").strip())
        if (
            media.scheme != "https"
            or media.hostname != "images-assets.nasa.gov"
            or not media.path.lower().endswith(".mp4")
            or media.username
            or media.password
        ):
            continue
        try:
            score = float(row.get("follow_growth_score") or 0)
        except (TypeError, ValueError):
            continue
        if score < 60 or len(set(row.get("hook_signals") or [])) < 2:
            continue
        row.setdefault("cleaned_review_caption", str(row.get("title") or ""))
        nasa_candidates.append(row)

    nasa_candidates.sort(
        key=lambda row: (
            str(row.get("discovered_at") or ""),
            float(row.get("follow_growth_score") or 0),
        ),
        reverse=True,
    )
    state = load_state()
    combined = [row for row in telegram_candidates if not has_prior_attempt(row, state)]
    combined.extend(row for row in nasa_candidates if not has_prior_attempt(row, state))
    combined.sort(
        key=lambda row: (
            float(row.get("momentum_priority_score") or 0),
            float(row.get("follow_growth_score") or 0),
            str(row.get("discovered_at") or ""),
        ),
        reverse=True,
    )
    return combined[:MAX_CANDIDATES_TO_TRY]


def prepare_candidate_video(item: dict, workdir: Path) -> tuple[Path | None, dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    url = str(item.get("url") or "").strip()
    if not url:
        return None, {"passed": False, "reason": "missing_source_url"}
    target = workdir / "selected-video.mp4"

    if str(item.get("source") or "").lower() == "nasa_public_domain_video":
        media_url = str(item.get("direct_media_url") or "").strip()
        parsed_media = urlparse(media_url)
        if (
            parsed_media.scheme != "https"
            or parsed_media.hostname != "images-assets.nasa.gov"
            or not parsed_media.path.lower().endswith(".mp4")
            or parsed_media.username
            or parsed_media.password
        ):
            return None, {"passed": False, "reason": "nasa_media_url_not_allowlisted"}
        try:
            with requests.get(
                media_url,
                headers={"User-Agent": UA},
                stream=True,
                timeout=(15, 90),
            ) as response:
                final_url = urlparse(str(response.url or ""))
                if final_url.scheme != "https" or final_url.hostname != "images-assets.nasa.gov":
                    return None, {"passed": False, "reason": "nasa_media_redirect_not_allowlisted"}
                response.raise_for_status()
                try:
                    declared_size = int(response.headers.get("content-length") or 0)
                except (TypeError, ValueError):
                    declared_size = 0
                if declared_size > MAX_VIDEO_BYTES:
                    return None, {"passed": False, "reason": "nasa_video_over_size_limit"}
                written = 0
                with target.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > MAX_VIDEO_BYTES:
                            output.close()
                            target.unlink(missing_ok=True)
                            return None, {"passed": False, "reason": "nasa_video_over_size_limit"}
                        output.write(chunk)
                if written <= 0:
                    target.unlink(missing_ok=True)
                    return None, {"passed": False, "reason": "nasa_video_empty_download"}
        except requests.RequestException as exc:
            target.unlink(missing_ok=True)
            return None, {"passed": False, "reason": "nasa_download_failed", "error_type": type(exc).__name__}
    else:
        # Telegram adapter may use only the exact source post selected from the queue.
        downloaded_directly = download_direct_telegram_post(url, target, item=item)
        if not downloaded_directly:
            target.unlink(missing_ok=True)
            try:
                fallback_path = download_with_ytdlp(url, workdir)
            except Exception as exc:
                print("IFILO_TELEGRAM_YTDLP_FALLBACK_FAILED", type(exc).__name__)
                fallback_path = None
            if fallback_path is None or not Path(fallback_path).is_file():
                return None, {"passed": False, "reason": "source_video_download_failed"}
            fallback_path = Path(fallback_path)
            if fallback_path.resolve() != target.resolve():
                fallback_path.replace(target)
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
    """Read-only inspect of FiLo lazy-loaded Vue upload chunks and form wiring."""
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
        # Modern FiLo lazy-loads the upload page as a route chunk. Such chunks are
        # not necessarily represented by a persistent <script src> element.
        dynamic_script_urls = page.evaluate("""() => {
            const origin = location.origin;
            return performance.getEntriesByType('resource')
                .map(entry => String(entry.name || ''))
                .filter(raw => {
                    try {
                        const url = new URL(raw, location.href);
                        return url.origin === origin && /\\.m?js$/i.test(url.pathname);
                    } catch { return false; }
                });
        }""")
        script_urls = list(dict.fromkeys((script_urls or []) + (dynamic_script_urls or [])))[:40]
    except Exception:
        script_urls = []

    terms = [
        "fileInput", "acceptRule", "acceptRules", "canMakeMoney", "canRepublish",
        "dontPublish", "isMadeByMe", "madeForKids", "over18", "unixTime",
        "انتشار ویدیو", "startUpload", "uploadComplete", "uploadStarted",
        "fileSelected", "publishing", "messageIsError", "message", "videoCode",
        "scopeUpload", "publishVideo", "saveVideo", "uploadVideo", "startUrlUpload",
        "startPublish", "checkDontPublish", "checkMadeByMe",
        "createUploader", "uploadChunk", "chunkSize", "uploadProgress",
        "finishUpload", "completeUpload", "FormData",
        "multipart/form-data", "presigned", "signedUrl", "video/upload",
        "/api/v1/", "axios.post", "fetch(", "uploadFile", "fileUpload",
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
                # Report compact source context around upload lifecycle methods in the
                # lazy route bundles, not only legacy Angular handlers.
                lifecycle_terms = (
                    "DorfakUploader", "upload_domain", "file_domain", "serverId",
                    "onBeforeSend", "onStart", "onUploadProgress", "onEnd", "onError",
                    "startUpload", "uploadChunk", "createUploader", "finishUpload",
                    "completeUpload", "publishVideo", "videoCode", "uploadProgress",
                    "fileHeader", "acceptRule", "acceptRules", "dontPublish",
                    "canMakeMoney", "canRepublish", "presigned", "signedUrl",
                )
                contexts = []
                seen_contexts = set()
                for term in lifecycle_terms:
                    idx = 0
                    occurrences = 0
                    while occurrences < 2:
                        idx = body.find(term, idx)
                        if idx < 0:
                            break
                        start = max(0, idx - 700)
                        end = min(len(body), idx + len(term) + 1300)
                        sample = re.sub(r"\\s+", " ", body[start:end]).strip()
                        if sample not in seen_contexts:
                            seen_contexts.add(sample)
                            contexts.append({"term": term, "offset": idx, "snippet": sample[:2000]})
                        occurrences += 1
                        idx += len(term)
                        if len(contexts) >= 36:
                            break
                    if len(contexts) >= 36:
                        break
                if contexts:
                    row["upload_lifecycle_contexts"] = contexts

                # Extract only same-origin path strings and request-call context;
                # this reveals the public upload protocol without reading auth data.
                endpoint_paths = []
                for match in re.finditer(r'["\\x60](/(?:api|upload|video|file)[^"\\x60\\s]{1,160})["\\x60]', body, re.I):
                    candidate_path = match.group(1).split("?", 1)[0]
                    if candidate_path not in endpoint_paths:
                        endpoint_paths.append(candidate_path)
                    if len(endpoint_paths) >= 60:
                        break
                if endpoint_paths:
                    row["endpoint_path_candidates"] = endpoint_paths
                request_contexts = []
                for term in ("fetch(", ".post(", ".put(", ".get(", "uploadChunk", "DorfakUploader"):
                    idx = 0
                    found = 0
                    while found < 3:
                        idx = body.find(term, idx)
                        if idx < 0:
                            break
                        sample = re.sub(r"\\s+", " ", body[max(0, idx - 450):min(len(body), idx + len(term) + 900)]).strip()
                        request_contexts.append({"term": term, "snippet": sample[:1400]})
                        found += 1
                        idx += len(term)
                        if len(request_contexts) >= 20:
                            break
                    if len(request_contexts) >= 20:
                        break
                if request_contexts:
                    row["request_contexts"] = request_contexts
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



def _normalize_review_telegram_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    host = (parsed.netloc or "").lower().removeprefix("www.")
    match = re.fullmatch(r"/([A-Za-z0-9_]+)/([0-9]+)/?", parsed.path or "")
    if host not in {"t.me", "telegram.me"} or not match:
        return ""
    channel, post_id = match.groups()
    return f"https://t.me/{channel}/{post_id}"


def _normalize_queue_candidate_url(raw_url: str, item: dict) -> str:
    """Validate a queue source URL against its specific source adapter."""
    source = str((item or {}).get("source") or "").strip().lower()
    if source == "nasa_public_domain_video":
        parsed = urlparse(str(raw_url or "").strip())
        host = (parsed.hostname or "").lower()
        match = re.fullmatch(r"/details/([A-Za-z0-9_-]{2,100})/?", parsed.path or "")
        if (
            parsed.scheme != "https"
            or host != "images.nasa.gov"
            or not match
            or parsed.username
            or parsed.password
        ):
            return ""
        return "https://images.nasa.gov/details/" + match.group(1)
    return _normalize_review_telegram_url(raw_url)


def _load_previously_sent_review_item(source_url: str) -> dict | None:
    """Return one exact Telegram review item only when its URL is in the sent state."""
    normalized = _normalize_review_telegram_url(source_url)
    if not normalized:
        return None

    review_state_path = ROOT / "attention_state" / "social_discovery_notification_state.json"
    review_state = load_json(review_state_path, {})
    sent_urls = {
        _normalize_review_telegram_url(value)
        for value in review_state.get("media_reviewed_urls", [])
        if _normalize_review_telegram_url(value)
    }
    sent_fingerprints = {
        _normalize_review_telegram_url(row.get("url"))
        for row in review_state.get("media_reviewed_fingerprints", [])
        if isinstance(row, dict) and _normalize_review_telegram_url(row.get("url"))
    }
    if normalized not in sent_urls and normalized not in sent_fingerprints:
        return None

    payload = load_json(DISCOVERY_INPUT, {"items": []})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        if (
            _normalize_review_telegram_url(row.get("url")) == normalized
            and str(row.get("platform") or "").lower() == "telegram"
            and str(row.get("source") or "").lower() == "telegram_native_video"
        ):
            item = dict(row)
            item["url"] = normalized
            item["platform"] = "telegram"
            item["source"] = "telegram_native_video"
            return item
    return None


def _account_page_contains_title(page, expected_title: str) -> tuple[bool, list[dict]]:
    """Read-only account-page verification. Never logs page text or private account details."""
    targets = [BASE_URL + "/my", BASE_URL + "/myVideos", BASE_URL + "/my/videos"]
    expected_host = urlparse(BASE_URL).netloc.lower()
    reports: list[dict] = []
    visited: set[str] = set()
    while targets and len(visited) < 6:
        target = targets.pop(0)
        parsed_target = urlparse(target)
        if parsed_target.netloc.lower() != expected_host or parsed_target.path in visited:
            continue
        visited.add(parsed_target.path)
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(900)
            current = urlparse(str(page.url))
            if current.netloc.lower() != expected_host:
                reports.append({"requested_path": parsed_target.path, "result": "redirected_off_host"})
                continue
            if re.search(r"/login(?:/|$)", current.path, re.I):
                reports.append({"requested_path": parsed_target.path, "result": "login_required"})
                continue
            body = page.locator("body").inner_text(timeout=8000)
            found = expected_title.casefold() in body.casefold()
            reports.append({
                "requested_path": parsed_target.path,
                "result_path": current.path,
                "title_found": found,
            })
            if found:
                return True, reports
            links = page.locator("a[href]").evaluate_all(
                """els => els.map(a => ({
                    href: a.href,
                    path: (() => { try { return new URL(a.href, location.href).pathname; } catch { return ''; } })(),
                    text: (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim()
                })).filter(a => /ویدیوهای من|مدیریت ویدیو|ویدیوهای آپلودشده|my videos|uploaded videos/i.test(a.text))
                  .map(a => ({href: a.href, path: a.path}))
                  .slice(0, 8)"""
            )
            for link in links:
                parsed_link = urlparse(str(link.get("href") or ""))
                path = str(link.get("path") or "")
                if parsed_link.netloc.lower() == expected_host and path and path not in visited:
                    targets.append(str(link["href"]))
        except Exception as exc:
            reports.append({"requested_path": parsed_target.path, "navigation_error": type(exc).__name__})
    return False, reports


def create_video_thumbnail(video_path: Path, destination: Path) -> Path | None:
    """Extract a representative JPEG frame for FiLo's required custom thumbnail."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "1", "-i", str(video_path),
                "-frames:v", "1",
                "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
                "-q:v", "3", str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except Exception as exc:
        print("IFILO_THUMBNAIL_FRAME_EXTRACTION_FAILED", type(exc).__name__)
        return None
    if (
        result.returncode != 0
        or not destination.is_file()
        or destination.stat().st_size < 1000
        or destination.stat().st_size > 8 * 1024 * 1024
    ):
        print("IFILO_THUMBNAIL_FRAME_EXTRACTION_FAILED " + json.dumps({
            "return_code": int(result.returncode),
            "file_present": destination.is_file(),
            "file_bytes": destination.stat().st_size if destination.is_file() else 0,
        }, ensure_ascii=False))
        destination.unlink(missing_ok=True)
        return None
    return destination


def upload_video_thumbnail(page, video_path: Path, workdir: Path, mode_tag: str) -> dict:
    """Upload a representative frame through FiLo's own thumbnail uploader and verify Angular state."""
    thumbnail_path = workdir / "ifilo-video-thumbnail.jpg"
    if create_video_thumbnail(video_path, thumbnail_path) is None:
        return {"uploaded": False, "reason": "thumbnail_frame_extraction_failed"}

    custom_thumb_radio = page.locator("#cbCustomThumb")
    thumb_file_input = page.locator("#dut")
    if custom_thumb_radio.count() == 0 or thumb_file_input.count() == 0:
        print(f"{mode_tag}_THUMBNAIL_CONTROLS_NOT_FOUND")
        return {"uploaded": False, "reason": "thumbnail_controls_not_found"}

    stage = "select_custom_thumbnail"
    try:
        # FiLo only sends the custom thumbnail upload when this radio option is selected.
        try:
            custom_thumb_radio.check(force=True, timeout=10000)
        except Exception:
            # Some FiLo builds wire the hidden radio through its visible label only.
            page.locator("label[for='cbCustomThumb']").click(force=True, timeout=10000)
        stage = "attach_thumbnail_file"
        thumb_file_input.set_input_files(str(thumbnail_path), timeout=30000)
        stage = "wait_for_thumbnail_commit"
        page.wait_for_function(
            """() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return false;
                const scope = window.angular.element(root).scope();
                if (!scope) return false;
                const item = scope.item || {};
                return (
                    (Number(item.customThumb) === 1 &&
                     !!String(item.selectedFrame || item.image || '').trim())
                    || (!!scope.messageIsError && !!String(scope.message || '').trim())
                );
            }""",
            timeout=90000,
            polling=1000,
        )
    except Exception as exc:
        print(f"{mode_tag}_THUMBNAIL_UPLOAD_WAIT_FAILED " + json.dumps({
            "stage": stage,
            "error_type": type(exc).__name__,
            "error": safe_provider_message_excerpt(str(exc), limit=200),
        }, ensure_ascii=False))
        return {"uploaded": False, "reason": "thumbnail_upload_wait_failed", "stage": stage}

    try:
        status = page.evaluate("""() => {
            const root = document.querySelector('[ng-controller="ctrl"]');
            if (!root || !window.angular) return {};
            const scope = window.angular.element(root).scope();
            if (!scope) return {};
            const item = scope.item || {};
            return {
                customThumb: Number(item.customThumb || 0),
                selectedFramePresent: !!String(item.selectedFrame || '').trim(),
                imagePresent: !!String(item.image || '').trim(),
                messageIsError: !!scope.messageIsError,
                messagePresent: !!String(scope.message || '').trim()
            };
        }""")
    except Exception:
        status = {}

    uploaded = (
        status.get("customThumb") == 1
        and (status.get("selectedFramePresent") is True or status.get("imagePresent") is True)
        and status.get("messageIsError") is not True
    )
    safe_status = {
        "custom_thumb_selected": status.get("customThumb") == 1,
        "selected_frame_present": bool(status.get("selectedFramePresent")),
        "image_present": bool(status.get("imagePresent")),
        "provider_error_present": bool(status.get("messageIsError")),
        "provider_message_present": bool(status.get("messagePresent")),
        "thumbnail_bytes": thumbnail_path.stat().st_size if thumbnail_path.is_file() else 0,
    }
    print(f"{mode_tag}_THUMBNAIL_UPLOAD_" + ("OK " if uploaded else "FAILED ") + json.dumps(safe_status))
    return {"uploaded": uploaded, **safe_status}


def upload_review_video_from_telegram(
    page,
    source_url: str,
    *,
    public: bool = False,
    candidate: dict | None = None,
    prepared_video: Path | None = None,
    prepared_quality: dict | None = None,
) -> int:
    """Use the full checked uploader for a review item or a rights-cleared queue candidate."""
    mode_tag = (
        "IFILO_QUEUE_PUBLIC" if public else "IFILO_QUEUE_DRAFT"
    ) if candidate is not None else (
        "IFILO_REVIEW_PUBLIC" if public else "IFILO_REVIEW_DRAFT"
    )
    from urllib.parse import parse_qs

    item = candidate if isinstance(candidate, dict) else None
    normalized_url = (
        _normalize_queue_candidate_url(source_url, item)
        if item is not None
        else _normalize_review_telegram_url(source_url)
    )
    if not normalized_url:
        print(f"{mode_tag}_BLOCKED_INVALID_SOURCE_URL")
        return 3

    if candidate is not None:
        candidate_url = _normalize_queue_candidate_url(str(item.get("url") or ""), item) if item else ""
        if item is None or candidate_url != normalized_url:
            print(f"{mode_tag}_BLOCKED_CANDIDATE_URL_MISMATCH")
            return 3
        if public and not rights_cleared_for_ifilo(item):
            print(f"{mode_tag}_BLOCKED_RIGHTS_EVIDENCE_MISSING")
            return 3
    else:
        item = _load_previously_sent_review_item(normalized_url)
        if item is None:
            print(f"{mode_tag}_BLOCKED_NOT_FOUND_IN_SENT_REVIEW_STATE")
            return 3

    state = load_state()
    matching_attempts = [
        row for row in state.get("attempts", [])
        if isinstance(row, dict) and (
            str(row.get("source_key") or "") == source_key(item)
            or str(row.get("source_url") or "") == normalized_url
        )
    ]
    prior = matching_attempts[-1] if matching_attempts else {}
    # A prior rejected draft may be retried publicly only after checking the account
    # for an existing matching upload. Ambiguous/accepted attempts remain non-retryable.
    prior_guard = prior.get("request_guard") if isinstance(prior.get("request_guard"), dict) else {}
    try:
        prior_continued_count = int(prior_guard.get("continued_count", -1))
        prior_blocked_count = int(prior_guard.get("blocked_count", 0))
    except (TypeError, ValueError, OverflowError):
        prior_continued_count, prior_blocked_count = -1, 0
    known_public_post_was_not_sent = (
        str(prior.get("status") or "") == "review_public_uncertain"
        and prior_continued_count == 0
        and prior_blocked_count > 0
        and not str(prior.get("ifilo_url") or "").strip()
    )
    recoverable_public_retry = public and (
        str(prior.get("status") or "") in {
            "review_draft_rejected", "review_public_rejected", "submit_failed_before_click"
        }
        or known_public_post_was_not_sent
        or (
            str(prior.get("status") or "") == "review_public_uncertain"
            and prior.get("retry_safe") is True
        )
    )
    if matching_attempts and not recoverable_public_retry:
        print(f"{mode_tag}_DUPLICATE_GUARD " + json.dumps({
            "status": str(prior.get("status") or "prior_attempt"),
            "retry_policy": "no_duplicate_upload_attempt",
            "source_url": normalized_url,
        }, ensure_ascii=False))
        return 4

    with tempfile.TemporaryDirectory(prefix=("ifilo-review-public-" if public else "ifilo-review-draft-")) as temp_dir:
        if prepared_video is not None:
            video_path = Path(prepared_video)
            quality = prepared_quality if isinstance(prepared_quality, dict) else {}
            if (
                not video_path.is_file()
                or not 0 < video_path.stat().st_size <= MAX_VIDEO_BYTES
                or quality.get("passed") is not True
                or quality.get("content_gate_passed") is not True
            ):
                print(f"{mode_tag}_BLOCKED_PREPARED_MEDIA_QC")
                return 3
        else:
            video_path, quality = prepare_candidate_video(item, Path(temp_dir))
            if video_path is None:
                print(f"{mode_tag}_BLOCKED_MEDIA_QC " + json.dumps({
                    "reason": str(quality.get("reason") or quality.get("content_gate_reason") or "media_not_qualified"),
                    "source_url": normalized_url,
                }, ensure_ascii=False))
                return 3

        source_title = str(item.get("title") or item.get("source_post_caption") or "")[:300]
        base_title = make_title(item)
        draft_title = normalized_text("پیش‌نویس خصوصی | " + base_title, limit=100)[:100]
        title = base_title if public else draft_title
        if public:
            description = make_description(item, base_title) + "\n\nمنبع اصلی: " + normalized_url
            # A previous uncertain/rejected draft can exist despite its status. Do not
            # create a duplicate if either its private title or the intended public title
            # is already present in the authenticated account.
            found_public, _ = _account_page_contains_title(page, title)
            found_draft, _ = _account_page_contains_title(page, draft_title)
            if found_public or found_draft:
                print(f"{mode_tag}_BLOCKED_EXISTING_ACCOUNT_VIDEO " + json.dumps({
                    "public_title_found": bool(found_public),
                    "prior_draft_title_found": bool(found_draft),
                    "action": "no_duplicate_upload; existing item needs visibility review",
                    "source_url": normalized_url,
                }, ensure_ascii=False))
                return 4
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(800)
            form_summary = probe_upload_form(page)
            if not form_summary.get("file_input_count", 0):
                print(f"{mode_tag}_BLOCKED_UPLOAD_FORM_NOT_READY")
                return 3
        else:
            description = (
                make_description(item, base_title)
                + "\n\nپیش‌نویس غیرعمومی برای بررسی فنی؛ انتشار عمومی، کسب درآمد، دانلود و بازنشر خاموش است."
                + "\nمنبع اصلی: " + normalized_url
            )
        description = description[:1800]

        file_input = page.locator("#fileInput")
        if file_input.count() == 0:
            print(f"{mode_tag}_BLOCKED_NO_PRIMARY_FILE_INPUT")
            return 3
        try:
            file_input.set_input_files(str(video_path), timeout=30000)
        except Exception as exc:
            print(f"{mode_tag}_FILE_SELECTION_FAILED", type(exc).__name__)
            return 3

        # Wait for the actual chunk transfer, not an arbitrary sleep or only the progress bar.
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
                    videoCodePresent: !!String(item.videoCode || '').trim(),
                    durationPresent: Number(item.duration || 0) > 0
                };
            }""")
        except Exception:
            upload_state = {}

        if not upload_state.get("uploadComplete") or not upload_state.get("videoCodePresent"):
            print(f"{mode_tag}_FILE_TRANSFER_INCOMPLETE " + json.dumps({
                **upload_state,
                "final_publish_request_sent": False,
            }, ensure_ascii=False))
            return 3

        # FiLo refuses publication without a selected/uploaded thumbnail. Extract one
        # representative frame from this same verified video and confirm the site's
        # own thumbnail uploader has committed it to the current video model.
        thumbnail_state = upload_video_thumbnail(page, video_path, Path(temp_dir), mode_tag)
        if not thumbnail_state.get("uploaded"):
            print(f"{mode_tag}_BLOCKED_THUMBNAIL_NOT_CONFIRMED " + json.dumps({
                **thumbnail_state,
                "final_publish_request_sent": False,
                "source_url": normalized_url,
            }, ensure_ascii=False))
            return 3

        title_ok, description_ok = fill_metadata(page, title, description)
        rules_ok = _set_checkbox(page, "#acceptRules", True)
        publication_flag_ok = _set_checkbox(page, "#dontPublish", not public)
        # Only the queue publisher with explicit rights evidence may enable monetization.
        monetization_expected = bool(public and candidate is not None and rights_cleared_for_ifilo(item))
        monetization_set = _set_checkbox(page, "#canMakeMoney", monetization_expected, allow_hidden=True)
        redistribution_off = _set_checkbox(page, "#canRepublish", False, allow_hidden=True)
        download_off = _set_checkbox(page, "#allowDownload", False, allow_hidden=True)
        ownership_not_claimed = _set_checkbox(page, "#isMadeByMe", False, allow_hidden=True)

        try:
            model = page.evaluate("""(expected) => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return {};
                const scope = window.angular.element(root).scope();
                if (!scope) return {};
                const item = scope.item || {};
                return {
                    titleMatches: String(item.title || '') === expected.title,
                    descriptionMatches: String(item.description || '') === expected.description,
                    dontPublish: !!Number(item.dontPublish),
                    canMakeMoney: !!item.canMakeMoney,
                    canRepublish: !!item.canRepublish,
                    allowDownload: !!item.allowDownload,
                    isMadeByMe: !!item.isMadeByMe,
                    acceptRules: !!scope.acceptRules,
                    uploadComplete: !!scope.uploadComplete,
                    videoCodePresent: !!String(item.videoCode || '').trim(),
                    customThumb: Number(item.customThumb || 0),
                    selectedFramePresent: !!String(item.selectedFrame || '').trim(),
                    imagePresent: !!String(item.image || '').trim()
                };
            }""", {"title": title, "description": description})
        except Exception:
            model = {}

        dom_safe = all((
            title_ok, description_ok, rules_ok, publication_flag_ok, monetization_set,
            redistribution_off, download_off, ownership_not_claimed,
            page.locator("#dontPublish").is_checked() is (not public),
            page.locator("#canMakeMoney").is_checked() is monetization_expected,
            not page.locator("#canRepublish").is_checked(),
            not page.locator("#allowDownload").is_checked(),
            not page.locator("#isMadeByMe").is_checked(),
            # FiLo may unset the radio's visual checked state after the async
            # uploader commits the thumbnail; Angular's item model is checked below.
        ))
        model_safe = (
            model.get("titleMatches") is True
            and model.get("descriptionMatches") is True
            and model.get("dontPublish") is (not public)
            and model.get("canMakeMoney") is monetization_expected
            and model.get("canRepublish") is False
            and model.get("allowDownload") is False
            and model.get("isMadeByMe") is False
            and model.get("acceptRules") is True
            and model.get("uploadComplete") is True
            and model.get("videoCodePresent") is True
            and model.get("customThumb") == 1
            and (
                model.get("selectedFramePresent") is True
                or model.get("imagePresent") is True
            )
        )
        if not dom_safe or not model_safe:
            print(f"{mode_tag}_BLOCKED_SAFETY_CHECK " + json.dumps({
                "dom_safe": bool(dom_safe),
                "model_safe": bool(model_safe),
                "model": model,
                "source_url": normalized_url,
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
            print(f"{mode_tag}_BLOCKED_FINAL_ACTION_NOT_READY")
            return 3

        attempt = {
            "source_key": source_key(item),
            "source_url": normalized_url,
            "source_title": source_title,
            "title": title,
            "status": "pending",
            "mode": (
                ("queue_public" if public else "queue_draft_not_public")
                if candidate is not None
                else ("telegram_review_public" if public else "telegram_review_draft_not_public")
            ),
            "started_at": utc_now(),
            "video_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
            "bytes": video_path.stat().st_size,
            "quality": quality,
            "rights_evidence_present": bool(str(item.get("rights_evidence_url") or item.get("license_url") or "").strip()),
            "rights_evidence_host": urlparse(str(item.get("rights_evidence_url") or item.get("license_url") or "")).netloc.lower(),
            "publication_checkbox": "dontPublish=false" if public else "dontPublish=true",
            "monetization_checkbox": f"canMakeMoney={str(monetization_expected).lower()}",
            "redistribution_checkbox": "canRepublish=false",
            "download_checkbox": "allowDownload=false",
            "ownership_checkbox": "isMadeByMe=false",
        }
        state.setdefault("attempts", []).append(attempt)
        write_state(state)

        request_state = {"matching_post_count": 0, "continued_count": 0, "blocked_count": 0, "checks": {}}
        expected_host = urlparse(BASE_URL).netloc.lower()
        false_values = {"", "0", "false", "off", "no", "none", "null"}
        true_values = {"1", "true", "on", "yes"}
        response_summary = {
            "response_received": False,
            "http_status": None,
            "payload_success": None,
            "content_type": "",
            "body_kind": "not_available",
        }
        submit_error_type = None

        def guard_publish_route(route):
            request = route.request
            parsed = urlparse(request.url)
            if request.method.upper() != "POST" or parsed.path.rstrip("/") != "/video/publish":
                route.continue_()
                return
            request_state["matching_post_count"] += 1
            fields = {}
            try:
                fields = parse_qs(request.post_data or "", keep_blank_values=True)
            except Exception:
                fields = {}

            def field(name):
                vals = fields.get(name, [""])
                return str(vals[-1] if vals else "").strip().casefold()

            checks = {
                "canonical_host": parsed.netloc.lower() == expected_host,
                "exact_title": str(fields.get("title", [""])[-1]) == title,
                "exact_description": str(fields.get("description", [""])[-1]) == description,
                # FiLo serializes unchecked checkboxes as 0/empty on some requests.
                "publication_state_matches": field("dontPublish") in (false_values if public else true_values),
                "monetization_state_matches": field("canMakeMoney") in (true_values if monetization_expected else false_values),
                "redistribution_false": field("canRepublish") in false_values,
                "download_false": field("allowDownload") in false_values,
                "not_claiming_ownership": field("isMadeByMe") in false_values,
                "first_request_only": request_state["matching_post_count"] == 1,
            }
            request_state["checks"] = checks
            if all(checks.values()) and request_state["continued_count"] == 0:
                request_state["continued_count"] += 1
                route.continue_()
            else:
                request_state["blocked_count"] += 1
                route.abort("blockedbyclient")

        try:
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
                response = response_info.value
                response_summary["response_received"] = True
                response_summary["http_status"] = int(response.status)
                response_summary["content_type"] = str(response.headers.get("content-type") or "")[:80]
                try:
                    payload = response.json()
                    response_summary["body_kind"] = type(payload).__name__
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
                    if isinstance(payload, dict):
                        if "s" in payload:
                            response_summary["payload_success"] = bool(payload.get("s"))
                        response_summary["response_keys"] = sorted(str(k) for k in payload.keys())[:20]
                except Exception:
                    try:
                        raw_body = response.text()
                        response_summary["body_kind"] = "text" if raw_body.strip() else "empty"
                        response_summary["body_length"] = len(raw_body)
                        response_summary["body_prefix_kind"] = (
                            "json_object" if raw_body.lstrip().startswith("{")
                            else "json_array" if raw_body.lstrip().startswith("[")
                            else "html" if raw_body.lstrip().startswith("<")
                            else "plain_text"
                        )
                    except Exception:
                        pass
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

        try:
            page.wait_for_timeout(1200)
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
                    dontPublish: !!Number(item.dontPublish),
                    canMakeMoney: !!item.canMakeMoney,
                    canRepublish: !!item.canRepublish,
                    allowDownload: !!item.allowDownload,
                    isMadeByMe: !!item.isMadeByMe,
                    acceptRules: !!scope.acceptRules,
                    messageIsError: !!scope.messageIsError,
                    titleCleared: String(item.title || '') === '',
                    descriptionCleared: String(item.description || '') === ''
                };
            }""")
            message = page.evaluate("""() => {
                const root = document.querySelector('[ng-controller="ctrl"]');
                if (!root || !window.angular) return '';
                const scope = window.angular.element(root).scope();
                return scope ? String(scope.message || '').slice(0, 500) : '';
            }""")
        except Exception:
            final_model = {}

        message_success = bool(DRAFT_SUCCESS_RE.search(message)) and final_model.get("messageIsError") is False
        provider_message_excerpt = safe_provider_message_excerpt(message)
        # Persist only a coarse reason code and scrubbed provider text, not account data.
        message_signals = []
        signal_patterns = (
            ("title", ("عنوان",)),
            ("description", ("توضیح",)),
            ("format", ("فرمت",)),
            ("size", ("حجم",)),
            ("duplicate", ("تکراری", "قبلا", "قبلاً")),
            ("rights_or_policy", ("مجوز", "مالک", "حق نشر", "قانون", "مجاز")),
            ("publication", ("انتشار", "منتشر")),
            ("verification", ("کد تایید", "کد تأیید", "captcha", "تایید کنید", "تأیید کنید")),
            ("upload", ("آپلود", "بارگذاری")),
            ("failure", ("ناموفق", "خطا", "نشد", "نمی توان", "نمی‌توان", "امکان ندارد")),
        )
        folded_message = message.casefold()
        for signal, phrases in signal_patterns:
            if any(phrase.casefold() in folded_message for phrase in phrases):
                message_signals.append(signal)
        if not message:
            message_kind = "none"
        elif final_model.get("messageIsError") is True:
            if "title" in message_signals:
                message_kind = "title_validation"
            elif "description" in message_signals:
                message_kind = "description_validation"
            elif "format" in message_signals:
                message_kind = "file_format_validation"
            elif "size" in message_signals:
                message_kind = "file_size_validation"
            elif "duplicate" in message_signals:
                message_kind = "duplicate_video_or_post"
            elif "rights_or_policy" in message_signals:
                message_kind = "rights_or_policy_rejection"
            elif "verification" in message_signals:
                message_kind = "verification_required"
            elif "failure" in message_signals:
                message_kind = "provider_reported_failure"
            elif "upload" in message_signals:
                message_kind = "provider_upload_rejection"
            else:
                message_kind = "unclassified_provider_rejection"
        elif "title" in message_signals:
            message_kind = "title_validation"
        elif "description" in message_signals:
            message_kind = "description_validation"
        elif "format" in message_signals:
            message_kind = "file_format_validation"
        elif "size" in message_signals:
            message_kind = "file_size_validation"
        elif "rights_or_policy" in message_signals:
            message_kind = "rights_or_ownership_message"
        elif "upload" in message_signals or "با موفقیت" in folded_message or "ثبت شد" in folded_message:
            message_kind = "success_like_or_upload_related"
        else:
            message_kind = "other_message"

        # Verify the draft from read-only account views after the request. Never retry if uncertain.
        post_submit_url = str(page.url or "")
        post_submit_path = urlparse(post_submit_url)
        public_video_url = (
            post_submit_url.split("?", 1)[0]
            if post_submit_url != UPLOAD_URL
            and post_submit_path.netloc.lower() == expected_host
            and re.search(r"/(?:v|video|watch)/[^/]+", post_submit_path.path, re.I)
            else ""
        )
        found_in_account, account_reports = _account_page_contains_title(page, title)
        safe_report = {
            "http_response": response_summary,
            "request_guard": request_state,
            "final_angular_state": final_model,
            "message_kind": message_kind,
            "provider_message_excerpt": provider_message_excerpt,
            "message_present": bool(message),
            "account_title_found": found_in_account,
            "account_paths_checked": account_reports,
            "public_video_url_detected": bool(public_video_url),
            "submit_error_type": submit_error_type,
        }

        # Persist the local request guard so retries can distinguish a post that
        # never left the browser from a submission with an uncertain server result.
        attempt["request_guard"] = dict(request_state)
        attempt["http_response_summary"] = dict(response_summary)
        attempt["public_video_url_detected"] = bool(public_video_url)
        attempt["server_reason_kind"] = message_kind
        attempt["provider_message_signals"] = list(message_signals)
        attempt["provider_message_excerpt"] = provider_message_excerpt
        attempt["verification_summary"] = {
            "final_angular_state": dict(final_model),
            "account_title_found": bool(found_in_account),
            "account_paths_checked": account_reports,
            "submit_error_type": submit_error_type,
        }

        if public_video_url and not public:
            attempt["status"] = "review_draft_unexpected_public_route"
            attempt["finished_at"] = utc_now()
            attempt["ifilo_url"] = public_video_url
            write_state(state)
            print(f"{mode_tag}_UNEXPECTED_PUBLIC_ROUTE " + json.dumps({
                "source_url": normalized_url,
                "safety_flags_were_set_before_submit": True,
                "verification": safe_report,
            }, ensure_ascii=False))
            return 4

        success = (
            request_state["continued_count"] == 1
            and (
                response_summary["payload_success"] is True
                or message_success
                or found_in_account
                or (public and bool(public_video_url))
            )
        )
        if success:
            attempt["status"] = "uploaded" if public else "draft_uploaded"
            attempt["finished_at"] = utc_now()
            attempt["ifilo_url"] = public_video_url
            attempt["confirmation"] = (
                "public_video_route_detected" if public and public_video_url
                else "account_title_found" if found_in_account
                else "server_success_payload" if response_summary["payload_success"] is True
                else "success_message_without_error_flag"
            )
            write_state(state)
            print(f"{mode_tag}_SUCCESS " + json.dumps({
                "title": title,
                "source_url": normalized_url,
                "status": attempt["status"],
                "ifilo_url": public_video_url,
                "public_post": bool(public),
                "monetization_enabled": monetization_expected,
                "redistribution_enabled": False,
                "download_enabled": False,
                "ownership_claimed": False,
                "bytes": attempt["bytes"],
                "verification": safe_report,
            }, ensure_ascii=False))
            return 0

        attempt["status"] = (
            ("review_public_rejected" if public else "review_draft_rejected")
            if final_model.get("messageIsError") is True
            else ("review_public_uncertain" if public else "review_draft_uncertain")
        )
        attempt["finished_at"] = utc_now()
        attempt["server_reason_kind"] = message_kind
        write_state(state)
        print(f"{mode_tag}_FAILED " + json.dumps({
            "source_url": normalized_url,
            "status": attempt["status"],
            "retry_policy": "no_retry_after_uncertain_or_rejected_result",
            "verification": safe_report,
        }, ensure_ascii=False))
        return 5 if attempt["status"] == "review_draft_rejected" else 4


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


def run_browser(publish: bool, test_draft: bool = False, verify_test_draft: bool = False, inspect_client: bool = False, review_draft_source_url: str = "", review_public_source_url: str = "") -> int:
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
                if review_public_source_url:
                    return upload_review_video_from_telegram(page, review_public_source_url, public=True)
                if review_draft_source_url:
                    return upload_review_video_from_telegram(page, review_draft_source_url, public=False)
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
                    print("IFILO_NO_ELIGIBLE_NEW_CANDIDATE: No fresh, qualified, unattempted native Telegram video with rights evidence is ready.")
                    return 0

                # Use the full checked transfer + thumbnail + publish flow; never the
                # older shallow form-submit path. Process one eligible item per run.
                with tempfile.TemporaryDirectory(prefix="ifilo-media-") as media_tmp:
                    for row in candidates:
                        video_path, quality = prepare_candidate_video(row, Path(media_tmp))
                        if video_path is None:
                            record_queue_media_failure(
                                row,
                                str(quality.get("reason") or quality.get("content_gate_reason") or "media_gate_rejected"),
                            )
                            continue
                        if has_prior_attempt(row, load_state()):
                            print("IFILO_DUPLICATE_GUARD: Candidate was attempted by another run.")
                            continue
                        return upload_review_video_from_telegram(
                            page,
                            str(row.get("url") or ""),
                            public=True,
                            candidate=row,
                            prepared_video=video_path,
                            prepared_quality=quality,
                        )

                print("IFILO_NO_CANDIDATE_PASSED_MEDIA_QC")
                return 0
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
    parser.add_argument(
        "--upload-review-draft",
        metavar="TELEGRAM_POST_URL",
        help="Upload one previously sent Telegram review video as an unpublished, non-monetized draft only.",
    )
    parser.add_argument(
        "--publish-review-video",
        metavar="TELEGRAM_POST_URL",
        help="Publish one exact, previously reviewed Telegram video publicly after checking the authenticated account for duplicates.",
    )
    args = parser.parse_args()
    if sum(bool(x) for x in (args.publish, args.test_draft, args.verify_test_draft, args.inspect_upload_client, args.upload_review_draft, args.publish_review_video)) > 1:
        parser.error("--publish, --test-draft, --verify-test-draft, --inspect-upload-client, --upload-review-draft, and --publish-review-video are mutually exclusive")
    mode = "inspect_upload_client_read_only" if args.inspect_upload_client else (
        "verify_test_draft_read_only" if args.verify_test_draft else (
            "review_video_public" if args.publish_review_video else (
                "review_video_draft_not_public" if args.upload_review_draft else (
                    "test_draft_not_public" if args.test_draft else ("publish_one" if args.publish else "probe_only")
                )
            )
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
        return run_browser(publish=args.publish, test_draft=args.test_draft, verify_test_draft=args.verify_test_draft, inspect_client=args.inspect_upload_client, review_draft_source_url=args.upload_review_draft or "", review_public_source_url=args.publish_review_video or "")
    except Exception as exc:
        # Avoid exception messages from browser/network layers because they can
        # include full request URLs, query tokens, or personal account details.
        print("IFILO_RUN_FAILED " + json.dumps({"exception_type": type(exc).__name__}, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
