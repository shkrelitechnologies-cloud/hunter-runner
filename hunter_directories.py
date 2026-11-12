"""Explicitly invited, zero-spend product-directory APIs only.

No arbitrary forms, signups, votes, DMs, browser cookies or new destinations.
Adding a connector needs a separate current rules/fit review and code push.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

ENDPOINTS = {"launchpad": "https://techtools.cz/launchpad-api/tools",
             "develif": "https://develif.com/api/submit",
             "curlship": "https://curlship.com/api/submit"}
DOCS = {"launchpad": "https://techtools.cz/launchpad-api/docs",
        "develif": "https://develif.com/docs",
        "curlship": "https://curlship.com/terms"}
FIT = {"launchpad": {"cero-1": "productivity", "cero-5": "business"},
       "develif": {"cero-1": "Productivity"}, "curlship": {"cero-1": "productivity"}}


class DirectoryError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_json(url, body=None):
    u = urllib.parse.urlsplit(url)
    paths = {"techtools.cz": r"/launchpad-api/tools(?:/\d+)?", "develif.com": r"/api/submit",
             "curlship.com": r"/api/(?:submit|listings|listing/\d+)"}
    if (u.scheme != "https" or u.hostname not in paths or u.username or u.password
            or u.port not in (None,443) or not re.fullmatch(paths.get(u.hostname, "!"),u.path)):
        raise DirectoryError("endpoint_not_allowlisted")
    headers = {"Accept": "application/json", "User-Agent": "Hunter product-directory client/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(url, data=data, headers=headers), timeout=20) as r:
            raw = r.read(2_000_001)
            if len(raw) > 2_000_000:
                raise DirectoryError("response_too_large")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        if error.code == 409:
            return {"duplicate": True}
        if error.code == 429:
            raise DirectoryError("rate_limited_stop") from None
        raise DirectoryError("http_" + str(error.code)) from None
    except (OSError, ValueError):
        raise DirectoryError("ambiguous_network_or_response") from None


def settings_valid(cfg, now):
    d = cfg.get("directories", {})
    if not d.get("enabled"):
        return False
    if not d.get("authority_reference") or d.get("contact_email") != cfg.get("publishing", {}).get("identity"):
        raise DirectoryError("directory_authority_or_identity_missing")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", d.get("contact_email", "")):
        raise DirectoryError("contact_email_invalid")
    for site in d.get("connectors", []):
        if site not in ENDPOINTS:
            raise DirectoryError("unreviewed_connector")
        proof = d.get("policy_evidence", {}).get(site, {})
        try:
            expiry = datetime.fromisoformat(proof["expires_at"])
            if expiry.tzinfo is None or expiry <= now or proof.get("status") != "verified" or proof.get("url") != DOCS[site]:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise DirectoryError("directory_policy_expired") from None
    return True


def payload(site, product, email):
    if product.get("id") not in FIT[site] or not product.get("enabled"):
        raise DirectoryError("product_not_a_directory_fit")
    u = urllib.parse.urlsplit(product.get("url", ""))
    if u.scheme != "https" or u.hostname != "cero.gumroad.com" or not u.path.startswith("/l/"):
        raise DirectoryError("product_link_invalid")
    name, description = product["name"], product["description"]
    if not (2 <= len(name) <= 100 and 30 <= len(description) <= 2000):
        raise DirectoryError("product_text_invalid")
    if site == "curlship":
        return {"url": product["url"], "email": email}
    common = {"name": name, "url": product["url"], "description": description, "category": FIT[site][product["id"]]}
    if site == "launchpad":
        # A truthful bot name, not a fake human and not an invented maker identity.
        common.update({"tagline": description.split(". ")[0][:150], "submitted_by": "Hunter"})
    else:
        common.update({"email": email, "locale": "en"})
    return common


def log(con, now, operation, site, pid, status):
    con.execute("INSERT INTO execution_logs(occurred_at,operation,source_id,status) VALUES(?,?,?,?)",
                (now.isoformat(), operation, site + ":" + pid, status))


def run_directories(con, cfg, now, *, api=api_json):
    result = {"sites_attempted": 0, "attempted": 0, "submitted": 0, "existing": 0, "failed": 0, "pending_verification": 0}
    if not settings_valid(cfg, now):
        return dict(result, status="disabled")
    d = cfg["directories"]
    for site in d["connectors"]:
        attempted_site = False
        for p in cfg["products"]:
            pid = p["id"]
            if pid not in FIT[site] or not p.get("enabled"):
                continue
            receipt_id = "directory-" + site + "-" + pid
            if con.execute("SELECT 1 FROM published_content WHERE id=?", (receipt_id,)).fetchone():
                continue
            prior = con.execute("SELECT status FROM execution_logs WHERE operation='directory_attempt' AND source_id=? ORDER BY id DESC LIMIT 1",
                                (site + ":" + pid,)).fetchone()
            if prior:
                # Failed or ambiguous writes are not automatically repeated. Reconcile first.
                continue
            proof = p.get("facts_evidence", {})
            try:
                if proof.get("status") != "verified" or datetime.fromisoformat(proof["expires_at"]) <= now:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                log(con, now, "directory_skipped", site, pid, "product_facts_expired")
                continue
            attempted_site = True
            result["attempted"] += 1
            log(con, now, "directory_attempt", site, pid, "started")
            try:
                body = payload(site, p, d["contact_email"])
                if site == "curlship":
                    lookup = api("https://curlship.com/api/listings?"+urllib.parse.urlencode({"q":"cero.gumroad.com"}))
                    if lookup.get("ok") is not True or not isinstance(lookup.get("listings"),list):
                        raise DirectoryError("lookup_not_verified")
                    existing = lookup["listings"]
                    if existing and any(x.get("url", "").rstrip("/") != p["url"].rstrip("/") for x in existing):
                        raise DirectoryError("domain_already_listed_other_product")
                    if existing:
                        response = {"listing":existing[0],"duplicate":True}
                    else:
                        response=api(ENDPOINTS[site],body)
                    item=response.get("listing",{})
                    if response.get("duplicate") and not item:
                        raise DirectoryError("duplicate_requires_reconciliation")
                    if not isinstance(item.get("id"),int) or item.get("url", "").rstrip("/") != p["url"].rstrip("/"):
                        raise DirectoryError("listing_not_confirmed")
                    link="https://curlship.com/api/listing/"+str(item["id"])
                    check=api(link)
                    verified=check.get("listing",{})
                    if (check.get("ok") is not True or verified.get("tier")!="free"
                            or verified.get("url", "").rstrip("/")!=p["url"].rstrip("/")
                            or not verified.get("title") or not verified.get("description")):
                        raise DirectoryError("listing_readback_failed")
                elif site == "launchpad":
                    lookup = api(ENDPOINTS[site] + "?" + urllib.parse.urlencode({"url": p["url"]}))
                    items = lookup.get("data", [])
                    if lookup.get("success") is not True or not isinstance(items, list):
                        raise DirectoryError("lookup_not_verified")
                    if items:
                        response = {"data": items[0], "duplicate": True}
                    else:
                        response = api(ENDPOINTS[site], body)
                        if response.get("duplicate"):
                            existing = api(ENDPOINTS[site] + "?" + urllib.parse.urlencode({"url": p["url"]}))
                            if not existing.get("data"):
                                raise DirectoryError("duplicate_not_reconciled")
                            response["data"] = existing["data"][0]
                    item = response.get("data", {})
                    if item.get("url", "").rstrip("/").lower() != p["url"].rstrip("/").lower() or not isinstance(item.get("id"), int):
                        raise DirectoryError("listing_not_confirmed")
                    check = api(ENDPOINTS[site] + "/" + str(item["id"]))
                    if check.get("data", {}).get("url", "").rstrip("/").lower() != p["url"].rstrip("/").lower():
                        raise DirectoryError("listing_readback_failed")
                    link = response.get("share_url") or item.get("share_url") or check.get("data", {}).get("share_url")
                    if urllib.parse.urlsplit(link or "").hostname != "techtools.cz":
                        raise DirectoryError("listing_link_unverified")
                else:
                    response = api(ENDPOINTS[site], body)
                    if response.get("duplicate"):
                        log(con, now, "directory_attempt", site, pid, "existing_domain_not_resubmitted")
                        result["existing"] += 1
                        continue
                    # Documentation does not promise a response schema. Preserve a receipt for review,
                    # but do not claim published without a real returned listing URL and content check.
                    links = []
                    def collect(value):
                        if isinstance(value, dict):
                            for child in value.values():
                                collect(child)
                        elif isinstance(value, list):
                            for child in value:
                                collect(child)
                        elif isinstance(value, str):
                            u = urllib.parse.urlsplit(value)
                            if u.scheme == "https" and u.hostname == "develif.com" and u.path.startswith("/s/") and not u.query:
                                links.append(value)
                    collect(response)
                    log(con, now, "directory_response", site, pid, "accepted_needs_readback")
                    # Response schemas can change. Store only observed public links and key names,
                    # never arbitrary response bodies or management tokens in Git state.
                    con.execute("INSERT OR IGNORE INTO configuration_history VALUES(?,?,?)", (
                        "directory-response-" + receipt_id, now.isoformat(), json.dumps({
                            "public_links": links, "response_keys": sorted(response.keys())})))
                    result["pending_verification"] += 1
                    log(con, now, "directory_attempt", site, pid, "accepted_pending_live_verification")
                    continue
                con.execute("INSERT INTO published_content VALUES(?,?,?)", (receipt_id, json.dumps({
                    "connector": site, "product_id": pid, "url": link, "product_url": p["url"],
                    "status": "existing" if response.get("duplicate") else "submitted"}), now.isoformat()))
                key = "existing" if response.get("duplicate") else "submitted"
                result[key] += 1
                log(con, now, "directory_attempt", site, pid, key)
            except DirectoryError as error:
                result["failed"] += 1
                log(con, now, "directory_attempt", site, pid, error.args[0])
                if "rate_limited" in error.args[0] or error.args[0] in ("http_401", "http_403"):
                    break
        result["sites_attempted"] += int(attempted_site)
    return dict(result, status="completed")
