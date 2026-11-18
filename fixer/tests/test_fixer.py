import sys, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixer import classify_error, recent_failure

KB={"patterns":[
 {"id":"transient_network","action":"retry_safe_job_once","signatures":[r"connection reset by peer",r"temporary failure in name resolution"]},
 {"id":"workflow_permission","action":"manual_review","signatures":[r"workflow.*permission"]},
 {"id":"unknown","action":"manual_review","signatures":[]}
]}
class FixerPolicyTests(unittest.TestCase):
 def test_documented_transient_signature(self):
  self.assertEqual(classify_error("Connection reset by peer",KB)["id"],"transient_network")
 def test_permission_error_is_not_auto_retried(self):
  self.assertEqual(classify_error("workflow permission denied",KB)["action"],"manual_review")
 def test_unknown_error_requires_review(self):
  self.assertEqual(classify_error("unrecognised failure",KB)["id"],"unknown")
 def test_recent_main_failure_is_eligible(self):
  now=datetime.now(timezone.utc)
  run={"status":"completed","conclusion":"failure","branch":"main","event":"schedule","created_at":now.isoformat()}
  self.assertTrue(recent_failure(run,now))
  run["branch"]="feature"
  self.assertFalse(recent_failure(run,now))
 def test_old_failure_is_rejected(self):
  now=datetime.now(timezone.utc)
  run={"status":"completed","conclusion":"failure","branch":"main","event":"schedule","created_at":(now-timedelta(hours=25)).isoformat()}
  self.assertFalse(recent_failure(run,now))

 def test_retry_requires_both_documented_and_repository_allowlists(self):
  from fixer import retry_is_allowed
  pattern={"action":"retry_safe_job_once","allowed_jobs":["test"]}
  self.assertTrue(retry_is_allowed(pattern,"test",1,{"test"}))
  self.assertFalse(retry_is_allowed(pattern,"run",1,{"run"}))
  self.assertFalse(retry_is_allowed(pattern,"test",2,{"test"}))

if __name__=="__main__": unittest.main()
