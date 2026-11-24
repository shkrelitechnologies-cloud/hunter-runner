"""HUNTER's browser-only publisher for explicitly permission-reviewed destinations.

No API publisher, OAuth client, account creation, or credential handling is present.
A site is eligible only when current official evidence, a site-specific field map,
no-login access, and public readback requirements are all explicitly recorded.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from hunter import valid_evidence, safe_url, stamp

BLOCKERS = (
    "captcha", "verify you are human", "unusual traffic", "security check",
    "sign in", "log in", "login required", "email verification", "verify your email",
    "phone verification", "payment required", "checkout", "approval required",
    "permission denied", "access denied", "rate limit", "too many requests",
)


class PublishError(Exception):
    """Controlled publishing error code; never contains page text or credentials."""


def check_settings(cfg: dict, now: datetime | None = None) -> bool:
    """Accept only the explicit browser connector. Empty site lists are safe no-ops."""
    p = cfg.get("publishing", {}) if isinstance(cfg, dict) else {}
    return bool(isinstance(p, dict) and p.get("enabled") is True
                and p.get("connector") == "browser"
                and isinstance(p.get("destinations", []), list)
                and not any(k in p for k in ("token", "oauth", "access_token", "api_key", "password")))


def _host(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return parsed.hostname.lower().rstrip(".")
    except (TypeError, ValueError):
        return ""


def _approved(site: dict, now: datetime) -> bool:
    permission = site.get("permission") or {}
    if not isinstance(permission, dict):
        return False
    if permission.get("status") != "verified" or permission.get("automation_allowed") is not True:
        return False
    if permission.get("login_required") is not False or permission.get("email_verification_required") is not False:
        return False
    if permission.get("captcha_required") is not False or permission.get("payment_required") is not False:
        return False
    if permission.get("human_approval_required") is not False:
        return False
    if not permission.get("reference") or not valid_evidence(permission, now):
        return False
    submission = site.get("submission_url", "")
    readback = site.get("readback_url", "")
    rules = permission.get("url", "")
    host = _host(submission)
    if not host or _host(rules) != host or _host(readback) != host:
        return False
    selectors = site.get("selectors") or {}
    if not isinstance(selectors, dict) or not all(
        isinstance(selectors.get(k), str) and selectors[k].strip()
        for k in ("title", "description", "submit")
    ):
        return False
    if site.get("content_fit_reviewed") is not True or site.get("dedupe_key") is None:
        return False
    try:
        limit = int(site.get("daily_limit", 0))
    except (TypeError, ValueError):
        return False
    return 1 <= limit <= 3


def _marker(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _page_blocker(page) -> str | None:
    try:
        text = _marker(page.locator("body").inner_text(timeout=2000))
    except Exception:
        text = ""
    return next((marker.replace(" ", "_") for marker in BLOCKERS if marker in text), None)


def run_publisher(con, cfg: dict, now: datetime, *, playwright_factory=None) -> dict:
    """Submit only explicitly approved, unique, one-off destination payloads.

    Candidate discovery never creates a permission record. No configured eligible
    destination means an honest idle result, not an attempted publication.
    """
    p = cfg.get("publishing", {}) if isinstance(cfg, dict) else {}
    if not check_settings(cfg, now):
        return {"status": "disabled_no_browser_permission_config", "published": 0, "attempted": 0}
    sites = p.get("destinations", [])
    if not sites:
        return {"status": "no_eligible_browser_destinations", "published": 0, "attempted": 0}
    eligible = [s for s in sites if isinstance(s, dict) and _approved(s, now)]
    skipped = len(sites) - len(eligible)
    if not eligible:
        return {"status": "no_currently_approved_browser_destinations", "published": 0,
                "attempted": 0, "skipped": skipped}
    try:
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright
            playwright_factory = sync_playwright
    except ImportError:
        return {"status": "playwright_unavailable", "published": 0, "attempted": 0,
                "skipped": skipped}

    published = attempted = failed = blocked = 0
    used_payloads: set[str] = set()
    # No more than one distinct payload per site in a cycle; no cross-site repeats.
    with playwright_factory() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for site in eligible:
                payload = site.get("payload") or {}
                title = str(payload.get("title") or "").strip()
                description = str(payload.get("description") or "").strip()
                product_url = str(payload.get("url") or "").strip()
                if not title or not description:
                    skipped += 1
                    continue
                if product_url:
                    try:
                        safe_url(product_url)
                    except Exception:
                        skipped += 1
                        continue
                    if site.get("link_permission") is not True:
                        skipped += 1
                        continue
                fingerprint = hashlib.sha256((title + "\n" + description + "\n" + product_url).encode()).hexdigest()
                if fingerprint in used_payloads:
                    skipped += 1
                    continue
                used_payloads.add(fingerprint)
                site_id = hashlib.sha256((site["permission"]["url"] + "|" + fingerprint).encode()).hexdigest()
                already = con.execute("SELECT 1 FROM published_content WHERE id=?", (site_id,)).fetchone()
                if already:
                    skipped += 1
                    continue

                page = browser.new_page()
                try:
                    response = page.goto(site["submission_url"], wait_until="domcontentloaded", timeout=20000)
                    if response is None or response.status >= 400:
                        failed += 1
                        continue
                    blocker = _page_blocker(page)
                    if blocker:
                        blocked += 1
                        continue
                    selectors = site["selectors"]
                    title_field = page.locator(selectors["title"])
                    description_field = page.locator(selectors["description"])
                    submit = page.locator(selectors["submit"])
                    if any(x.count() != 1 or not x.is_visible() or not x.is_enabled()
                           for x in (title_field, description_field, submit)):
                        skipped += 1
                        continue
                    title_field.fill(title)
                    description_field.fill(description)
                    if product_url and selectors.get("url"):
                        url_field = page.locator(selectors["url"])
                        if url_field.count() != 1 or not url_field.is_visible() or not url_field.is_enabled():
                            skipped += 1
                            continue
                        url_field.fill(product_url)
                    # Recheck for barriers immediately before the only external write.
                    blocker = _page_blocker(page)
                    if blocker:
                        blocked += 1
                        continue
                    attempted += 1
                    submit.click(timeout=8000)
                    page.wait_for_timeout(1200)
                    if _page_blocker(page):
                        blocked += 1
                        continue
                    verify = browser.new_page()
                    try:
                        check = verify.goto(site["readback_url"], wait_until="domcontentloaded", timeout=15000)
                        if check is None or check.status >= 400:
                            failed += 1
                            continue
                        visible = _marker(verify.locator("body").inner_text(timeout=4000))
                        expected = _marker(title)
                        if expected not in visible or (product_url and _marker(product_url) not in visible):
                            failed += 1
                            continue
                        record = {"project": "HUNTER", "destination": site["submission_url"],
                                  "public_url": site["readback_url"], "content_id": fingerprint,
                                  "verified_at_utc": stamp(now), "verification": "fresh_public_readback_contains_title_and_link"}
                        con.execute("INSERT INTO published_content(id,payload,occurred_at) VALUES(?,?,?)",
                                    (site_id, json.dumps(record, sort_keys=True), stamp(now)))
                        published += 1
                    finally:
                        verify.close()
                except Exception as exc:
                    # Keep logs to exception classes only; do not leak page content.
                    failed += 1
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
                time.sleep(max(1, min(int(site.get("spacing_seconds", 5)), 60)))
        finally:
            browser.close()
    status = "published_verified" if published else "no_verified_publication"
    return {"status": status, "published": published, "attempted": attempted,
            "failed": failed, "blocked": blocked, "skipped": skipped}
