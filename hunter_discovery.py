"""Read-only candidate discovery. Evidence is untrusted page data, never permission.

No POST, forms, logins, product-store requests, SEO actions or search-engine scraping.
Only manually reviewed connectors can publish. A keyword score never enables one.
"""
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

USER_AGENT = "HunterDirectoryResearch/1.0"
EXCLUDED = {"gumroad.com", "github.com", "raw.githubusercontent.com", "youtube.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "reddit.com", "linkedin.com", "t.me", "discord.com"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS directory_candidates(
 url TEXT PRIMARY KEY, domain TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
 catalog_free TEXT NOT NULL, priority INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 checked_at TEXT, score INTEGER, evidence TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS directory_queue ON directory_candidates(state,priority DESC,url);
"""

class DiscoveryError(Exception):
    pass


def canonical(url):
    try:
        u = urllib.parse.urlsplit(url.strip())
        host = (u.hostname or "").lower().rstrip(".")
        if u.scheme != "https" or not host or u.username or u.password or u.port not in (None,443):
            raise ValueError()
        if host == "localhost" or "." not in host or any(host == h or host.endswith("."+h) for h in EXCLUDED):
            raise ValueError()
        # Query arguments may carry tokens or write-like actions. Discovery needs neither.
        return urllib.parse.urlunsplit(("https",host,u.path or "/","",""))
    except (ValueError,AttributeError):
        raise DiscoveryError("unsafe_or_excluded_url") from None


def resolve_public(host):
    try:
        answers = socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(row[4][0]).is_global for row in answers):
            raise ValueError()
    except (ValueError,OSError):
        raise DiscoveryError("non_public_or_unresolved_address") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_text(url):
    """Public HTTPS GET, bounded bytes, bounded redirects, never store-domain navigation."""
    current = canonical(url)
    for _ in range(4):
        u = urllib.parse.urlsplit(current)
        resolve_public(u.hostname)
        req = urllib.request.Request(current,headers={"User-Agent":USER_AGENT,"Accept":"text/html,text/plain,application/json"})
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=5) as r:
                raw = r.read(500001)
                if len(raw)>500000:
                    raise DiscoveryError("page_too_large")
                content_type = r.headers.get("Content-Type","").lower()
                if not any(x in content_type for x in ("text/","json","xml")):
                    raise DiscoveryError("unsupported_content_type")
                return raw.decode("utf-8",errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (301,302,303,307,308):
                target = canonical(urllib.parse.urljoin(current,e.headers.get("Location","")))
                old,new = u.hostname,urllib.parse.urlsplit(target).hostname
                if old.removeprefix("www.") != new.removeprefix("www."):
                    raise DiscoveryError("cross_domain_redirect_not_followed") from None
                current = target
                continue
            if e.code in (401,403,429):
                raise DiscoveryError("access_or_rate_limit_stop_"+str(e.code)) from None
            if e.code==404:
                raise DiscoveryError("http_404") from None
            raise DiscoveryError("http_"+str(e.code)) from None
        except (OSError,ValueError):
            raise DiscoveryError("network_or_decode_error") from None
    raise DiscoveryError("redirect_limit")


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[];self.links=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","noscript"):self.hidden+=1
        if tag=="a":
            href=dict(attrs).get("href","")
            if href:self.links.append(href)
    def handle_endtag(self,tag):
        if tag in ("script","style","noscript") and self.hidden:self.hidden-=1
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def classify(html,url,keywords):
    p=Page();p.feed(html);text=" ".join(" ".join(p.parts).split());lower=text.lower()
    if re.search(r"verify you are human|access denied|just a moment|checking your browser|captcha",lower):
        return {"state":"challenge_stop","score":0,"evidence":{},"links":[]}
    matched=[k for k in keywords if k.lower() in lower][:20]
    submit=bool(re.search(r"submit (?:your |a )?(?:tool|product|website|startup|template)|add your (?:site|product|tool)|list your (?:site|product)",lower))
    automation=bool(re.search(r"(?:bots?|agents?|automated|programmatic).{0,80}(?:welcome|submit|submission)|(?:submit|submission).{0,80}(?:bots?|agents?|automated|programmatic)",lower))
    prohibited=bool(re.search(r"no automated submissions|automated submissions (?:are )?(?:not allowed|prohibited)|bots (?:are )?not allowed",lower))
    # These are triage signals only. Never set permission_verified from page text.
    score=len(matched)*2+int(submit)*3+int(automation)*4-int(prohibited)*20
    state="review_rules" if submit and not prohibited else "research_only"
    if prohibited:state="automation_prohibited_signal"
    evidence={"untrusted_page_data":True,"permission_verified":False,"matched_keywords":matched,
              "submission_signal":submit,"automation_signal":automation,"prohibition_signal":prohibited,
              "excerpt":text[:1200]}
    base=urllib.parse.urlsplit(url)
    links=[]
    for href in p.links:
        try:
            target=canonical(urllib.parse.urljoin(url,href));u=urllib.parse.urlsplit(target)
            if u.hostname!=base.hostname:continue
            if re.search(r"/(?:submit|add|terms|polic|rule|docs|developers|api|faq)(?:[/.\-]|$)",u.path,re.I):
                links.append(target)
        except DiscoveryError:continue
    return {"state":state,"score":score,"evidence":evidence,"links":list(dict.fromkeys(links))[:6]}


def enqueue(con,items,limit=10000):
    inserted=0
    for item in items:
        if con.execute("SELECT count(*) FROM directory_candidates").fetchone()[0]>=limit:break
        try:url=canonical(item["url"])
        except (DiscoveryError,KeyError):continue
        domain=urllib.parse.urlsplit(url).hostname
        # One initial candidate per domain. Additional same-site policy links are evidence only.
        if con.execute("SELECT 1 FROM directory_candidates WHERE domain=?",(domain,)).fetchone():continue
        free=str(item.get("catalog_free","Unknown"))[:12]
        priority=10 if free=="Yes" else (5 if free.startswith("Yes") else 0)
        con.execute("INSERT OR IGNORE INTO directory_candidates(url,domain,name,source,catalog_free,priority) VALUES(?,?,?,?,?,?)",
                    (url,domain,str(item.get("name",domain))[:160],str(item.get("source","manual"))[:300],free,priority))
        inserted+=1
    return inserted


def run_discovery(con,cfg,now,inbox,*,fetch=fetch_text,clock=time.monotonic,pause=time.sleep):
    d=cfg.get("discovery",{})
    if not d.get("enabled"):return {"status":"disabled","scanned":0,"eligible":0,"failed":0}
    for statement in SCHEMA.split(";"):
        if statement.strip():con.execute(statement)
    # Never a persisted processing state: each row is finalized before the next starts.
    seed_path=inbox/"directory-seeds.json"
    if seed_path.exists():
        items=json.loads(seed_path.read_text())
        if not isinstance(items,list) or len(items)>20000:raise DiscoveryError("seed_file_invalid")
        imported=enqueue(con,items,min(int(d.get("max_candidates",10000)),10000))
    else:imported=0
    # Leave time for the runner's fixed eight-minute job timeout, state commit and cleanup.
    # This is an external-platform checkpoint, not a daily/site-count throttling policy.
    budget=int(d.get("checkpoint_seconds",360))
    keywords=[str(k)[:80] for k in d.get("keywords",[])][:40]
    # No artificial 30/60-site ceiling. A pending domain is claimed once in this run.
    rows=con.execute("SELECT url,domain FROM directory_candidates WHERE state='pending' ORDER BY priority DESC,url").fetchall()
    scanned=failed=review=blocked=0;started=clock()
    def inspect(row):
        url,domain=row
        try:
            robots=fetch("https://"+domain+"/robots.txt")
        except DiscoveryError as e:
            if e.args[0]=="http_404":robots="User-agent: *\nAllow: /"
            else:return url,"robots_unverified",None,{},e.args[0]
        if "<html" in robots.lower() or "<body" in robots.lower():
            return url,"robots_unverified",None,{},"robots_not_plain_rules"
        rules=urllib.robotparser.RobotFileParser();rules.parse(robots.splitlines())
        if not rules.can_fetch(USER_AGENT,url):
            return url,"robots_disallowed",None,{},None
        delay=rules.crawl_delay(USER_AGENT) or 0
        if delay:
            # A long per-site delay is a restriction, not something to bypass with workers.
            if delay>20:return url,"requires_slow_site_review",None,{},None
            pause(delay)
        try:
            result=classify(fetch(url),url,keywords)
            return url,result["state"],result["score"],dict(result["evidence"],policy_links=result["links"]),None
        except DiscoveryError as e:
            return url,"fetch_failed",None,{},e.args[0]
    def save(result):
        nonlocal scanned,failed,review,blocked
        url,state,score,evidence,error=result
        scanned+=1;failed+=int(error is not None);review+=int(state=="review_rules")
        blocked+=int(state in ("robots_disallowed","challenge_stop","automation_prohibited_signal","requires_slow_site_review"))
        con.execute("UPDATE directory_candidates SET state=?,checked_at=?,score=?,evidence=?,error=? WHERE url=?",
                    (state,now.isoformat(),score,json.dumps(evidence),error,url))
    # Workers touch different domains only, never SQLite. No login or write happens here.
    workers=int(d.get("workers",8))
    if not 1<=workers<=32 or not 1<=budget<=390:raise DiscoveryError("runner_resource_settings_invalid")
    pending_rows=iter(rows)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active={}
        for _ in range(workers):
            row=next(pending_rows,None)
            if row:active[pool.submit(inspect,row)]=row
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                row=active.pop(future)
                try:result=future.result()
                except Exception:
                    # A worker defect must not erase earlier completed read results.
                    result=(row[0],"fetch_failed",None,{},"worker_failed")
                save(result)
                if clock()-started<budget:
                    row=next(pending_rows,None)
                    if row:active[pool.submit(inspect,row)]=row
    pending=con.execute("SELECT count(*) FROM directory_candidates WHERE state='pending'").fetchone()[0]
    total=con.execute("SELECT count(*) FROM directory_candidates").fetchone()[0]
    reviewed_connectors=len(cfg.get("directories",{}).get("connectors",[]))
    return {"status":"completed","imported":imported,"queued_domains":total,"scanned":scanned,
            "needs_rule_review":review,"eligible":0,"reviewed_connectors":reviewed_connectors,
            "failed":failed,"blocked":blocked,"pending":pending,
            "note":"Discovery eligibility is never inferred. Reviewed connectors publish in a separate stage."}
