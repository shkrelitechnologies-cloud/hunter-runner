"""HUNTER: private, draft-only opportunity triage. Python 3.12+, standard library.
No publishing, account creation, affiliate application or payment capability exists.
Network sources require current, human-recorded automation permission evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from pathlib import Path

VERSION = "1.0.0"
SCHEMA_VERSION = 1
UA = "HUNTER/1.0 (permission-gated public RSS reader; no publishing)"
TABLES = ("products", "buyer_intents", "sources", "discovered_posts", "response_drafts",
          "affiliate_products", "affiliate_applications", "affiliate_links", "published_content",
          "click_events", "sales", "commissions", "daily_statistics", "execution_logs",
          "configuration_history")
BUYER_WEIGHTS = {"intent": 30, "relevance": 25, "freshness": 15,
                 "purchase": 15, "source_quality": 10, "market": 5}
AFFILIATE_WEIGHTS = {"demand": 25, "quality": 20, "relevance": 20,
                     "economics": 15, "positioning": 10, "credibility": 10}
INTENT = {
    "purchase": ["looking to buy", "want to buy", "willing to pay", "budget for"],
    "recommendation": ["looking for", "recommend", "recommendations", "any suggestions",
                       "where can i find", "can anyone suggest", "does anyone know"],
    "problem": ["struggling with", "need help", "how do i", "how can i", "can't focus",
                "cannot focus", "keep putting off", "having trouble"],
    "comparison": ["which is better", "compared to", "versus", "alternative to"],
}
DEFAULT_NEGATIVE = ["not looking for", "not interested in", "don't need", "do not need",
                    "no self-promotion", "no promotion", "no advertising", "no ads",
                    "buy my", "use my code", "limited time offer", "check out my product"]
SENSITIVE = ["suicide", "suicidal", "self-harm", "self harm", "adhd", "dementia",
             "depression", "medication", "diagnosed", "medical advice", "insomnia"]
TIPS = {
    "productivity": ["Write down one next action small enough to finish in ten minutes.",
                     "Choose one task for today and give it a clear finish line.",
                     "Try a short work period, then note what interrupted it."],
    "self_improvement": ["Choose a small version of the habit and connect it to an existing routine.",
                         "Track whether you started, rather than expecting a perfect result.",
                         "Make the first step easy enough to repeat on a busy day."],
    "cognitive_exercises": ["Try a short distraction-free practice period and record what was difficult.",
                            "Start with a simple recall exercise, then compare with your original notes.",
                            "Keep practice short and consistent rather than making performance claims."],
    "personal_development": ["Define what progress would look like this week, then choose one step.",
                             "Write down the decision, your options and the trade-off that matters most.",
                             "Separate what you can act on today from what needs more information."],
}
STOP = False
LOG = logging.getLogger("hunter")


class HunterError(Exception):
    pass


class SourceError(HunterError):
    def __init__(self, code: str, retry_seconds: int = 3600, permanent: bool = False):
        super().__init__(code)
        self.code, self.retry_seconds, self.permanent = code, retry_seconds, permanent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat(timespec="seconds")


def date(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as error:
        raise HunterError("invalid_timezone_aware_timestamp") from error


def digest(value) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def norm(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split())


def hits(text: str, phrases: list[str]) -> list[str]:
    value = norm(text)
    return [p for p in phrases if re.search(r"(?<!\w)" + re.escape(norm(p)) + r"(?!\w)", value)]


def clean(text: str, limit: int = 500) -> str:
    # Do not retain contact handles, long numeric identifiers or markup in excerpts.
    text = html.unescape(re.sub(r"<[^>]*>", " ", str(text)))
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email removed]", text)
    text = re.sub(r"(?<!\w)\+?[\d ()-]{9,}\d", "[number removed]", text)
    text = re.sub(r"(?i)(?:api[_ -]?key|password|token|secret)\s*[:=]\s*\S+", "[secret removed]", text)
    return " ".join(text.split())[:limit]


def safe_url(value: str, *, network: bool = False, hosts: list[str] | None = None) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise HunterError("invalid_url")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise HunterError("invalid_url")
    if parsed.port not in (None, 80, 443):
        raise HunterError("nonstandard_port_rejected")
    host = parsed.hostname.casefold().rstrip(".")
    if network:
        if parsed.scheme != "https" or not hosts or host not in hosts:
            raise HunterError("network_host_not_allowlisted")
        if host == "cero.gumroad.com" or host.endswith(".cero.gumroad.com"):
            raise HunterError("cero_store_network_reads_disabled")
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise HunterError("nonpublic_network_address")
        except socket.gaierror as error:
            raise SourceError("dns_failure") from error
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def valid_evidence(value: dict, now: datetime, *, require_url: bool = True) -> bool:
    try:
        if value.get("status") != "verified" or not value.get("reference"):
            return False
        if require_url:
            safe_url(value["url"])
        verified, expiry = date(value["verified_at"]), date(value["expires_at"])
        return verified <= now < expiry
    except (HunterError, KeyError, TypeError):
        return False


def validate_config(cfg: dict) -> None:
    if cfg.get("schema_version") != 1:
        raise HunterError("unsupported_config_version")
    safety = cfg.get("safety", {})
    if safety.get("publishing_enabled") is not False or safety.get("human_approval_required") is not True:
        from hunter_publish import PublishError, check_settings
        try:
            if not check_settings(cfg, utcnow()):
                raise HunterError("publishing_must_remain_disabled")
        except PublishError:
            raise HunterError("publishing_must_remain_disabled") from None
    if cfg.get("catalogue_scope") != "cero-six-only":
        raise HunterError("catalogue_scope_must_be_cero_six_only")
    products = cfg.get("products", [])
    if len(products) != 6 or len({p.get("id") for p in products}) != 6:
        raise HunterError("exactly_six_unique_cero_slots_required")
    for product in products:
        if product.get("id") not in {f"cero-{n}" for n in range(1, 7)}:
            raise HunterError("invalid_cero_product_id")
        if not isinstance(product.get("enabled"), bool):
            raise HunterError("product_enabled_must_be_boolean")
        if product["enabled"]:
            required = ("name", "url", "currency", "description", "target_customer")
            if any(not product.get(k) or product[k] == "UNKNOWN" for k in required):
                raise HunterError("enabled_product_facts_missing")
            safe_url(product["url"])
            if not product.get("features") or not product.get("problems_solved") or not product.get("search_phrases"):
                raise HunterError("enabled_product_deliverables_missing")
            if not valid_evidence(product.get("facts_evidence", {}), utcnow(), require_url=False):
                raise HunterError("enabled_product_facts_not_verified")
            if not isinstance(product.get("price_minor"), int) or product["price_minor"] < 0:
                raise HunterError("invalid_price_minor")
            if product.get("topic") not in TIPS:
                raise HunterError("invalid_product_topic")
    sources = cfg.get("sources", [])
    if len(sources) > 20 or len({s.get("id") for s in sources}) != len(sources):
        raise HunterError("invalid_source_ids_or_count")
    for source in sources:
        if not re.fullmatch(r"[a-z0-9_-]{1,48}", source.get("id", "")):
            raise HunterError("invalid_source_id")
        if source.get("kind") not in ("manual", "rss", "hn_api"):
            raise HunterError("unsupported_source_type")
        if not isinstance(source.get("enabled"), bool):
            raise HunterError("source_enabled_must_be_boolean")
        for key, lower, upper in (("max_requests", 1, 3), ("cooldown_seconds", 3600, 604800),
                                  ("quality_score", 0, 10)):
            if not isinstance(source.get(key), int) or not lower <= source[key] <= upper:
                raise HunterError("invalid_source_limits")
        if source["kind"] in ("rss", "hn_api") and source["enabled"]:
            hosts = source.get("allowed_hosts", [])
            if not hosts or urllib.parse.urlsplit(source.get("url", "")).hostname not in hosts:
                raise HunterError("rss_host_allowlist_required")
            if not valid_evidence(source.get("permission", {}), utcnow()):
                raise HunterError("automation_permission_unverified")
            if source["kind"] == "rss" and source["max_requests"] < 2:
                raise HunterError("rss_request_budget_must_include_robots")
            if source["kind"] == "hn_api" and (source["allowed_hosts"] != ["hacker-news.firebaseio.com"] or source.get("drafting_permitted") is not False):
                raise HunterError("hn_api_research_only_required")
    for key in ("qualified", "high_intent"):
        value = cfg.get("thresholds", {}).get(key)
        if not isinstance(value, int) or not 1 <= value <= 100:
            raise HunterError("invalid_score_threshold")
    if cfg["thresholds"]["high_intent"] < cfg["thresholds"]["qualified"]:
        raise HunterError("high_threshold_below_qualified")
    for key, low, high in (("retention_days", 1, 365), ("max_age_days", 1, 30),
                           ("max_posts_per_source", 1, 100)):
        if not isinstance(cfg.get(key), int) or not low <= cfg[key] <= high:
            raise HunterError("invalid_retention_or_post_limit")
    if cfg.get("affiliate", {}).get("enabled") and not valid_evidence(
            cfg["affiliate"].get("policy_evidence", {}), utcnow()):
        raise HunterError("affiliate_policy_unverified_engine_disabled")
    # No secret values or executable extensions belong in configuration.
    for forbidden in ("password", "api_key", "access_token", "private_key", "payment_card"):
        if re.search(r'"' + forbidden + r'"\s*:', json.dumps(cfg), re.I):
            raise HunterError("secret_field_in_config")


SCHEMA = """
CREATE TABLE products(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY, payload TEXT NOT NULL, last_checked TEXT,
 next_allowed TEXT, blocked INTEGER NOT NULL DEFAULT 0, last_status TEXT NOT NULL DEFAULT 'not_checked');
CREATE TABLE discovered_posts(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
 content_hash TEXT NOT NULL UNIQUE, title TEXT NOT NULL, excerpt TEXT NOT NULL,
 published_at TEXT NOT NULL, discovered_at TEXT NOT NULL);
CREATE TABLE buyer_intents(id TEXT PRIMARY KEY, post_id TEXT NOT NULL UNIQUE REFERENCES discovered_posts(id) ON DELETE CASCADE,
 product_id TEXT REFERENCES products(id), classification TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
 components TEXT NOT NULL, explanation TEXT NOT NULL, status TEXT NOT NULL, permission_status TEXT NOT NULL);
CREATE TABLE response_drafts(id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES buyer_intents(id) ON DELETE CASCADE,
 variation INTEGER NOT NULL, body TEXT NOT NULL, product_link TEXT, status TEXT NOT NULL DEFAULT 'awaiting_human_approval',
 UNIQUE(lead_id, variation));
CREATE TABLE affiliate_products(id TEXT PRIMARY KEY, payload TEXT NOT NULL, score INTEGER NOT NULL,
 explanation TEXT NOT NULL, status TEXT NOT NULL, verified_at TEXT);
CREATE TABLE affiliate_applications(product_id TEXT PRIMARY KEY REFERENCES affiliate_products(id),
 status TEXT NOT NULL, invitation_url TEXT, requirements TEXT NOT NULL);
CREATE TABLE affiliate_links(product_id TEXT PRIMARY KEY REFERENCES affiliate_products(id),
 approved_url TEXT, status TEXT NOT NULL, verified_at TEXT, evidence_reference TEXT);
CREATE TABLE published_content(id TEXT PRIMARY KEY, payload TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE click_events(id TEXT PRIMARY KEY, payload TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE sales(id TEXT PRIMARY KEY, kind TEXT NOT NULL, amount_minor INTEGER NOT NULL,
 currency TEXT NOT NULL, evidence_reference TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE commissions(id TEXT PRIMARY KEY, kind TEXT NOT NULL, amount_minor INTEGER NOT NULL,
 currency TEXT NOT NULL, evidence_reference TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE daily_statistics(day TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE execution_logs(id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, operation TEXT NOT NULL,
 source_id TEXT, status TEXT NOT NULL);
CREATE TABLE configuration_history(hash TEXT PRIMARY KEY, seen_at TEXT NOT NULL, payload TEXT NOT NULL);
"""


def open_database(path: Path, initialize: bool = False) -> sqlite3.Connection:
    if not initialize and not path.is_file():
        raise HunterError("database_missing_refusing_empty_start")
    if initialize and path.exists():
        raise HunterError("database_already_exists")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if initialize:
            con.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
        elif version != SCHEMA_VERSION:
            raise HunterError("unsupported_database_migration_restore_compatible_release")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise HunterError("database_integrity_check_failed")
        if con.execute("PRAGMA foreign_key_check").fetchone():
            raise HunterError("database_foreign_key_check_failed")
        return con
    except Exception:
        con.close()
        raise


def record(con: sqlite3.Connection, operation: str, status: str, source: str | None = None):
    # Only bounded machine codes go to logs. No URLs, excerpts or exception bodies.
    con.execute("INSERT INTO execution_logs(occurred_at,operation,source_id,status) VALUES(?,?,?,?)",
                (stamp(), operation, source, status))
    LOG.info("operation=%s status=%s", operation, status)


def restore_guard(directory: Path, initialize: bool):
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise HunterError("state_directory_symlink_rejected")
    db, manifest = directory / "hunter.sqlite", directory / "manifest.json"
    if initialize:
        if db.exists() or manifest.exists():
            raise HunterError("initialization_requires_empty_state")
    else:
        if not manifest.is_file() or not db.is_file() or db.is_symlink():
            raise HunterError("state_missing_refusing_empty_start")
        try:
            content = json.loads(manifest.read_text())
            if content.get("sha256") != digest(db.read_bytes()):
                raise HunterError("state_checksum_mismatch_restore_backup")
        except (ValueError, OSError) as error:
            raise HunterError("state_manifest_invalid") from error
    return db


def backup_database(con: sqlite3.Connection, directory: Path):
    target = directory / "previous.sqlite"
    temporary = directory / "previous.tmp.sqlite"
    temporary.unlink(missing_ok=True)
    with sqlite3.connect(temporary) as destination:
        con.backup(destination)
    os.replace(temporary, target)


def write_manifest(directory: Path):
    content = {"schema_version": SCHEMA_VERSION, "sha256": digest((directory / "hunter.sqlite").read_bytes()),
               "saved_at": stamp(), "version": VERSION}
    temporary = directory / "manifest.tmp.json"
    temporary.write_text(json.dumps(content, indent=2) + "\n")
    os.replace(temporary, directory / "manifest.json")
    (directory / ".hunter-state").write_text("HUNTER private state v1\n")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Redirect destinations need their own permission review; do not follow automatically.
        raise SourceError("redirect_requires_permission_review", permanent=True)


def retry_after(value: str | None, now: datetime) -> int:
    try:
        if value and value.isdigit():
            return max(3600, min(int(value), 604800))
        when = parsedate_to_datetime(value or "")
        return max(3600, min(int((when - now).total_seconds()), 604800))
    except (ValueError, TypeError, OverflowError):
        return 3600


def fetch_bytes(url: str, hosts: list[str], *, max_attempts: int = 2) -> bytes:
    safe_url(url, network=True, hosts=hosts)
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(max_attempts):
        if STOP:
            raise SourceError("shutdown_requested")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA,
                "Accept": "application/rss+xml, application/atom+xml, text/plain, application/xml"})
            with opener.open(request, timeout=15) as response:
                data = response.read(1048577)
                if len(data) > 1048576:
                    raise SourceError("response_too_large", permanent=True)
                return data
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise SourceError("rate_limited", retry_after(error.headers.get("Retry-After"), utcnow())) from None
            if error.code in (401, 403, 451):
                raise SourceError("access_prohibited", permanent=True) from None
            if 500 <= error.code <= 599 and attempt + 1 < max_attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SourceError("http_error", permanent=400 <= error.code < 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt + 1 < max_attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SourceError("network_failure") from None
    raise SourceError("network_failure")


def parse_feed(data: bytes, limit: int) -> list[dict]:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise SourceError("xml_entities_rejected", permanent=True)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise SourceError("invalid_feed") from None
    def local(tag):
        return tag.rsplit("}", 1)[-1]
    posts = []
    for item in root.iter():
        if local(item.tag) not in ("entry", "item"):
            continue
        fields = {}
        for child in item:
            key = local(child.tag)
            if key == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                fields["link"] = child.get("href")
            elif key in ("title", "description", "summary", "content", "pubDate", "published", "updated", "link"):
                fields[key] = " ".join(child.itertext())[:10000]
        raw_date = fields.get("published") or fields.get("pubDate") or fields.get("updated")
        if not raw_date:
            continue
        try:
            try:
                published = date(raw_date)
            except HunterError:
                published = parsedate_to_datetime(raw_date)
                if published.tzinfo is None:
                    continue
            posts.append({"url": fields.get("link", ""), "title": fields.get("title", ""),
                          "text": fields.get("description") or fields.get("summary") or fields.get("content", ""),
                          "published_at": stamp(published), "public_confirmed": True,
                          "language": "UNKNOWN", "region": "UNKNOWN"})
        except (HunterError, ValueError, TypeError):
            continue
        if len(posts) >= limit:
            break
    return posts


def read_rss(source: dict, cfg: dict) -> list[dict]:
    if not valid_evidence(source.get("permission", {}), utcnow()):
        raise SourceError("automation_permission_expired", permanent=True)
    url, hosts = source["url"], source["allowed_hosts"]
    safe_url(url, network=True, hosts=hosts)
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    # robots refusal/unavailability is fail-closed. Robots does not substitute for ToS permission.
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(fetch_bytes(robots_url, hosts, max_attempts=1).decode("utf-8", errors="replace").splitlines())
    if not robots.can_fetch(UA, url):
        raise SourceError("robots_prohibited", permanent=True)
    delay = max(robots.crawl_delay(UA) or 0, source.get("request_spacing_seconds", 2))
    if delay > 30:
        raise SourceError("robots_delay_exceeds_run_budget", retry_seconds=max(3600, delay))
    time.sleep(delay)
    # One robots request plus max_requests feed attempts. Source config accounts for both.
    return parse_feed(fetch_bytes(url, hosts, max_attempts=source["max_requests"] - 1), cfg["max_posts_per_source"])


def read_hn(source: dict, cfg: dict) -> list[dict]:
    """Official API, public Ask HN research only. Never collect authors or user profiles."""
    if not valid_evidence(source.get("permission", {}), utcnow()):
        raise SourceError("automation_permission_expired", permanent=True)
    hosts = ["hacker-news.firebaseio.com"]
    ids = json.loads(fetch_bytes("https://hacker-news.firebaseio.com/v0/askstories.json", hosts, max_attempts=1))
    if not isinstance(ids, list):
        raise SourceError("invalid_api_index")
    posts = []
    for item_id in ids[:min(cfg["max_posts_per_source"], source["max_requests"] - 1)]:
        if not isinstance(item_id, int) or item_id <= 0:
            continue
        time.sleep(source.get("request_spacing_seconds", 2))
        item = json.loads(fetch_bytes(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", hosts, max_attempts=1))
        if not isinstance(item, dict) or item.get("deleted") or item.get("dead") or item.get("type") != "story":
            continue
        posts.append({"url": f"https://news.ycombinator.com/item?id={item_id}",
                      "title": item.get("title", ""), "text": item.get("text", ""),
                      "published_at": stamp(datetime.fromtimestamp(item["time"], timezone.utc)),
                      "public_confirmed": True, "language": "UNKNOWN", "region": "UNKNOWN"})
    return posts


def read_manual(path: Path, limit: int) -> list[dict]:
    if not path.is_file():
        return []
    if path.stat().st_size > 1048576:
        raise SourceError("manual_import_too_large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) > limit:
            raise SourceError("manual_import_invalid_count")
        return data
    except (ValueError, UnicodeError):
        raise SourceError("manual_import_invalid_json") from None


def score_buyer(post: dict, product: dict, source: dict, cfg: dict, now: datetime) -> dict:
    text = str(post.get("title", "")) + " " + str(post.get("text", ""))
    negatives = hits(text, DEFAULT_NEGATIVE + SENSITIVE + cfg["negative_keywords"] + product["negative_keywords"])
    matched = hits(text, product["search_phrases"])
    kinds = [kind for kind, words in INTENT.items() if hits(text, words)]
    age = (now - date(post["published_at"])).total_seconds() / 86400
    reasons = []
    if negatives:
        reasons.append("negative_or_sensitive_context")
    if not matched:
        reasons.append("no_product_relevance")
    if not kinds:
        reasons.append("no_actionable_intent")
    if age < -0.01 or age > cfg["max_age_days"]:
        reasons.append("outside_freshness_window")
    if post.get("public_confirmed") is not True:
        reasons.append("public_status_unconfirmed")
    if post.get("spam_confirmed") is True or post.get("bot_confirmed") is True:
        reasons.append("known_spam_or_bot")
    components = {
        "intent": 30 if "purchase" in kinds else 25 if "recommendation" in kinds else 20 if kinds else 0,
        "relevance": min(25, len(matched) * 10),
        "freshness": 15 if 0 <= age <= 1 else 10 if 0 <= age <= 3 else 5 if 0 <= age <= cfg["max_age_days"] else 0,
        "purchase": 15 if "purchase" in kinds else 5 if "comparison" in kinds else 0,
        "source_quality": source["quality_score"],
        "market": 5 if (post.get("language") in cfg["target_languages"] and
                          (cfg["target_regions"] == ["GLOBAL"] or post.get("region") in cfg["target_regions"])) else 0,
    }
    total = sum(components.values())
    status = "rejected" if reasons else "qualified" if total >= cfg["thresholds"]["qualified"] else "discovered"
    return {"score": total, "components": components, "status": status,
            "classification": "+".join(kinds) or "none",
            "explanation": {"matched_phrases": matched, "rejection_reasons": reasons,
                            "market_unknown": components["market"] == 0,
                            "warning": "Rule-based triage, not a purchase probability or verified need."}}


def response_variations(post: dict, product: dict, source: dict, high: bool) -> list[dict]:
    topic = product["topic"]
    context = clean(post.get("title", ""), 100)
    tips = TIPS[topic]
    variations = []
    link_allowed = (source.get("promotion_permission") == "explicitly_permitted" and
                    valid_evidence(source.get("promotion_evidence", {}), utcnow()) and
                    product.get("promotional_guidelines", {}).get("include_link") is True)
    for index in range(3 if high else 1):
        openings = [f'On your question about "{context}": ',
                    f'For "{context}", one small experiment is this: ',
                    f'You mentioned "{context}". A starting point: ']
        body = openings[index] + tips[index]
        if link_allowed:
            # Only the supplied factual description, not invented features or outcomes.
            body += " A related option from our Cero catalogue is " + product["name"] + ". " + product["description"]
            body += " Consider whether its listed contents fit your needs before buying."
        variations.append({"body": body, "product_link": product["url"] if link_allowed else None})
    return variations


def ingest_post(con, post: dict, source: dict, cfg: dict, now: datetime):
    if not isinstance(post, dict):
        raise HunterError("post_not_object")
    url = safe_url(post.get("url", ""))
    if urllib.parse.urlsplit(url).hostname == "cero.gumroad.com":
        raise HunterError("store_page_not_discussion")
    if source["kind"] == "rss" and urllib.parse.urlsplit(url).hostname not in source["allowed_hosts"]:
        raise HunterError("post_link_outside_source_hosts")
    if len(str(post.get("text", ""))) > 10000:
        raise HunterError("post_text_too_large")
    published = date(post.get("published_at", ""))
    if (now - published).days > cfg["retention_days"]:
        return
    title, excerpt = clean(post.get("title", ""), 200), clean(post.get("text", ""))
    if not title or not excerpt:
        raise HunterError("post_title_and_text_required")
    content_hash = digest(norm(title + " " + excerpt))
    post_id = digest(url)
    inserted = con.execute("INSERT OR IGNORE INTO discovered_posts VALUES(?,?,?,?,?,?,?,?)",
        (post_id, source["id"], url, content_hash, title, excerpt, stamp(published), stamp(now))).rowcount
    if not inserted:
        return
    enabled = [p for p in cfg["products"] if p["enabled"]]
    if not enabled:
        return
    matches = [(p, score_buyer(post, p, source, cfg, now)) for p in enabled]
    product, result = max(matches, key=lambda pair: (pair[1]["status"] != "rejected", pair[1]["score"]))
    lead_id = digest([post_id, product["id"]])
    con.execute("INSERT INTO buyer_intents VALUES(?,?,?,?,?,?,?,?,?)",
        (lead_id, post_id, product["id"], result["classification"], result["score"],
         json.dumps(result["components"]), json.dumps(result["explanation"]), result["status"],
         "research_only_generated_replies_prohibited" if source.get("drafting_permitted") is False else "not_approved_for_publication"))
    if result["status"] == "qualified" and source.get("drafting_permitted", True):
        for i, draft in enumerate(response_variations(post, product, source,
                                result["score"] >= cfg["thresholds"]["high_intent"])):
            con.execute("INSERT INTO response_drafts(id,lead_id,variation,body,product_link) VALUES(?,?,?,?,?)",
                        (digest([lead_id, i]), lead_id, i + 1, draft["body"], draft["product_link"]))


def affiliate_verification(item: dict, cfg: dict, now: datetime) -> dict:
    # No rate, eligibility, account relationship or link is inferred from listing copy.
    parts = {key: 0 for key in AFFILIATE_WEIGHTS}
    reasons = []
    if not cfg["affiliate"]["enabled"] or not valid_evidence(cfg["affiliate"].get("policy_evidence", {}), now):
        return {"status": "disabled_policy_unverified", "score": 0, "components": parts,
                "reasons": ["Current Gumroad policy verification is required before activation."],
                "commission_percentage": "UNKNOWN", "approved_url": None}
    safe_url(item.get("url", ""))
    if not item.get("name") or not item.get("creator"):
        reasons.append("identity_missing")
    evidence = item.get("evidence", {})
    for key, weight in AFFILIATE_WEIGHTS.items():
        proof = evidence.get(key, {})
        if valid_evidence(proof, now) and proof.get("assessment") == "pass":
            parts[key] = weight
    programme = valid_evidence(item.get("programme_evidence", {}), now)
    relationship = valid_evidence(item.get("relationship_evidence", {}), now)
    link_proof = valid_evidence(item.get("tracking_link_evidence", {}), now)
    commission = "UNKNOWN"
    if programme and valid_evidence(item.get("commission_evidence", {}), now):
        try:
            rate = Decimal(str(item["commission_percentage"]))
            if not rate.is_finite() or not 0 < rate <= 100:
                raise InvalidOperation
            commission = str(rate)
        except (InvalidOperation, KeyError):
            reasons.append("commission_invalid")
    if commission == "UNKNOWN":
        parts["economics"] = 0
    if item.get("suspicious") is True or item.get("quality_rejected") is True:
        return {"status": "rejected", "score": 0, "components": parts, "reasons": ["quality_or_safety_rejection"],
                "commission_percentage": commission, "approved_url": None}
    tracking = item.get("approved_affiliate_url")
    if tracking:
        safe_url(tracking)
        if tracking == item["url"]:
            reasons.append("ordinary_product_url_not_tracking_url")
            link_proof = False
    approved = bool(programme and relationship and link_proof and tracking and not reasons)
    status = "verified_relationship" if approved else "application_review_needed" if programme else "unverified"
    return {"status": status, "score": sum(parts.values()), "components": parts, "reasons": reasons,
            "commission_percentage": commission, "approved_url": tracking if approved else None}


def ingest_affiliate(con, item: dict, cfg: dict, now: datetime):
    result = affiliate_verification(item, cfg, now)
    product_id = digest(item.get("url", ""))
    retained = {key: item.get(key) for key in ("name", "creator", "url", "description", "features",
                                              "application_requirements", "invitation_url", "commission_type")}
    retained["commission_percentage"] = result["commission_percentage"]
    retained["content_drafts"] = affiliate_content(item, result, cfg)
    con.execute("INSERT OR REPLACE INTO affiliate_products VALUES(?,?,?,?,?,?)",
                (product_id, json.dumps(retained), result["score"], json.dumps(result), result["status"], stamp(now)))
    con.execute("INSERT OR REPLACE INTO affiliate_applications VALUES(?,?,?,?)",
                (product_id, "not_submitted", item.get("invitation_url"), clean(item.get("application_requirements", "UNKNOWN"))))
    con.execute("INSERT OR REPLACE INTO affiliate_links VALUES(?,?,?,?,?)",
                (product_id, result["approved_url"], "verified" if result["approved_url"] else "UNKNOWN",
                 stamp(now) if result["approved_url"] else None,
                 item.get("tracking_link_evidence", {}).get("reference") if result["approved_url"] else None))


def affiliate_content(item: dict, result: dict, cfg: dict) -> dict:
    """Generate internal content only for supplied, verified affiliate relationships."""
    if result["status"] != "verified_relationship" or not result["approved_url"]:
        return {}
    description = clean(item.get("description", ""), 400)
    features = [clean(value, 100) for value in item.get("features", [])[:8]]
    if not description or not features or not valid_evidence(item.get("content_evidence", {}), utcnow()):
        return {}
    name = clean(item["name"], 100)
    disclosure = cfg["affiliate"]["disclosure"]
    facts = description + " Included: " + "; ".join(features) + "."
    ending = " " + disclosure + " " + result["approved_url"]
    return {
        "curated_recommendation": name + ": " + facts + ending,
        "educational_outline": "Discuss the audience's stated problem, then compare its needs with these supplied deliverables: " + "; ".join(features) + ending,
        "social_draft": name + ": " + description + ending,
        "contextual_answer_draft": "Start by addressing the specific question. If the supplied deliverables fit, consider " + name + ". " + facts + ending,
        "search_description_draft": name + " - " + facts + ending,
        "comparison_entry": name + ": " + facts + " No comparative superiority is claimed." + ending,
        "status": "internal_drafts_not_approved_for_publication",
    }


def commission_minor(sale_minor: int, rate: str) -> int:
    percentage = Decimal(rate)
    if not isinstance(sale_minor, int) or sale_minor < 0 or not percentage.is_finite() or not 0 <= percentage <= 100:
        raise HunterError("invalid_commission_inputs")
    return int((Decimal(sale_minor) * percentage / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def import_ledger(con, path: Path):
    # Only operator-reviewed statement facts, no customer identities. Never called from feeds.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "kind", "amount_minor", "currency", "occurred_at", "evidence_reference", "verified"}
        if not required.issubset(reader.fieldnames or []):
            raise HunterError("ledger_columns_missing")
        for index, row in enumerate(reader):
            if index >= 1000 or row.get("verified") != "true" or not row.get("evidence_reference"):
                raise HunterError("ledger_unverified_or_too_large")
            kind = row["kind"]
            if kind not in ("order_confirmed", "commission_confirmed", "revenue_received", "commission_received"):
                raise HunterError("unsupported_ledger_event")
            amount = int(row["amount_minor"])
            if amount < 0 or not re.fullmatch(r"[A-Z]{3}", row["currency"]):
                raise HunterError("invalid_ledger_amount_or_currency")
            date(row["occurred_at"])
            table = "commissions" if kind.startswith("commission_") else "sales"
            existing = con.execute(f"SELECT kind,amount_minor,currency,evidence_reference,occurred_at FROM {table} WHERE id=?",
                                   (row["id"],)).fetchone()
            value = (kind, amount, row["currency"], row["evidence_reference"], row["occurred_at"])
            if existing and existing != value:
                raise HunterError("conflicting_ledger_event_id")
            con.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?)", (row["id"], *value))


def md(value) -> str:
    text = html.escape(str(value), quote=True).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", text)


def statistics(con, cfg: dict) -> dict:
    count = lambda table, condition="1=1": con.execute(f"SELECT count(*) FROM {table} WHERE {condition}").fetchone()[0]
    result = {"posts_discovered": count("discovered_posts"), "qualified_leads": count("buyer_intents", "status='qualified'"),
              "high_intent_leads": con.execute("SELECT count(*) FROM buyer_intents WHERE status='qualified' AND score>=?",
                                               (cfg["thresholds"]["high_intent"],)).fetchone()[0],
              "drafts_awaiting_approval": count("response_drafts", "status='awaiting_human_approval'"),
              "affiliate_candidates": count("affiliate_products"),
              "verified_affiliate_relationships": count("affiliate_products", "status='verified_relationship'"),
              "affiliate_applications_pending": count("affiliate_applications", "status='not_submitted'"),
              "published_content": count("published_content"), "approved_promotions": 0,
              "tracked_clicks": "UNKNOWN - no authorized click reporting source",
              "confirmed_orders": count("sales", "kind='order_confirmed'"),
              "confirmed_commissions": count("commissions", "kind='commission_confirmed'"),
              "received_revenue": {}, "received_commissions": {},
              "revenue_status": "UNKNOWN unless imported verified received events exist",
              "last_execution_utc": stamp(), "next_expected_window_utc": stamp(utcnow() + timedelta(hours=6)),
              "scheduling_note": "Expected cadence only; GitHub schedules can be delayed or inactive.",
              "sources": [{"id": row[0], "status": row[1]} for row in con.execute("SELECT id,last_status FROM sources")],
              "active_products": sum(p["enabled"] for p in cfg["products"]),
              "affiliate_enabled": cfg["affiliate"]["enabled"]}
    for table, kind, key in (("sales", "revenue_received", "received_revenue"),
                             ("commissions", "commission_received", "received_commissions")):
        for currency, amount in con.execute(f"SELECT currency,sum(amount_minor) FROM {table} WHERE kind=? GROUP BY currency", (kind,)):
            result[key][currency] = {"amount_minor": amount, "basis": "verified_manual_statement_import"}
    publisher = con.execute("SELECT status FROM execution_logs WHERE operation='publisher' ORDER BY id DESC LIMIT 1").fetchone()
    result["publisher_status"] = publisher[0] if publisher else "not_enabled"
    directory = con.execute("SELECT status FROM execution_logs WHERE operation='directory_summary' ORDER BY id DESC LIMIT 1").fetchone()
    result["directory_last_run"] = directory[0] if directory else "not_enabled"
    discovery = con.execute("SELECT status FROM execution_logs WHERE operation='discovery_summary' ORDER BY id DESC LIMIT 1").fetchone()
    result["discovery_last_run"] = discovery[0] if discovery else "not_enabled"
    return result


def write_report(con, cfg: dict, directory: Path):
    stats = statistics(con, cfg)
    lines = ["# HUNTER private opportunity report", "", "Private research and publication receipts. Affiliate applications are never submitted.",
             "No sales or revenue are inferred from opportunities. Amounts are in currency minor units.", "",
             "## Status", ""]
    for key, value in stats.items():
        lines.append(f"- {md(key)}: {md(json.dumps(value) if isinstance(value, (dict, list)) else value)}")
    lines += ["", "## Highest-priority direct-product opportunities", ""]
    leads = con.execute("SELECT b.id,b.score,p.title,p.url,b.explanation FROM buyer_intents b JOIN discovered_posts p ON p.id=b.post_id WHERE b.status='qualified' ORDER BY b.score DESC,p.discovered_at DESC LIMIT 20").fetchall()
    if not leads:
        lines.append("No qualified opportunities recorded. Disabled products/sources are not proof of no demand.")
    for lead_id, score, title, url, explanation in leads:
        lines += [f"### {md(title)}", f"Score: {score}/100. Source URL (untrusted text): {md(url)}",
                  f"Explanation: {md(explanation)}", "Publication: NOT APPROVED."]
        for variation, body, link in con.execute("SELECT variation,body,product_link FROM response_drafts WHERE lead_id=? ORDER BY variation", (lead_id,)):
            lines += [f"- Draft {variation}: {md(body)}", f"  Suggested link: {md(link or 'omitted - permission not established')}"]
    lines += ["", "## Affiliate review", "", "Current policy status: " + md(cfg["affiliate"]["policy_evidence"]["status"]),
              "Official policy verification and existing relationship/link evidence are required. No signup or application is sent."]
    for payload, score, explanation, status in con.execute("SELECT payload,score,explanation,status FROM affiliate_products ORDER BY score DESC LIMIT 20"):
        item = json.loads(payload)
        lines += [f"- {md(item.get('name', 'UNKNOWN'))}: {score}/100; {md(status)}; {md(explanation)}"]
    lines += ["", "## Limits", "", "- Scores are transparent rules, not forecasts or proof of buyer identity.",
              "- Reply drafts use topic-specific guidance and quoted question titles; human review is required for fit and tone.",
              "- Bot/spam detection is conservative keyword/explicit-flag triage, not reliable authorship detection.",
              "- Gumroad public affiliate policy was checked; no private affiliate account or payment eligibility was checked. Automated Gumroad discovery remains unavailable without documented permitted access.",
              "- Current-row retention does not erase private Git history or existing backup commits."]
    report_dir = directory / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    con.execute("INSERT OR REPLACE INTO daily_statistics VALUES(?,?)", (utcnow().date().isoformat(), json.dumps(stats)))
    # Terminal dashboard deliberately contains counts/status only, never lead text/URLs or revenue values.
    print(json.dumps({k: stats[k] for k in ("posts_discovered", "qualified_leads", "drafts_awaiting_approval",
                                           "affiliate_candidates", "active_products", "affiliate_enabled")}, sort_keys=True))


def sync_configuration(con, cfg: dict):
    for product in cfg["products"]:
        con.execute("INSERT INTO products VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (product["id"], json.dumps(product)))
    for source in cfg["sources"]:
        con.execute("INSERT INTO sources(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (source["id"], json.dumps(source)))
    con.execute("INSERT OR IGNORE INTO configuration_history VALUES(?,?,?)", (digest(cfg), stamp(), json.dumps(cfg)))


def run_sources(con, cfg: dict, inbox: Path):
    now = utcnow()
    for source in cfg["sources"]:
        if STOP:
            record(con, "shutdown", "requested")
            break
        row = con.execute("SELECT next_allowed,blocked FROM sources WHERE id=?", (source["id"],)).fetchone()
        if not source["enabled"]:
            con.execute("UPDATE sources SET last_status='disabled' WHERE id=?", (source["id"],))
            continue
        if row[1] or (row[0] and date(row[0]) > now):
            continue
        # Isolate every source in a SAVEPOINT. One malformed post cannot lose another source's work.
        try:
            if source["kind"] == "manual":
                posts = read_manual(inbox / "posts.json", cfg["max_posts_per_source"])
            elif source["kind"] == "hn_api":
                posts = read_hn(source, cfg)
            else:
                posts = read_rss(source, cfg)
            for post in posts:
                con.execute("SAVEPOINT post_item")
                try:
                    ingest_post(con, post, source, cfg, now)
                    con.execute("RELEASE post_item")
                except (HunterError, ValueError, TypeError, KeyError):
                    con.execute("ROLLBACK TO post_item")
                    con.execute("RELEASE post_item")
                    record(con, "post_rejected", "invalid_input", source["id"])
            status, wait, blocked = "success", source["cooldown_seconds"], 0
        except SourceError as error:
            status, wait, blocked = error.code, error.retry_seconds, int(error.permanent)
        except (HunterError, OSError, ValueError, TypeError):
            status, wait, blocked = "source_input_failure", source["cooldown_seconds"], 0
        con.execute("UPDATE sources SET last_checked=?,next_allowed=?,blocked=?,last_status=? WHERE id=?",
                    (stamp(now), stamp(now + timedelta(seconds=wait)), blocked, status, source["id"]))
        record(con, "source_checked", status, source["id"])
    if cfg["affiliate"]["enabled"] and not STOP:
        last = con.execute("SELECT max(occurred_at) FROM execution_logs WHERE operation='affiliate_import' AND status='completed'").fetchone()[0]
        if last and (now - date(last)).total_seconds() < cfg["automation"]["affiliate_interval_hours"] * 3600:
            return
        try:
            for item in read_manual(inbox / "affiliate_candidates.json", cfg["max_posts_per_source"]):
                con.execute("SAVEPOINT affiliate_item")
                try:
                    ingest_affiliate(con, item, cfg, now)
                    con.execute("RELEASE affiliate_item")
                except (HunterError, ValueError, TypeError, KeyError):
                    con.execute("ROLLBACK TO affiliate_item")
                    con.execute("RELEASE affiliate_item")
                    record(con, "affiliate_rejected", "invalid_input")
            record(con, "affiliate_import", "completed")
        except (SourceError, OSError):
            record(con, "affiliate_import", "input_failure")
    else:
        record(con, "affiliate", "disabled_missing_verified_relationship")


def apply_retention(con, cfg: dict):
    cutoff = stamp(utcnow() - timedelta(days=cfg["retention_days"]))
    con.execute("DELETE FROM discovered_posts WHERE discovered_at<?", (cutoff,))
    con.execute("DELETE FROM execution_logs WHERE occurred_at<?", (cutoff,))
    con.execute("DELETE FROM configuration_history WHERE seen_at<?", (cutoff,))
    # Financial evidence is deliberately retained, not silently deleted with discovery data.


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--inbox", type=Path, default=Path("inbox"))
    parser.add_argument("--initialize", action="store_true", help="Explicit FIRST database creation only")
    parser.add_argument("--import-ledger", type=Path)
    parser.add_argument("--reset-source", help="Clear a persisted source block after a fresh permission review")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    con = None
    try:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        validate_config(cfg)
        db = restore_guard(args.state_dir, args.initialize)
        con = open_database(db, args.initialize)
        if not args.initialize:
            backup_database(con, args.state_dir)
        with con:
            sync_configuration(con, cfg)
            if args.reset_source:
                source = next((s for s in cfg["sources"] if s["id"] == args.reset_source), None)
                if not source or (source["kind"] in ("rss", "hn_api") and not valid_evidence(source.get("permission", {}), utcnow())):
                    raise HunterError("source_reset_requires_current_permission")
                con.execute("UPDATE sources SET blocked=0,next_allowed=NULL WHERE id=?", (args.reset_source,))
            record(con, "run", "started")
            if args.import_ledger:
                import_ledger(con, args.import_ledger)
            if cfg["buyer"]["enabled"]:
                run_sources(con, cfg, args.inbox)
            elif cfg["affiliate"]["enabled"]:
                affiliate_cfg = dict(cfg, sources=[])
                run_sources(con, affiliate_cfg, args.inbox)
            if cfg.get("discovery", {}).get("enabled"):
                from hunter_discovery import DiscoveryError, run_discovery
                try:
                    discovery_result = run_discovery(con, cfg, utcnow(), args.inbox)
                    record(con, "discovery_summary", json.dumps(discovery_result, sort_keys=True))
                except DiscoveryError as error:
                    record(con, "discovery_summary", error.args[0])
            if cfg.get("directories", {}).get("enabled"):
                from hunter_directories import DirectoryError, run_directories
                try:
                    directory_result = run_directories(con, cfg, utcnow())
                    record(con, "directory_summary", json.dumps(directory_result, sort_keys=True))
                except DirectoryError as error:
                    record(con, "directory_summary", error.args[0])
            if cfg.get("publishing", {}).get("enabled"):
                from hunter_publish import PublishError, run_publisher
                try:
                    result = run_publisher(con, cfg, utcnow())
                    record(con, "publisher", result["status"])
                except PublishError as error:
                    # Codes are controlled by the connector. Never print API responses or secrets.
                    record(con, "publisher", error.args[0])
            apply_retention(con, cfg)
            write_report(con, cfg, args.state_dir)
            record(con, "run", "interrupted_safely" if STOP else "completed")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise HunterError("post_run_integrity_failed")
        con.close()
        con = None
        write_manifest(args.state_dir)
        return 0
    except (HunterError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        # Do not print arbitrary external input or exception strings to public job logs.
        LOG.error("operation=run status=failed_safely consult_private_state_and_configuration")
        return 2
    finally:
        if con is not None:
            con.close()


def handle_signal(signum, frame):
    global STOP
    STOP = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    raise SystemExit(main())
