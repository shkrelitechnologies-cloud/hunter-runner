"""Offline regression tests for HUNTER's browser-only publisher."""
import unittest
from datetime import timedelta

import hunter as h
import hunter_publish as hp


def evidence():
    now = h.utcnow()
    return {
        "status": "verified",
        "reference": "synthetic-test-only",
        "url": "https://directory.example/rules",
        "verified_at": h.stamp(now - timedelta(days=1)),
        "expires_at": h.stamp(now + timedelta(days=1)),
        "automation_allowed": True,
        "login_required": False,
        "email_verification_required": False,
        "captcha_required": False,
        "payment_required": False,
        "human_approval_required": False,
    }


def destination():
    return {
        "submission_url": "https://directory.example/submit",
        "readback_url": "https://directory.example/listing/synthetic",
        "permission": evidence(),
        "selectors": {"title": "#title", "description": "#description", "submit": "button[type=submit]"},
        "content_fit_reviewed": True,
        "dedupe_key": "synthetic-unique",
        "daily_limit": 1,
        "payload": {"title": "Synthetic sample", "description": "Test fixture only."},
    }


class BrowserPublisherSafetyTests(unittest.TestCase):
    def test_blogger_connector_is_rejected(self):
        cfg = {"publishing": {"enabled": True, "connector": "blogger"}}
        self.assertFalse(hp.check_settings(cfg, h.utcnow()))

    def test_browser_connector_accepts_empty_list_as_safe_noop(self):
        cfg = {"publishing": {"enabled": True, "connector": "browser", "destinations": []}}
        self.assertTrue(hp.check_settings(cfg, h.utcnow()))
        self.assertEqual(hp.run_publisher(None, cfg, h.utcnow())["status"], "no_eligible_browser_destinations")

    def test_disabled_publisher_does_not_attempt(self):
        result = hp.run_publisher(None, {"publishing": {"enabled": False}}, h.utcnow())
        self.assertEqual(result["published"], 0)
        self.assertEqual(result["attempted"], 0)

    def test_current_explicit_permission_is_required(self):
        site = destination()
        self.assertTrue(hp._approved(site, h.utcnow()))
        site["permission"]["login_required"] = True
        self.assertFalse(hp._approved(site, h.utcnow()))

    def test_expired_permission_is_rejected(self):
        site = destination()
        site["permission"]["expires_at"] = h.stamp(h.utcnow() - timedelta(seconds=1))
        self.assertFalse(hp._approved(site, h.utcnow()))

    def test_cross_host_readback_is_rejected(self):
        site = destination()
        site["readback_url"] = "https://other.example/listing"
        self.assertFalse(hp._approved(site, h.utcnow()))

    def test_no_auth_or_api_client_symbols(self):
        self.assertFalse(hasattr(hp, "request_json"))
        self.assertFalse(hasattr(hp, "access_token"))


if __name__ == "__main__":
    unittest.main()
