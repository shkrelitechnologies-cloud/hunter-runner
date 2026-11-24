import unittest
import hunter_directories as hd


class RetiredDirectoryPublisherTests(unittest.TestCase):
    def test_legacy_client_is_never_invoked(self):
        calls = []
        result = hd.run_directories(None, {"directories": {"enabled": True}}, None,
                                    api=lambda *args, **kwargs: calls.append(args))
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "disabled_no_approved_browser_adapter")
        self.assertEqual(result["submitted"], 0)
        self.assertEqual(result["published"], 0)

    def test_no_auth_or_api_client_surface_remains(self):
        self.assertFalse(hasattr(hd, "api_json"))
        self.assertFalse(hasattr(hd, "access_token"))

    def test_disabled_state_is_reported_without_network(self):
        result = hd.run_directories(None, {"directories": {"enabled": False}}, None)
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
