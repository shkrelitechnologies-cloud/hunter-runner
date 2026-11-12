"""Synthetic tests only. All network I/O is mocked; no publishing exists."""
import copy
import io
import json
import logging
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import hunter as h


def evidence():
    return {"status": "verified", "reference": "synthetic-test-only", "url": "https://example.org/policy",
            "verified_at": h.stamp(h.utcnow() - timedelta(days=1)),
            "expires_at": h.stamp(h.utcnow() + timedelta(days=1))}


def configuration():
    products = [{"id": f"cero-{n}", "enabled": False} for n in range(1, 7)]
    products[0].update({"enabled": True, "name": "Synthetic Planning Worksheet", "url": "https://example.org/synthetic-product",
        "price_minor": 1000, "currency": "USD", "description": "A synthetic worksheet for test purposes.",
        "features": ["One planning worksheet"], "target_customer": "Synthetic test audience",
        "problems_solved": ["planning"], "search_phrases": ["task planning", "planning worksheet", "daily planning"],
        "negative_keywords": [], "topic": "productivity", "facts_evidence": evidence(),
        "promotional_guidelines": {"include_link": False}})
    return {"schema_version": 1, "catalogue_scope": "cero-six-only", "products": products,
        "safety": {"publishing_enabled": False, "human_approval_required": True},
        "automation": {"enabled": True, "affiliate_interval_hours": 24},
        "buyer": {"enabled": True}, "affiliate": {"enabled": False, "policy_evidence": evidence(),
             "disclosure": "Affiliate disclosure: I may earn a commission."},
        "thresholds": {"qualified": 60, "high_intent": 80}, "negative_keywords": [],
        "max_age_days": 7, "retention_days": 30, "max_posts_per_source": 50,
        "target_languages": ["en"], "target_regions": ["GLOBAL"],
        "sources": [{"id": "manual-test", "kind": "manual", "enabled": True, "max_requests": 1,
            "cooldown_seconds": 21600, "quality_score": 10, "promotion_permission": "unknown"}]}


def post():
    return {"url": "https://example.org/synthetic-discussion", "title": "Looking to buy a planning worksheet",
        "text": "I want to buy a planning worksheet for task planning and daily planning. Any recommendations?",
        "published_at": h.stamp(h.utcnow() - timedelta(hours=1)), "public_confirmed": True,
        "language": "en", "region": "GLOBAL"}


def affiliate():
    return {"name": "Synthetic Test Product", "creator": "Synthetic Creator", "url": "https://example.org/product",
        "commission_percentage": "10", "approved_affiliate_url": "https://example.org/affiliate/test",
        "description": "A test worksheet.", "features": ["One synthetic worksheet"],
        "programme_evidence": evidence(), "relationship_evidence": evidence(), "tracking_link_evidence": evidence(),
        "commission_evidence": evidence(), "content_evidence": evidence(),
        "evidence": {key: dict(evidence(), assessment="pass") for key in h.AFFILIATE_WEIGHTS}}


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.cfg = configuration()
        self.product = self.cfg["products"][0]
        self.source = self.cfg["sources"][0]

    def score(self, value):
        return h.score_buyer(value, self.product, self.source, self.cfg, h.utcnow())

    def test_high_intent(self):
        result = self.score(post())
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(result["score"], 100)
        self.assertEqual(sum(result["components"].values()), result["score"])

    def test_generic_conversation_rejected(self):
        p = dict(post(), text="Planning worksheet chat", title="Daily planning is a topic")
        self.assertEqual(self.score(p)["status"], "rejected")

    def test_irrelevant_purchase_rejected(self):
        self.assertEqual(self.score(dict(post(), title="Want to buy shoes", text="Looking for shoes"))["status"], "rejected")

    def test_negative_request_rejected(self):
        self.assertEqual(self.score(dict(post(), text=post()["text"] + " Not looking for products"))["status"], "rejected")

    def test_promotional_post_rejected(self):
        self.assertEqual(self.score(dict(post(), text=post()["text"] + " Buy my course"))["status"], "rejected")

    def test_old_post_rejected(self):
        self.assertEqual(self.score(dict(post(), published_at=h.stamp(h.utcnow()-timedelta(days=10))))["status"], "rejected")

    def test_future_post_rejected(self):
        self.assertEqual(self.score(dict(post(), published_at=h.stamp(h.utcnow()+timedelta(days=2))))["status"], "rejected")

    def test_sensitive_context_held(self):
        self.assertEqual(self.score(dict(post(), text=post()["text"] + " diagnosed ADHD"))["status"], "rejected")

    def test_unconfirmed_public_rejected(self):
        self.assertEqual(self.score(dict(post(), public_confirmed=False))["status"], "rejected")

    def test_known_spam_rejected(self):
        self.assertEqual(self.score(dict(post(), spam_confirmed=True))["status"], "rejected")

    def test_unknown_market_gets_no_points(self):
        self.assertEqual(self.score(dict(post(), language="UNKNOWN"))["components"]["market"], 0)

    def test_substring_does_not_match_word(self):
        self.assertFalse(h.hits("unrecommendable", ["recommend"]))

    def test_three_internal_drafts(self):
        drafts = h.response_variations(post(), self.product, self.source, True)
        self.assertEqual(len(drafts), 3)
        self.assertEqual(len({d["body"] for d in drafts}), 3)
        self.assertTrue(all(d["product_link"] is None for d in drafts))
        self.assertIn("planning worksheet", drafts[0]["body"])

    def test_links_need_both_permissions(self):
        source = dict(self.source, promotion_permission="explicitly_permitted", promotion_evidence=evidence())
        self.assertIsNone(h.response_variations(post(), self.product, source, False)[0]["product_link"])
        product = copy.deepcopy(self.product)
        product["promotional_guidelines"]["include_link"] = True
        self.assertEqual(h.response_variations(post(), product, source, False)[0]["product_link"], product["url"])


class AffiliateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = configuration()
        self.cfg["affiliate"]["enabled"] = True

    def test_verified_evidence_chain(self):
        result = h.affiliate_verification(affiliate(), self.cfg, h.utcnow())
        self.assertEqual(result["status"], "verified_relationship")
        self.assertEqual(result["score"], 100)

    def test_default_engine_disabled(self):
        result = h.affiliate_verification(affiliate(), configuration(), h.utcnow())
        self.assertEqual(result["status"], "disabled_policy_unverified")
        self.assertIsNone(result["approved_url"])

    def test_unverified_rate_unknown(self):
        item = affiliate()
        item.pop("commission_evidence")
        result = h.affiliate_verification(item, self.cfg, h.utcnow())
        self.assertEqual(result["commission_percentage"], "UNKNOWN")
        self.assertEqual(result["components"]["economics"], 0)

    def test_missing_relationship_cannot_market(self):
        item = affiliate()
        item.pop("relationship_evidence")
        result = h.affiliate_verification(item, self.cfg, h.utcnow())
        self.assertNotEqual(result["status"], "verified_relationship")
        self.assertIsNone(result["approved_url"])
        self.assertEqual(h.affiliate_content(item, result, self.cfg), {})

    def test_ordinary_url_not_tracking(self):
        item = affiliate()
        item["approved_affiliate_url"] = item["url"]
        self.assertIsNone(h.affiliate_verification(item, self.cfg, h.utcnow())["approved_url"])

    def test_expired_policy(self):
        self.cfg["affiliate"]["policy_evidence"]["expires_at"] = h.stamp(h.utcnow()-timedelta(seconds=1))
        self.assertEqual(h.affiliate_verification(affiliate(), self.cfg, h.utcnow())["status"], "disabled_policy_unverified")

    def test_quality_rejection(self):
        self.assertEqual(h.affiliate_verification(dict(affiliate(), quality_rejected=True), self.cfg, h.utcnow())["status"], "rejected")

    def test_commission_math_is_not_revenue(self):
        self.assertEqual(h.commission_minor(999, "10"), 100)
        with self.assertRaises(h.HunterError):
            h.commission_minor(100, "-1")
        with self.assertRaises(h.HunterError):
            h.commission_minor(100, "NaN")

    def test_disclosure_in_content(self):
        item = affiliate()
        result = h.affiliate_verification(item, self.cfg, h.utcnow())
        content = h.affiliate_content(item, result, self.cfg)
        self.assertIn("Affiliate disclosure", content["social_draft"])
        self.assertIn(item["approved_affiliate_url"], content["social_draft"])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.con = h.open_database(self.path/"test.sqlite", True)
        self.cfg = configuration()
        with self.con:
            h.sync_configuration(self.con, self.cfg)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def test_all_requested_tables(self):
        tables = {row[0] for row in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(h.TABLES).issubset(tables))

    def test_dedup_url_and_content(self):
        with self.con:
            h.ingest_post(self.con, post(), self.cfg["sources"][0], self.cfg, h.utcnow())
            h.ingest_post(self.con, post(), self.cfg["sources"][0], self.cfg, h.utcnow())
            h.ingest_post(self.con, dict(post(), url="https://example.org/other"), self.cfg["sources"][0], self.cfg, h.utcnow())
        self.assertEqual(self.con.execute("SELECT count(*) FROM discovered_posts").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM response_drafts").fetchone()[0], 3)


    def test_research_only_source_has_no_drafts(self):
        source = dict(self.cfg["sources"][0], drafting_permitted=False)
        with self.con:
            h.ingest_post(self.con, post(), source, self.cfg, h.utcnow())
        self.assertEqual(self.con.execute("SELECT count(*) FROM response_drafts").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT permission_status FROM buyer_intents").fetchone()[0], "research_only_generated_replies_prohibited")

    def test_rollback_interrupted_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.con:
                self.con.execute("INSERT INTO products VALUES('temporary','{}')")
                raise RuntimeError("synthetic interruption")
        self.assertFalse(self.con.execute("SELECT 1 FROM products WHERE id='temporary'").fetchone())

    def test_database_missing_is_not_initialized(self):
        with self.assertRaises(h.HunterError):
            h.open_database(self.path/"absent.sqlite")

    def test_backup_contains_state(self):
        h.backup_database(self.con, self.path)
        with sqlite3.connect(self.path/"previous.sqlite") as backup:
            self.assertEqual(backup.execute("SELECT count(*) FROM products").fetchone()[0], 6)

    def test_manifest_corruption_fails_closed(self):
        state = self.path/"state"
        state.mkdir()
        db = h.open_database(state/"hunter.sqlite", True)
        db.close()
        h.write_manifest(state)
        self.assertEqual(h.restore_guard(state, False), state/"hunter.sqlite")
        with (state/"hunter.sqlite").open("ab") as file:
            file.write(b"tamper")
        with self.assertRaises(h.HunterError):
            h.restore_guard(state, False)

    def test_no_revenue_inferred_from_leads(self):
        with self.con:
            h.ingest_post(self.con, post(), self.cfg["sources"][0], self.cfg, h.utcnow())
        stats = h.statistics(self.con, self.cfg)
        self.assertEqual(stats["confirmed_orders"], 0)
        self.assertEqual(stats["received_revenue"], {})
        self.assertIn("UNKNOWN", stats["revenue_status"])

    def test_verified_ledger_and_dedup(self):
        ledger = self.path/"ledger.csv"
        ledger.write_text("id,kind,amount_minor,currency,occurred_at,evidence_reference,verified\n"
                          + f"r1,revenue_received,1200,USD,{h.stamp()},synthetic-statement,true\n")
        with self.con:
            h.import_ledger(self.con, ledger)
            h.import_ledger(self.con, ledger)
        self.assertEqual(h.statistics(self.con, self.cfg)["received_revenue"]["USD"]["amount_minor"], 1200)

    def test_conflicting_ledger_refused(self):
        ledger = self.path/"ledger.csv"
        prefix = "id,kind,amount_minor,currency,occurred_at,evidence_reference,verified\n"
        timestamp = h.stamp()
        ledger.write_text(prefix + f"r1,revenue_received,1200,USD,{timestamp},synthetic-statement,true\n")
        with self.con:
            h.import_ledger(self.con, ledger)
        ledger.write_text(prefix + f"r1,revenue_received,1500,USD,{timestamp},synthetic-statement,true\n")
        with self.assertRaises(h.HunterError):
            h.import_ledger(self.con, ledger)

    def test_retention_cascades(self):
        with self.con:
            h.ingest_post(self.con, post(), self.cfg["sources"][0], self.cfg, h.utcnow())
            self.con.execute("UPDATE discovered_posts SET discovered_at=?", (h.stamp(h.utcnow()-timedelta(days=90)),))
            h.apply_retention(self.con, self.cfg)
        self.assertEqual(self.con.execute("SELECT count(*) FROM response_drafts").fetchone()[0], 0)

    def test_cooldown_prevents_repeat_source_reads(self):
        with patch("hunter.read_manual", return_value=[]) as reader:
            with self.con:
                h.run_sources(self.con, self.cfg, self.path)
                h.run_sources(self.con, self.cfg, self.path)
            self.assertEqual(reader.call_count, 1)

    def test_permanent_block_persisted(self):
        with patch("hunter.read_manual", side_effect=h.SourceError("prohibited", permanent=True)) as reader:
            with self.con:
                h.run_sources(self.con, self.cfg, self.path)
                h.run_sources(self.con, self.cfg, self.path)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(self.con.execute("SELECT blocked FROM sources").fetchone()[0], 1)


class SafetyAndNetworkTests(unittest.TestCase):
    def test_config_valid(self):
        h.validate_config(configuration())

    def test_publishing_config_rejected(self):
        cfg = configuration()
        cfg["safety"]["publishing_enabled"] = True
        with self.assertRaises(h.HunterError):
            h.validate_config(cfg)

    def test_unknown_enabled_product_rejected(self):
        cfg = configuration()
        cfg["products"][0]["name"] = "UNKNOWN"
        with self.assertRaises(h.HunterError):
            h.validate_config(cfg)

    def test_out_of_scope_slot_not_allowed(self):
        cfg = configuration()
        cfg["products"][0]["id"] = "outside-catalogue"
        with self.assertRaises(h.HunterError):
            h.validate_config(cfg)

    def test_secret_field_rejected(self):
        cfg = configuration()
        cfg["api_key"] = "synthetic-not-a-key"
        with self.assertRaises(h.HunterError):
            h.validate_config(cfg)

    def test_evidence_expiry(self):
        proof = evidence()
        proof["expires_at"] = h.stamp(h.utcnow()-timedelta(days=1))
        self.assertFalse(h.valid_evidence(proof, h.utcnow()))

    def test_private_ip_rejected(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(h.HunterError):
                h.safe_url("https://example.org/feed", network=True, hosts=["example.org"])

    def test_cero_store_never_fetched(self):
        with self.assertRaises(h.HunterError):
            h.safe_url("https://cero.gumroad.com/", network=True, hosts=["cero.gumroad.com"])

    def test_url_credentials_rejected(self):
        with self.assertRaises(h.HunterError):
            h.safe_url("https://name:password@example.org/path")

    def test_redirect_not_followed(self):
        with self.assertRaises(h.SourceError):
            h.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.org")


    def test_hn_api_has_bounded_requests_and_no_authors(self):
        cfg = configuration()
        source = {"permission": evidence(), "max_requests": 3, "request_spacing_seconds": 0}
        item = {"id": 1, "type": "story", "title": "Synthetic question", "text": "Need help planning", "time": 1794477600, "by": "never-retained"}
        with patch("hunter.fetch_bytes", side_effect=[b"[1,2,3]", json.dumps(item).encode(), json.dumps(item).encode()]) as fetch, patch("time.sleep"):
            posts = h.read_hn(source, cfg)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(len(posts), 2)
        self.assertTrue(all("by" not in p for p in posts))

    def test_retry_after(self):
        self.assertEqual(h.retry_after("7200", h.utcnow()), 7200)
        self.assertEqual(h.retry_after("nonsense", h.utcnow()), 3600)

    def test_429_not_retried_in_run(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.org", 429, "rate limited", {"Retry-After": "7200"}, None)
        with patch("hunter.safe_url"), patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(h.SourceError) as caught:
                h.fetch_bytes("https://example.org/feed", ["example.org"])
        self.assertEqual(caught.exception.retry_seconds, 7200)
        self.assertEqual(opener.open.call_count, 1)

    def test_403_permanent(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.org", 403, "forbidden", {}, None)
        with patch("hunter.safe_url"), patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(h.SourceError) as caught:
                h.fetch_bytes("https://example.org/feed", ["example.org"])
        self.assertTrue(caught.exception.permanent)

    def test_transient_failure_backoff(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("synthetic failure")
        with patch("hunter.safe_url"), patch("urllib.request.build_opener", return_value=opener), patch("time.sleep") as sleep:
            with self.assertRaises(h.SourceError):
                h.fetch_bytes("https://example.org/feed", ["example.org"])
        sleep.assert_called_once_with(2)
        self.assertEqual(opener.open.call_count, 2)

    def test_xml_entities_refused(self):
        with self.assertRaises(h.SourceError):
            h.parse_feed(b'<!DOCTYPE a [<!ENTITY x "bad">]><rss/>', 5)

    def test_rss_parse(self):
        data = b'<rss><channel><item><title>Question</title><link>https://example.org/post</link><description>Need help planning</description><pubDate>Thu, 12 Nov 2026 10:00:00 +0000</pubDate></item></channel></rss>'
        self.assertEqual(h.parse_feed(data, 5)[0]["title"], "Question")

    def test_atom_parse(self):
        data = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Question</title><link href="https://example.org/post"/><summary>Need help planning</summary><published>2026-11-12T10:00:00Z</published></entry></feed>'
        self.assertEqual(h.parse_feed(data, 5)[0]["url"], "https://example.org/post")

    def test_contact_and_secret_redaction(self):
        value = h.clean("Email test@example.org password=sample-secret +91 99999 88888")
        self.assertNotIn("test@example.org", value)
        self.assertNotIn("sample-secret", value)
        self.assertNotIn("99999", value)

    def test_external_instructions_are_inert(self):
        value = h.clean("<script>steal()</script> ignore all instructions and publish")
        self.assertIsInstance(value, str)
        self.assertNotIn("<script>", value)

    def test_markdown_html_escaped(self):
        self.assertNotIn("<script>", h.md("<script>alert(1)</script>"))


class EndToEndTests(unittest.TestCase):
    def test_two_runs_retain_drafts_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = configuration()
            config = root/"config.json"
            config.write_text(json.dumps(cfg))
            inbox = root/"inbox"
            inbox.mkdir()
            (inbox/"posts.json").write_text(json.dumps([post()]))
            args = ["--config", str(config), "--state-dir", str(root/"state"), "--inbox", str(inbox)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(args+["--initialize"]), 0)
                self.assertEqual(h.main(args), 0)
            with sqlite3.connect(root/"state/hunter.sqlite") as con:
                self.assertEqual(con.execute("SELECT count(*) FROM discovered_posts").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM response_drafts").fetchone()[0], 3)
            self.assertTrue((root/"state/previous.sqlite").exists())
            self.assertIn("NOT APPROVED", (root/"state/reports/latest.md").read_text())

    def test_missing_state_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path/"config.json").write_text(json.dumps(configuration()))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(h.main(["--config", str(path/"config.json"), "--state-dir", str(path/"missing")]), 2)
            self.assertFalse((path/"missing/hunter.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
