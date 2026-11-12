"""Blogger publishing for Hunter's own blog. No replies, DMs or arbitrary sites.

A finite catalogue: one original guide per product, at most one per 24 hours.
OAuth credentials come only from HUNTER_BLOGGER_OAUTH, never config or logs.
"""
import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SCOPE = "https://www.googleapis.com/auth/blogger"

GUIDES = {
    "cero-1": ("A daily planning routine that fits in one Notion workspace", [
        "Start with the outcome, not a long task list. Pick one result that would make today useful, then write the next action that moves it forward.",
        "Keep projects and today's tasks separate. A project may take weeks; today's list should contain actions you can actually start. Leave room for interruptions instead of filling every hour.",
        "For habits, track completion rather than judging yourself. At the end of the day, move unfinished work deliberately and remove tasks that no longer matter."]),
    "cero-2": ("Using a nighttime affirmation audio as a reflection routine", [
        "A nighttime reflection routine can be simple: put away work, choose a comfortable listening volume, and decide whether you want audio or quiet.",
        "Treat affirmations as prompts to reflect on your attitudes toward money, not as evidence that money will arrive. A phrase about confidence can lead to a practical question: what small financial action can I take tomorrow?",
        "Keep that action grounded in your circumstances. Reviewing a budget or writing down a question is different from expecting an audio track to change your income. Skip any routine that makes it harder to rest."]),
    "cero-3": ("A practical way to approach a seven-day focus challenge", [
        "Before starting a focus challenge, pick one ordinary task you want to give more attention to. Choose a small daily practice that fits your schedule rather than attempting to change every habit at once.",
        "Write down what you tried and what interrupted you. Reduce one avoidable distraction, then compare your own notes at the end of the week. Do not mistake a good day for proof of a lasting cognitive change.",
        "A workbook can provide structure, but it is not a clinical assessment or treatment. Adjust the exercises to your needs, and judge the routine by whether it is useful in your daily work."]),
    "cero-4": ("How to turn a business or discipline essay into a useful note", [
        "Read one idea at a time. After an essay or letter, write its main claim in your own words and note which part is relevant to a decision you actually face.",
        "Separate an interesting argument from a fact you have checked. Before applying advice about health, money, or strategy, consider the source, the evidence, and the possible cost of being wrong.",
        "Choose a small, reversible experiment when an idea fits. Record what you expected and what happened. You do not need to accept an author's entire worldview to find a useful question in their writing."]),
    "cero-5": ("Choosing a startup acquisition channel without posting everywhere", [
        "Start with the audience you can help and the problem your product addresses. A directory of communities is a research aid, not an invitation to send the same pitch everywhere.",
        "For each channel, check who participates, what they discuss, whether promotion is welcome, and which posting rules apply. Read before contributing. Skip a community if the product is not a clear fit.",
        "Try a small number of relevant channels and record useful responses, not only clicks. Adapt the message to the context, respect refusals, and never treat a list of links as permission for mass outreach."]),
    "cero-6": ("Getting started on a task when you are stuck overthinking", [
        "Name the smallest visible action. Opening the document or writing one rough sentence is easier to start than a vague instruction to finish an entire project.",
        "Remove one source of friction before you begin. Put the material you need within reach and decide what a short first work period will cover. You can reassess after that first step.",
        "A motivational audio can be a cue to start, but it cannot promise to remove procrastination forever. If a routine does not help, change the routine rather than treating a difficult day as a personal failure."]),
}


class PublishError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, token=None, body=None, form=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.googleapis.com", "oauth2.googleapis.com"):
        raise PublishError("endpoint_not_allowlisted")
    headers = {"Accept": "application/json", "User-Agent": "Hunter/1.1"}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        # Never retry writes. An ambiguous result is reconciled by remote labels next run.
        with urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(url, data=data, headers=headers), timeout=20) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise PublishError("response_too_large")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        raise PublishError("http_" + str(error.code)) from None
    except (OSError, ValueError):
        raise PublishError("network_or_response_error") from None


def access_token():
    raw = os.environ.get("HUNTER_BLOGGER_OAUTH", "")
    if not raw:
        raise PublishError("oauth_not_connected")
    try:
        creds = json.loads(raw)
        fields = ("client_id", "client_secret", "refresh_token")
        if any(not isinstance(creds.get(k), str) or not creds[k] for k in fields):
            raise ValueError()
        reply = request_json("https://oauth2.googleapis.com/token", form={
            **{k: creds[k] for k in fields}, "grant_type": "refresh_token"})
        token = reply.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError()
        return token
    except (ValueError, TypeError):
        raise PublishError("oauth_configuration_invalid") from None


def check_settings(cfg, now):
    p = cfg.get("publishing", {})
    if not p.get("enabled"):
        return False
    if (p.get("connector") != "blogger" or not re.fullmatch(r"\d+", str(p.get("blog_id", "")))
            or not re.fullmatch(r"https://[a-z0-9-]+\.blogspot\.com/", p.get("blog_url", ""))
            or not re.fullmatch(r"[^@\s]+@[^@\s]+", p.get("identity", ""))
            or p.get("mode") != "finite_owned_blog_guides"
            or cfg.get("safety") != {"publishing_enabled": True, "human_approval_required": False}):
        raise PublishError("publishing_scope_invalid")
    proof = p.get("policy_evidence", {})
    try:
        expiry = datetime.fromisoformat(proof["expires_at"])
        if expiry.tzinfo is None or expiry <= now or proof.get("status") != "verified":
            raise ValueError()
        if proof.get("url") != "https://www.blogger.com/content-policy":
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise PublishError("publishing_policy_expired_or_missing") from None
    if not p.get("authority_reference"):
        raise PublishError("publishing_authority_missing")
    return True


def render_guide(product):
    pid = product.get("id")
    if pid not in GUIDES or not product.get("enabled"):
        raise PublishError("product_not_supported")
    if not product.get("name") or not product.get("features"):
        raise PublishError("product_facts_missing")
    link = urllib.parse.urlsplit(product.get("url", ""))
    if link.scheme != "https" or link.hostname != "cero.gumroad.com" or not link.path.startswith("/l/"):
        raise PublishError("product_link_invalid")
    e = lambda text: html.escape(str(text), quote=True)
    title, paragraphs = GUIDES[pid]
    content = "".join("<p>" + e(x) + "</p>" for x in paragraphs)
    content += "<h2>A Cero resource for this topic</h2><p>This guide includes a product promotion for Cero.</p>"
    content += "<p><strong>" + e(product["name"]) + "</strong></p>"
    content += "<p>" + e(product["description"]) + "</p><ul>"
    content += "".join("<li>" + e(x) + "</li>" for x in product["features"])
    content += "</ul><p>Check the product page for current price, contents and terms before buying. No outcome is guaranteed.</p>"
    content += '<p><a rel="sponsored" href="' + e(product["url"]) + '">View the Cero product</a></p>'
    return {"kind": "blogger#post", "title": title, "content": content,
            "labels": ["Cero", "hunter-cero", "hunter-" + pid]}


def run_publisher(con, cfg, now, *, api=request_json, get_token=access_token):
    if not check_settings(cfg, now):
        return {"status": "disabled", "published": 0}
    p = cfg["publishing"]
    blog_id = str(p["blog_id"])
    blog_host = urllib.parse.urlsplit(p["blog_url"]).hostname
    base = "https://www.googleapis.com/blogger/v3/blogs/" + blog_id
    local_done = set()
    for raw, occurred in con.execute("SELECT payload,occurred_at FROM published_content"):
        receipt = json.loads(raw)
        if receipt.get("connector") != "blogger":
            continue
        if receipt.get("product_id"):
            local_done.add("hunter-" + receipt["product_id"])
        local_done.update(receipt.get("labels", []))
        at = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        if at > now - timedelta(hours=24):
            return {"status": "daily_cooldown", "published": 0}
    token = get_token()
    blog = api(base, token=token)
    if str(blog.get("id")) != blog_id or urllib.parse.urlsplit(blog.get("url", "")).hostname != blog_host:
        raise PublishError("destination_identity_mismatch")
    user = api("https://www.googleapis.com/oauth2/v2/userinfo", token=token)
    if user.get("email") != p["identity"] or user.get("verified_email") is not True:
        raise PublishError("oauth_user_unverified")
    query = urllib.parse.urlencode({"labels": "hunter-cero", "maxResults": 100, "fetchBodies": "false"})
    posts = []
    for status in ("live", "draft", "scheduled"):
        remote = api(base + "/posts?" + query + "&status=" + status, token=token)
        if remote.get("nextPageToken"):
            raise PublishError("remote_history_requires_review")
        for post in remote.get("items", []):
            post["_query_status"] = status
            posts.append(post)
    done = local_done | {label for post in posts for label in post.get("labels", [])}
    for post in posts:
        url = post.get("url", "")
        if urllib.parse.urlsplit(url).hostname != blog_host:
            raise PublishError("published_url_mismatch")
        if post.get("id") and post.get("published") and post.get("_query_status") == "live":
            con.execute("INSERT OR IGNORE INTO published_content VALUES(?,?,?)", (
                "blogger-" + str(post["id"]), json.dumps({"connector": "blogger", "url": url,
                "post_id": str(post["id"]), "labels": post.get("labels", [])}), post["published"]))
            published = datetime.fromisoformat(post["published"].replace("Z", "+00:00"))
            if published > now - timedelta(hours=24):
                return {"status": "daily_cooldown", "published": 0}
    for product in cfg["products"]:
        if not product.get("enabled") or "hunter-" + product["id"] in done:
            continue
        proof = product.get("facts_evidence", {})
        try:
            if proof.get("status") != "verified" or datetime.fromisoformat(proof["expires_at"]) <= now:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise PublishError("product_facts_expired") from None
        body = render_guide(product)
        created = api(base + "/posts?isDraft=false", token=token, body=body)
        if not re.fullmatch(r"\d+", str(created.get("id", ""))):
            raise PublishError("ambiguous_publish_result")
        confirmed = api(base + "/posts/" + str(created["id"]), token=token)
        url = confirmed.get("url", "")
        if (urllib.parse.urlsplit(url).hostname != blog_host or confirmed.get("title") != body["title"]
                or "hunter-" + product["id"] not in confirmed.get("labels", [])
                or not confirmed.get("published") or confirmed.get("status", "LIVE") != "LIVE"):
            raise PublishError("publication_not_confirmed")
        con.execute("INSERT OR IGNORE INTO published_content VALUES(?,?,?)", (
            "blogger-" + str(created["id"]), json.dumps({"connector": "blogger", "url": url,
            "post_id": str(created["id"]), "product_id": product["id"],
            "content_sha256": hashlib.sha256(body["content"].encode()).hexdigest()}), now.isoformat()))
        return {"status": "published", "published": 1, "url": url}
    return {"status": "catalogue_complete_no_repetition", "published": 0}
