#!/usr/bin/env python3
"""Independent visible-browser uploader for Filo (ifilo.net)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE_URL = os.environ.get("FILO_BASE_URL", "https://ifilo.net").strip().rstrip("/")
MAX_VIDEO_BYTES = 48 * 1024 * 1024
LOGIN_RE = re.compile(r"/(?:login|signin|sign-in|account/login)(?:[/?#]|$)", re.I)
CHALLENGE_RE = re.compile(r"captcha|کد\s*(?:تأیید|تایید|امنیتی)|رمز\s*یکبار|پیامک|two.?factor|verification\s*code|one.?time\s*password", re.I)
TITLE_RE = re.compile(r"title|video.?name|عنوان|نام\s*ویدیو|نام\s*فیلم", re.I)
DESCRIPTION_RE = re.compile(r"description|details|توضیحات|شرح\s*ویدیو|شرح", re.I)
NEXT_RE = re.compile(r"بعدی|ادامه|مرحله\s*بعد|تکمیل\s*اطلاعات|next|continue", re.I)
SUBMIT_RE = re.compile(r"آپلود|بارگذاری|انتشار|ثبت|ارسال|ذخیره|publish|upload|submit", re.I)
SUCCESS_RE = re.compile(r"با\s*موفقیت|آپلود\s*شد|بارگذاری\s*شد|منتشر\s*شد|ثبت\s*شد|در\s*انتظار\s*(?:بررسی|پردازش)|upload(?:ed)?\s*success|published|successfully\s*(?:uploaded|submitted)", re.I)


def enabled():
    return os.environ.get("FILO_UPLOAD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def prepare_metadata(item: dict, fallback_title: str = "", fallback_description: str = "") -> dict:
    """Create a relevant title/description without external services."""
    item = item if isinstance(item, dict) else {}
    title = str(item.get("title") or item.get("source_post_caption") or item.get("cleaned_review_caption") or item.get("description") or fallback_title or "")
    title = re.sub(r"https?://\S+|www\.\S+", " ", title, flags=re.I)
    title = re.sub(r"(?<!\w)@[A-Za-z0-9_]{2,}", " ", title)
    title = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", " ", title)
    title = re.sub(r"[#*~|<>]+", " ", title)
    title = re.sub(r"[!！?？.،؛,:：\-—_=+]{2,}", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" \t\r\n-–—|•:،؛.")
    title = title[:100].strip()
    if len(title) < 3:
        raise ValueError("title_missing_or_too_short")

    source_url = str(item.get("url") or item.get("source_url") or "").strip()
    description = str(item.get("source_post_caption") or item.get("description") or item.get("cleaned_review_caption") or fallback_description or "")
    description = re.sub(r"[\u200b\u200e\u200f\u2060\ufeff]", "", description)
    description = re.sub(r"\s+", " ", description).strip()[:1250]
    if source_url and source_url not in description:
        description = (description + "\n\nمنبع ویدیو: " + source_url).strip()
    return {"title": title, "description": description[:1500], "source_url": source_url}


def _state():
    encoded = os.environ.get("FILO_STORAGE_STATE_BASE64", "").strip()
    if not encoded:
        return None
    try:
        data = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("cookies", []), list):
            raise ValueError
        return data
    except Exception as exc:
        raise ValueError("FILO_STORAGE_STATE_BASE64 must contain base64 Playwright storage-state JSON") from exc


def _start():
    from playwright.sync_api import sync_playwright
    manager = sync_playwright().start()
    browser = manager.chromium.launch(headless=True, args=["--no-sandbox"])
    state = _state()
    state_file = None
    kwargs = {"viewport": {"width": 1440, "height": 1000}, "locale": "fa-IR"}
    if state is not None:
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
        json.dump(state, state_file)
        state_file.close()
        kwargs["storage_state"] = state_file.name
    context = browser.new_context(**kwargs)
    page = context.new_page()
    page.set_default_timeout(8000)
    return manager, browser, context, page, state_file


def _visible(locator):
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _meta(locator):
    try:
        return locator.evaluate("""el => ({
          type:(el.getAttribute('type')||'').toLowerCase(), name:el.getAttribute('name')||'',
          id:el.id||'', placeholder:el.getAttribute('placeholder')||'',
          aria:el.getAttribute('aria-label')||'', accept:el.getAttribute('accept')||'',
          labels:Array.from(el.labels||[]).map(x=>(x.innerText||x.textContent||'').trim()).join(' ')
        })""")
    except Exception:
        return {}


def _snapshot(page):
    try:
        fields = page.locator("input, textarea, select, button").evaluate_all("""els => els.slice(0,80).map(el=>({
          tag:el.tagName.toLowerCase(),type:(el.getAttribute('type')||'').toLowerCase(),
          name:el.getAttribute('name')||'',id:el.id||'',placeholder:el.getAttribute('placeholder')||'',
          aria:el.getAttribute('aria-label')||'',accept:el.getAttribute('accept')||'',
          text:(el.innerText||el.textContent||'').trim().slice(0,80),
          labels:Array.from(el.labels||[]).map(x=>(x.innerText||x.textContent||'').trim()).join(' ')
        }))""")
    except Exception:
        fields = []
    try:
        links = page.locator("a[href]").evaluate_all("""els=>els.slice(0,120).map(el=>({
          text:(el.innerText||el.textContent||'').trim().slice(0,70),href:el.href||''
        })).filter(x=>/upload|login|sign.?in|ورود|آپلود|بارگذاری/i.test(x.text+' '+x.href)).slice(0,40)""")
    except Exception:
        links = []
    try:
        title = page.title()
    except Exception:
        title = ""
    return {"url": str(page.url), "page_title": title[:180], "fields": fields, "relevant_links": links}


def _login(page, base_url):
    username = os.environ.get("FILO_USERNAME", "").strip()
    password = os.environ.get("FILO_PASSWORD", "")
    if not username or not password:
        return "credentials_not_configured"
    try:
        if not LOGIN_RE.search(urlparse(page.url).path):
            links = page.locator("a[href*='login'], a[href*='signin'], a:has-text('ورود')")
            if _visible(links):
                links.first.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=12000)
            if not LOGIN_RE.search(urlparse(page.url).path):
                page.goto(urljoin(base_url + "/", "login?m=3&r=%2Fupload"), wait_until="domcontentloaded", timeout=30000)
        body = page.locator("body").inner_text(timeout=4000)[:4000]
        if CHALLENGE_RE.search(body):
            return "interactive_verification_required"
        passwords = page.locator("input[type='password']")
        if passwords.count() == 0:
            return "unexpected_login_form"
        fields = page.locator("input:not([type='hidden']):not([type='password']):not([type='submit']):not([type='button']):not([type='file']):not([type='search'])")
        chosen = None
        for i in range(fields.count()):
            field = fields.nth(i)
            if not _visible(field):
                continue
            meta = _meta(field)
            joined = " ".join(str(meta.get(k) or "") for k in ("name", "id", "placeholder", "aria", "labels", "type"))
            if re.search(r"phone|mobile|user|email|نام\s*کاربری|موبایل|تلفن|ایمیل|شماره", joined, re.I):
                chosen = field
                break
        if chosen is None:
            for i in range(fields.count()):
                field = fields.nth(i)
                if _visible(field) and _meta(field).get("type", "") in {"", "text", "email", "tel"}:
                    chosen = field
                    break
        if chosen is None:
            return "username_field_not_found"
        chosen.fill(username)
        passwords.first.fill(password)
        submits = page.locator("button[type='submit'], input[type='submit']")
        if _visible(submits):
            submits.first.click(timeout=10000)
        else:
            submit = page.get_by_role("button", name=re.compile(r"ورود|تأیید|تایید|ادامه|login|sign in", re.I))
            if not _visible(submit):
                return "login_submit_button_not_found"
            submit.first.click(timeout=10000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text(timeout=4000)[:4000]
        if CHALLENGE_RE.search(body):
            return "interactive_verification_required"
        if LOGIN_RE.search(urlparse(page.url).path) and page.locator("input[type='password']").count():
            return "login_rejected_or_extra_step_required"
        return "login_attempted"
    except Exception as exc:
        return "login_flow_error:" + type(exc).__name__


def _goto_upload(page, base_url):
    response = page.goto(urljoin(base_url + "/", "upload"), wait_until="domcontentloaded", timeout=35000)
    status = response.status if response else None
    if page.locator("input[type='file']").count() == 0:
        try:
            links = page.get_by_role("link", name=re.compile(r"آپلود\s*ویدیو|بارگذاری\s*ویدیو|upload\s*video", re.I))
            if _visible(links):
                links.first.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
    return status


def probe_site():
    """Inspect public reachability, auth state, and upload form. Never uploads."""
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL, "mode": "probe_only", "status": "unknown"}
    manager = browser = context = page = state_file = None
    try:
        manager, browser, context, page, state_file = _start()
        try:
            response = page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=35000)
            report["home"] = {"http_status": response.status if response else None, **_snapshot(page)}
        except Exception as exc:
            report["status"] = "site_unreachable_or_navigation_failed"
            report["error"] = type(exc).__name__
            report["detail"] = str(exc)[:200]
            return report
        status = _goto_upload(page, BASE_URL)
        if LOGIN_RE.search(urlparse(page.url).path) or page.locator("input[type='password']").count():
            report["authentication"] = _login(page, BASE_URL)
            if report["authentication"] == "login_attempted":
                status = _goto_upload(page, BASE_URL)
        else:
            report["authentication"] = "session_or_public_route"
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=4000)[:4000]
        except Exception:
            pass
        report["upload"] = {"http_status": status, **_snapshot(page)}
        if CHALLENGE_RE.search(body):
            report["status"] = "interactive_verification_required"
        elif LOGIN_RE.search(urlparse(page.url).path) or page.locator("input[type='password']").count():
            report["status"] = "login_required"
        elif page.locator("input[type='file']").count() > 0:
            report["status"] = "upload_form_detected"
        elif status and status >= 500:
            report["status"] = "server_error"
        elif status and status >= 400:
            report["status"] = "upload_route_not_found_or_blocked"
        else:
            report["status"] = "site_reachable_upload_form_not_confirmed"
        report["notes"] = ["Probe did not select a file or submit a video."]
        return report
    except Exception as exc:
        report["status"] = "probe_failed"
        report["error"] = type(exc).__name__
        report["detail"] = str(exc)[:200]
        return report
    finally:
        for obj, method in ((context, "close"), (browser, "close"), (manager, "stop")):
            try:
                if obj is not None:
                    getattr(obj, method)()
            except Exception:
                pass
        try:
            if state_file:
                os.unlink(state_file.name)
        except OSError:
            pass


def _find_field(page, pattern, fallback_index=None):
    fields = page.locator("input:not([type='hidden']):not([type='password']):not([type='file']):not([type='submit']):not([type='button']):not([type='search']), textarea")
    matches = []
    for i in range(fields.count()):
        field = fields.nth(i)
        if not _visible(field):
            continue
        meta = _meta(field)
        joined = " ".join(str(meta.get(k) or "") for k in ("name", "id", "placeholder", "aria", "labels"))
        if pattern.search(joined):
            matches.append(field)
    if matches:
        return matches[0]
    if fallback_index is not None:
        visible = []
        for i in range(fields.count()):
            field = fields.nth(i)
            if _visible(field) and _meta(field).get("type", "") in {"", "text", "email", "tel"}:
                visible.append(field)
        if fallback_index < len(visible):
            return visible[fallback_index]
    return None


def upload_video(video_path: str | Path, item: dict, history: list | None = None) -> dict:
    """Attempt one upload using the rendered form, never bypassing login/challenges."""
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "success": False, "status": "not_attempted", "source_url": str((item or {}).get("url") or ""), "destination_url": "", "title": "", "reason": ""}
    if not enabled():
        result.update(status="disabled", reason="FILO_UPLOAD_ENABLED_is_not_true")
        return result
    path = Path(video_path)
    if not path.is_file() or path.stat().st_size <= 100_000 or path.stat().st_size > MAX_VIDEO_BYTES:
        result.update(status="failed", reason="video_file_missing_or_size_out_of_range")
        return result
    try:
        meta = prepare_metadata(item)
        result["title"] = meta["title"]
    except Exception as exc:
        result.update(status="failed", reason=str(exc)[:140])
        return result
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result["sha256"] = digest
    for old in history if isinstance(history, list) else []:
        if isinstance(old, dict) and old.get("status") in {"submitted", "published", "success"}:
            if old.get("sha256") == digest or (meta["source_url"] and old.get("source_url") == meta["source_url"]):
                result.update(status="duplicate_skipped", reason="same_source_or_file_already_submitted")
                return result

    manager = browser = context = page = state_file = None
    try:
        manager, browser, context, page, state_file = _start()
        try:
            page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=35000)
        except Exception as exc:
            result.update(status="failed", reason="site_unreachable:" + type(exc).__name__)
            return result
        code = _goto_upload(page, BASE_URL)
        if LOGIN_RE.search(urlparse(page.url).path) or page.locator("input[type='password']").count():
            auth = _login(page, BASE_URL)
            result["auth_status"] = auth
            if auth != "login_attempted":
                result.update(status="failed", reason=auth, destination_url=str(page.url))
                return result
            code = _goto_upload(page, BASE_URL)
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=4000)[:4000]
        except Exception:
            pass
        if CHALLENGE_RE.search(body):
            result.update(status="failed", reason="interactive_verification_required", destination_url=str(page.url))
            return result
        if LOGIN_RE.search(urlparse(page.url).path) or page.locator("input[type='password']").count():
            result.update(status="failed", reason="login_required", http_status=code, destination_url=str(page.url))
            return result
        files = page.locator("input[type='file']")
        chosen_file = None
        for i in range(files.count()):
            field = files.nth(i)
            accept = str(_meta(field).get("accept") or "").lower()
            if not accept or "video" in accept or ".mp4" in accept or ".mov" in accept:
                chosen_file = field
                break
        if chosen_file is None:
            result.update(status="failed", reason="video_file_input_not_found", destination_url=str(page.url))
            return result
        chosen_file.set_input_files(str(path))
        page.wait_for_timeout(2500)
        title_field = _find_field(page, TITLE_RE, 0)
        desc_field = _find_field(page, DESCRIPTION_RE)
        if title_field is None:
            next_button = page.get_by_role("button", name=NEXT_RE)
            if not _visible(next_button):
                next_button = page.get_by_role("link", name=NEXT_RE)
            if _visible(next_button):
                next_button.first.click(timeout=8000)
                page.wait_for_timeout(1500)
                title_field = _find_field(page, TITLE_RE, 0)
                desc_field = _find_field(page, DESCRIPTION_RE)
        if title_field is None:
            result.update(status="failed", reason="title_field_not_found_after_file_selection", destination_url=str(page.url))
            return result
        title_field.fill(meta["title"])
        if desc_field is not None and meta["description"]:
            desc_field.fill(meta["description"])

        candidates = page.get_by_role("button", name=SUBMIT_RE)
        button = None
        for i in range(candidates.count()):
            candidate = candidates.nth(i)
            if _visible(candidate):
                label = str(candidate.inner_text() or "") + " " + str(candidate.get_attribute("aria-label") or "")
                if SUBMIT_RE.search(label) and not NEXT_RE.search(label):
                    button = candidate
        if button is None:
            submit = page.locator("button[type='submit'], input[type='submit']")
            if _visible(submit):
                button = submit.first
        if button is None:
            result.update(status="failed", reason="final_submit_control_not_found", destination_url=str(page.url))
            return result
        button.click(timeout=12000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(8000)
        result["destination_url"] = str(page.url)
        try:
            final_body = page.locator("body").inner_text(timeout=5000)[:9000]
        except Exception:
            final_body = ""
        route = urlparse(page.url).path
        video_route = bool(re.search(r"/(?:v|video|watch)/[A-Za-z0-9_-]+", route, re.I))
        confirmed = bool(SUCCESS_RE.search(final_body)) and meta["title"].casefold() in final_body.casefold()
        if video_route or confirmed:
            result.update(status="submitted", success=True, reason="destination_confirmation_detected")
        else:
            result.update(status="submission_unverified", reason="no_explicit_success_confirmation")
        return result
    except Exception as exc:
        result.update(status="failed", reason="browser_upload_error:" + type(exc).__name__)
        return result
    finally:
        for obj, method in ((context, "close"), (browser, "close"), (manager, "stop")):
            try:
                if obj is not None:
                    getattr(obj, method)()
            except Exception:
                pass
        try:
            if state_file:
                os.unlink(state_file.name)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Probe Filo without publishing a video.")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if not args.probe:
        parser.error("Use --probe for a non-publishing diagnostic run.")
    print(json.dumps(probe_site(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
