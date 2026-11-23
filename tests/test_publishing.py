"""Regression tests for the retired HUNTER publisher."""
import unittest

import hunter_publish as hp


class RetiredPublishingTests(unittest.TestCase):
    def test_no_destination_means_no_publication(self):
        result = hp.run_publisher(None, {}, None)
        self.assertEqual(result["status"], "disabled_no_approved_browser_adapter")
        self.assertEqual(result["published"], 0)

    def test_compatibility_module_has_no_network_or_credential_client(self):
        self.assertFalse(hasattr(hp, "request_json"))
        self.assertFalse(hasattr(hp, "access_token"))

    def test_retired_publisher_rejects_all_settings(self):
        self.assertFalse(hp.check_settings({"publishing": {"enabled": True}}, None))


if __name__ == "__main__":
    unittest.main()
