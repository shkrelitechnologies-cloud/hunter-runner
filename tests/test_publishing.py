import copy
import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import hunter_publish as hp

NOW = datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc)


def config():
    return {"safety": {"publishing_enabled": True, "human_approval_required": False},
            "publishing": {"enabled": True, "connector": "blogger", "blog_id": "12345",
                "blog_url": "https://example.blogspot.com/", "identity": "owner@example.com",
                "mode": "finite_owned_blog_guides", "authority_reference": "synthetic owner grant",
                "policy_evidence": {"status": "verified", "url": "https://www.blogger.com/content-policy",
                                    "expires_at": "2026-12-12T00:00:00+00:00"}},
            "products": [{"id": "cero-1", "enabled": True, "name": "Synthetic planner",
                "description": "A planning workspace.", "features": ["Task list"],
                "url": "https://cero.gumroad.com/l/example",
                "facts_evidence": {"status": "verified", "expires_at": "2026-12-12T00:00:00+00:00"}}]}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.items = []
        self.created = None
        self.fail_write = False
        self.identity = "owner@example.com"
        self.blog_id = "12345"
        self.pages = False

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "userinfo" in url:
            return {"email": self.identity, "verified_email": True}
        if "?isDraft=false" in url:
            if self.fail_write:
                raise hp.PublishError("network_or_response_error")
            self.created = dict(kwargs["body"], id="98765", url="https://example.blogspot.com/guide.html",
                                published=NOW.isoformat(), status="LIVE")
            return self.created
        if url.endswith("/posts/98765"):
            return self.created
        if "/posts?" in url:
            status = url.split("&status=")[-1]
            result = {"items": [x for x in self.items if x.get("_query_status", "live") == status]}
            if self.pages:
                result["nextPageToken"] = "more"
            return result
        return {"id": self.blog_id, "url": "https://example.blogspot.com/"}


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE published_content(id TEXT PRIMARY KEY,payload TEXT,occurred_at TEXT)")
        self.cfg = config()
        self.api = FakeAPI()

    def run_pub(self):
        return hp.run_publisher(self.con, self.cfg, NOW, api=self.api, get_token=lambda: "synthetic")

    def test_disabled_makes_no_requests(self):
        self.cfg["publishing"]["enabled"] = False
        self.assertEqual(self.run_pub()["status"], "disabled")
        self.assertEqual(self.api.calls, [])

    def test_one_post_and_receipt(self):
        result = self.run_pub()
        self.assertEqual(result["published"], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM published_content").fetchone()[0], 1)
        self.assertEqual(sum("?isDraft=false" in x[0] for x in self.api.calls), 1)

    def test_remote_reconcile_stops_duplicate_even_if_local_state_missing(self):
        self.api.items = [{"id": "111", "url": "https://example.blogspot.com/a.html",
            "published": "2026-11-01T00:00:00+00:00", "labels": ["hunter-cero", "hunter-cero-1"]}]
        self.assertEqual(self.run_pub()["status"], "catalogue_complete_no_repetition")
        self.assertFalse(any("?isDraft=false" in x[0] for x in self.api.calls))

    def test_local_receipt_prevents_repost_when_remote_is_empty(self):
        self.con.execute("INSERT INTO published_content VALUES(?,?,?)", ("blogger-old", json.dumps({
            "connector": "blogger", "product_id": "cero-1"}), "2026-11-01T00:00:00+00:00"))
        self.assertEqual(self.run_pub()["status"], "catalogue_complete_no_repetition")
        self.assertFalse(any("?isDraft=false" in x[0] for x in self.api.calls))

    def test_local_daily_cooldown_needs_no_network(self):
        self.con.execute("INSERT INTO published_content VALUES(?,?,?)", ("blogger-old", json.dumps({
            "connector": "blogger", "product_id": "cero-2"}), NOW.isoformat()))
        self.assertEqual(self.run_pub()["status"], "daily_cooldown")
        self.assertEqual(self.api.calls, [])

    def test_recent_remote_post_blocks_new_post(self):
        self.api.items = [{"id": "111", "url": "https://example.blogspot.com/a.html",
            "published": NOW.isoformat(), "labels": ["hunter-cero", "hunter-cero-2"]}]
        self.assertEqual(self.run_pub()["status"], "daily_cooldown")

    def test_draft_remote_marker_also_prevents_duplicate(self):
        self.api.items = [{"id": "111", "url": "https://example.blogspot.com/a.html",
            "_query_status": "draft", "labels": ["hunter-cero", "hunter-cero-1"]}]
        self.assertEqual(self.run_pub()["published"], 0)

    def test_bad_identity_no_write(self):
        self.api.identity = "other@example.com"
        with self.assertRaisesRegex(hp.PublishError, "oauth_user_unverified"):
            self.run_pub()
        self.assertFalse(any("?isDraft=false" in x[0] for x in self.api.calls))

    def test_bad_blog_no_write(self):
        self.api.blog_id = "999"
        with self.assertRaisesRegex(hp.PublishError, "destination_identity_mismatch"):
            self.run_pub()

    def test_expired_policy_no_requests(self):
        self.cfg["publishing"]["policy_evidence"]["expires_at"] = "2026-01-01T00:00:00+00:00"
        with self.assertRaises(hp.PublishError):
            self.run_pub()
        self.assertEqual(self.api.calls, [])

    def test_expired_facts_no_write(self):
        self.cfg["products"][0]["facts_evidence"]["expires_at"] = "2026-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(hp.PublishError, "product_facts_expired"):
            self.run_pub()

    def test_no_authority(self):
        del self.cfg["publishing"]["authority_reference"]
        with self.assertRaises(hp.PublishError):
            self.run_pub()

    def test_no_arbitrary_blog_host(self):
        self.cfg["publishing"]["blog_url"] = "https://other.example/"
        with self.assertRaises(hp.PublishError):
            self.run_pub()

    def test_missing_oauth_fails_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(hp.PublishError, "oauth_not_connected"):
                hp.access_token()

    def test_invalid_oauth_is_redacted(self):
        with patch.dict(os.environ, {"HUNTER_BLOGGER_OAUTH": "synthetic-secret-not-json"}):
            with self.assertRaisesRegex(hp.PublishError, "oauth_configuration_invalid"):
                hp.access_token()

    def test_write_failure_is_not_retried(self):
        self.api.fail_write = True
        with self.assertRaises(hp.PublishError):
            self.run_pub()
        self.assertEqual(sum("?isDraft=false" in x[0] for x in self.api.calls), 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM published_content").fetchone()[0], 0)

    def test_remote_pagination_fails_closed(self):
        self.api.pages = True
        with self.assertRaisesRegex(hp.PublishError, "remote_history_requires_review"):
            self.run_pub()

    def test_render_html_escapes_catalogue(self):
        self.cfg["products"][0]["name"] = '<script>bad</script>'
        body = hp.render_guide(self.cfg["products"][0])["content"]
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn('rel="sponsored"', body)

    def test_other_product_url_rejected(self):
        self.cfg["products"][0]["url"] = "https://evil.example/l/a"
        with self.assertRaises(hp.PublishError):
            hp.render_guide(self.cfg["products"][0])

    def test_all_six_guides_distinct_and_bounded(self):
        self.assertEqual(len(hp.GUIDES), 6)
        self.assertEqual(len({v[0] for v in hp.GUIDES.values()}), 6)
        for title, paragraphs in hp.GUIDES.values():
            self.assertGreater(len(" ".join(paragraphs).split()), 60)

    def test_endpoint_allowlist(self):
        with self.assertRaisesRegex(hp.PublishError, "endpoint_not_allowlisted"):
            hp.request_json("https://example.com/post", token="synthetic")

    def test_no_redirect_following(self):
        self.assertIsNone(hp.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))


if __name__ == "__main__":
    unittest.main()
