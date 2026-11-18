#!/usr/bin/env python3
"""Repository-local Fixer: consult documented recipes, apply bounded repairs, learn outcomes."""
import io, json, os, re, subprocess, sys, urllib.error, urllib.request, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "working-documents"
KB_PATH, STATE_PATH = DOCS / "knowledge-base.json", DOCS / "fixer-state.json"
INCIDENTS_PATH = DOCS / "incident-history.md"
API, TOKEN, REPO = "https://api.github.com", os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
SAFE_JOBS = set(filter(None, os.environ.get("FIXER_SAFE_RETRY_JOBS", "").split(",")))
MAX_AGE_HOURS, MAX_LOG_BYTES = 24, 8_000_000

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def api(path, method="GET", payload=None, binary=False):
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not available")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization":"Bearer "+TOKEN, "Accept":"application/vnd.github+json",
               "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"repository-local-fixer"}
    if data: headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API+path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return raw if binary else (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8","replace")
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc

def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n", encoding="utf-8")

def safe_log_text(job_id):
    raw = api(f"/repos/{REPO}/actions/jobs/{job_id}/logs", binary=True)
    raw = raw[:MAX_LOG_BYTES]
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        return "\n".join(archive.read(n).decode("utf-8","replace") for n in archive.namelist() if not n.endswith("/"))[-250_000:]
    except (zipfile.BadZipFile, OSError):
        return raw.decode("utf-8","replace")[-250_000:]

def classify_error(log_text, knowledge):
    for pattern in knowledge.get("patterns", []):
        for signature in pattern.get("signatures", []):
            if re.search(signature, log_text, re.IGNORECASE):
                return pattern
    return next((p for p in knowledge.get("patterns",[]) if p.get("id")=="unknown"),
                {"id":"unknown","title":"Unclassified failure","action":"manual_review","signatures":[]})

def recent_failure(run, now=None):
    now = now or datetime.now(timezone.utc)
    if run.get("status")!="completed" or run.get("conclusion")!="failure" or run.get("head_branch",run.get("branch"))!="main":
        return False
    if run.get("event") not in {"schedule","push","workflow_dispatch"}:
        return False
    created = datetime.fromisoformat(run["created_at"].replace("Z","+00:00"))
    return now-created <= timedelta(hours=MAX_AGE_HOURS)

def eligible_run(run, state=None):
    if run.get("name","").lower().startswith("repository fixer"):
        return False
    head_repo = (run.get("head_repository") or {}).get("full_name")
    if head_repo not in (None, REPO) or run.get("head_branch",run.get("branch")) != "main":
        return False
    if run.get("event") not in {"schedule","push","workflow_dispatch"}:
        return False
    created = datetime.fromisoformat(run["created_at"].replace("Z","+00:00"))
    if datetime.now(timezone.utc)-created > timedelta(hours=MAX_AGE_HOURS):
        return False
    if run.get("status") != "completed":
        return False
    if run.get("conclusion") == "failure":
        return True
    if run.get("conclusion") == "success" and int(run.get("run_attempt",1)) >= 2 and state:
        return any(str(item.get("run_id")) == str(run.get("id")) and item.get("verification") == "retry_requested"
                   for item in state.get("incidents",{}).values())
    return False

def retry_is_allowed(pattern, job_name, attempt, safe_jobs):
    return (pattern.get("action") == "retry_safe_job_once"
            and job_name in safe_jobs
            and job_name in pattern.get("allowed_jobs",[])
            and attempt == 1)

def append_incident(run_id, attempt, job_name, pattern_id, action, verification):
    DOCS.mkdir(parents=True, exist_ok=True)
    if not INCIDENTS_PATH.exists():
        INCIDENTS_PATH.write_text("# Fixer incident history\n\nNo incidents recorded yet.\n",encoding="utf-8")
    line = f"- {now_iso()} | run {run_id} attempt {attempt} | job {job_name} | pattern {pattern_id} | action {action} | verification {verification}\n"
    with INCIDENTS_PATH.open("a",encoding="utf-8") as f: f.write(line)

def process_run(run, knowledge, state):
    if not eligible_run(run,state): return
    run_id, attempt = run["id"], int(run.get("run_attempt",1))
    jobs = api(f"/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100").get("jobs",[])
    failed_jobs = [j for j in jobs if j.get("conclusion")=="failure"]
    failed_names = {j.get("name","unknown") for j in failed_jobs}

    # Verify a previously requested retry using the subsequent run attempt.
    for key, prior in list(state["incidents"].items()):
        if prior.get("run_id")==run_id and prior.get("verification")=="retry_requested" and attempt>=2:
            outcome = "retry_failed" if prior.get("job") in failed_names else "verified_success"
            prior["verification"], prior["updated_at"] = outcome, now_iso()
            for p in knowledge["patterns"]:
                if p.get("id")==prior.get("pattern"):
                    field = "verified_failures" if outcome=="retry_failed" else "verified_successes"
                    p[field] = int(p.get(field,0))+1
                    p["last_seen"] = now_iso()
            append_incident(run_id,attempt,prior.get("job"),prior.get("pattern"),"documented retry",outcome)

    for job in failed_jobs:
        name = job.get("name","unknown")
        key = f"{run_id}:{name}"
        existing = state["incidents"].get(key)
        if existing and int(existing.get("run_attempt",1))>=attempt: continue
        pattern = classify_error(safe_log_text(job["id"]),knowledge)
        action, verification = "manual_review","not_auto_repaired"
        # This is the only automatic repair: one retry of an allowlisted, side-effect-free test job.
        if retry_is_allowed(pattern,name,attempt,SAFE_JOBS) and not existing:
            api(f"/repos/{REPO}/actions/jobs/{job['id']}/rerun",method="POST")
            action, verification = "rerun_failed_test_job_once","retry_requested"
        state["incidents"][key] = {
            "run_id":run_id,"run_attempt":attempt,"job_id":job.get("id"),"job":name,
            "pattern":pattern.get("id","unknown"),"action":action,
            "verification":verification,"updated_at":now_iso()
        }
        for p in knowledge["patterns"]:
            if p.get("id")==pattern.get("id"):
                p["last_seen"]=now_iso()
                p["observations"]=int(p.get("observations",0))+1
        append_incident(run_id,attempt,name,pattern.get("id","unknown"),action,verification)

def commit_documents():
    subprocess.run(["git","config","user.name","Repository Fixer"],cwd=ROOT,check=True)
    subprocess.run(["git","config","user.email","fixer@users.noreply.github.com"],cwd=ROOT,check=True)
    subprocess.run(["git","add","working-documents"],cwd=ROOT,check=True)
    if subprocess.run(["git","diff","--cached","--quiet"],cwd=ROOT).returncode==0: return
    subprocess.run(["git","commit","-m","Update working documents with repair history"],cwd=ROOT,check=True)
    subprocess.run(["git","push","origin","HEAD:main"],cwd=ROOT,check=True)

def main():
    expected = os.environ.get("FIXER_EXPECTED_REPO","")
    if not REPO or REPO!=expected: raise RuntimeError("Unexpected repository identity; refusing to act")
    DOCS.mkdir(parents=True,exist_ok=True)
    knowledge=load_json(KB_PATH,{"version":1,"patterns":[]})
    state=load_json(STATE_PATH,{"version":1,"incidents":{}})
    event_name=os.environ.get("GITHUB_EVENT_NAME","")
    if event_name=="workflow_run":
        event=json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        process_run(event.get("workflow_run",{}),knowledge,state)
    elif event_name=="schedule":
        runs=api(f"/repos/{REPO}/actions/runs?branch=main&per_page=50").get("workflow_runs",[])
        for run in runs:
            if eligible_run(run,state): process_run(run,knowledge,state)
    # push events only execute the tests; they never inspect or replay business runs.
    save_json(KB_PATH,knowledge)
    save_json(STATE_PATH,state)
    commit_documents()
    print("Fixer completed. Local diagnosis and repair history saved.")

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"Fixer stopped safely: {type(exc).__name__}: {exc}",file=sys.stderr)
        raise
