import unittest
from datetime import datetime, timezone, timedelta
from self_repair import recent_scheduled_failure

class MindPolicyTests(unittest.TestCase):
    def test_accepts_first_attempt_recent_scheduled_failure(self):
        now = datetime.now(timezone.utc)
        run = {"event":"schedule","branch":"main","status":"completed","conclusion":"failure",
               "run_attempt":1,"created_at":now.isoformat()}
        self.assertTrue(recent_scheduled_failure(run, now))

    def test_rejects_push_failure(self):
        now = datetime.now(timezone.utc)
        run = {"event":"push","branch":"main","status":"completed","conclusion":"failure",
               "run_attempt":1,"created_at":now.isoformat()}
        self.assertFalse(recent_scheduled_failure(run, now))

    def test_rejects_second_attempt(self):
        now = datetime.now(timezone.utc)
        run = {"event":"schedule","branch":"main","status":"completed","conclusion":"failure",
               "run_attempt":2,"created_at":now.isoformat()}
        self.assertFalse(recent_scheduled_failure(run, now))

    def test_rejects_old_failure(self):
        now = datetime.now(timezone.utc)
        run = {"event":"schedule","branch":"main","status":"completed","conclusion":"failure",
               "run_attempt":1,"created_at":(now-timedelta(hours=7)).isoformat()}
        self.assertFalse(recent_scheduled_failure(run, now))

if __name__ == "__main__":
    unittest.main()
