import json
import sqlite3
import unittest
from datetime import datetime, timezone
import hunter_directories as hd

NOW = datetime(2026,11,12,17,0,tzinfo=timezone.utc)
def cfg():
    return {'publishing': {'identity':'owner@example.com'}, 'directories': {'enabled':True,
      'authority_reference':'synthetic grant','contact_email':'owner@example.com','connectors':['launchpad'],
      'policy_evidence':{'launchpad':{'status':'verified','url':hd.DOCS['launchpad'],'expires_at':'2026-12-01T00:00:00+00:00'}}},
      'products':[{'id':'cero-1','enabled':True,'name':'Synthetic planner','url':'https://cero.gumroad.com/l/synthetic',
      'description':'An example task planning tool for synthetic tests only.',
      'facts_evidence':{'status':'verified','expires_at':'2026-12-01T00:00:00+00:00'}}]}

class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:')
        self.c.executescript('CREATE TABLE published_content(id TEXT PRIMARY KEY,payload TEXT,occurred_at TEXT); CREATE TABLE execution_logs(id INTEGER PRIMARY KEY,occurred_at TEXT,operation TEXT,source_id TEXT,status TEXT); CREATE TABLE configuration_history(hash TEXT PRIMARY KEY,seen_at TEXT,payload TEXT);')
        self.cfg=cfg();self.calls=[];self.existing=False;self.fail=False
    def api(self,url,body=None):
        self.calls.append((url,body))
        data={'id':12,'url':self.cfg['products'][0]['url'],'share_url':'https://techtools.cz/tools/launchpad/?tool=12'}
        if body is not None:
            if self.fail:raise hd.DirectoryError('ambiguous_network_or_response')
            return {'success':True,'data':data,'share_url':data['share_url']}
        if '/tools/12' in url:return {'success':True,'data':data}
        return {'success':True,'data':[data] if self.existing else []}
    def run_dirs(self):return hd.run_directories(self.c,self.cfg,NOW,api=self.api)
    def test_submit_readback_and_local_dedupe(self):
        self.assertEqual(self.run_dirs()['submitted'],1)
        self.assertEqual(self.run_dirs()['attempted'],0)
        self.assertEqual(sum(b is not None for _,b in self.calls),1)
    def test_existing_is_not_new_submission(self):
        self.existing=True
        self.assertEqual(self.run_dirs()['existing'],1)
        self.assertFalse(any(b for _,b in self.calls))
    def test_ambiguous_write_not_retried_next_pass(self):
        self.fail=True
        self.assertEqual(self.run_dirs()['failed'],1)
        self.assertEqual(self.run_dirs()['attempted'],0)
    def test_non_tool_product_not_mislabelled(self):
        self.cfg['products'][0]['id']='cero-2'
        self.assertEqual(self.run_dirs()['attempted'],0)
    def test_disabled_no_calls(self):
        self.cfg['directories']['enabled']=False
        self.assertEqual(self.run_dirs()['status'],'disabled');self.assertEqual(self.calls,[])
    def test_unreviewed_site_rejected(self):
        self.cfg['directories']['connectors']=['arbitrary']
        with self.assertRaises(hd.DirectoryError):self.run_dirs()
    def test_identity_must_match(self):
        self.cfg['directories']['contact_email']='someone@example.com'
        with self.assertRaises(hd.DirectoryError):self.run_dirs()
    def test_expired_policy_rejected(self):
        self.cfg['directories']['policy_evidence']['launchpad']['expires_at']='2025-01-01T00:00:00+00:00'
        with self.assertRaises(hd.DirectoryError):self.run_dirs()
    def test_no_arbitrary_endpoint(self):
        with self.assertRaises(hd.DirectoryError):hd.api_json('https://evil.example/submit',{})
    def test_no_votes_endpoint_from_connector(self):
        # Connector only ever constructs submit, lookup and exact readback routes.
        self.run_dirs();self.assertFalse(any('upvote' in u for u,_ in self.calls))
    def test_payload_honest_bot_and_no_maker_claim(self):
        body=hd.payload('launchpad',self.cfg['products'][0],'owner@example.com')
        self.assertEqual(body['submitted_by'],'Hunter');self.assertNotIn('maker_name',body)
    def test_bad_product_link(self):
        self.cfg['products'][0]['url']='https://evil.example/a'
        self.assertEqual(self.run_dirs()['failed'],1)
    def test_develif_one_domain_pending_not_confirmed(self):
        self.cfg['directories']['connectors']=['develif']
        self.cfg['directories']['policy_evidence']['develif']={'status':'verified','url':hd.DOCS['develif'],'expires_at':'2026-12-01T00:00:00+00:00'}
        def api(url,body):return {'success':True,'managementToken':'SECRET','url':'https://develif.com/s/example'}
        out=hd.run_directories(self.c,self.cfg,NOW,api=api)
        self.assertEqual(out['pending_verification'],1);self.assertEqual(out['submitted'],0)
        saved=self.c.execute('select payload from configuration_history').fetchone()[0]
        self.assertNotIn('SECRET',saved)
