#!/usr/bin/env python3
"""MIND local self-maintenance. Conservative by design; never retries production jobs."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
ROLE = os.environ.get("MIND_ROLE", "")
SAFE_JOBS = set(filter(None, os.environ.get("MIND_SAFE_RETRY_JOBS", "").split(",")))
MAX_AGE_HOURS = 6

def request(path, method="GET"):
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is unavailable")
    req = urllib.request.Request(
        API + path, method=method,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "MIND-self-maintenance"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc

def recent_scheduled_failure(run, now=None):
    now = now or datetime.now(timezone.utc)
    if run.get("event") != "schedule" or run.get("branch") != "main":
        return False
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        return False
    if int(run.get("run_attempt", 1)) != 1:
        return False
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    return now - created <= timedelta(hours=MAX_AGE_HOURS)

def main():
    if not REPO or not ROLE:
        raise RuntimeError("Repository identity or MIND_ROLE missing")
    runs = request(f"/repos/{REPO}/actions/runs?branch=main&per_page=50").get("workflow_runs", [])
    failures = [r for r in runs if recent_scheduled_failure(r)]
    actions = []
    for run in failures:
        jobs = request(f"/repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100").get("jobs", [])
        failed = [j for j in jobs if j.get("conclusion") == "failure"]
        for job in failed:
            name = job.get("name", "")
            if name not in SAFE_JOBS:
                actions.append(f"SKIP run={run['id']} job={name!r}: production/side-effect job is not safe to retry")
                continue
            request(f"/repos/{REPO}/actions/jobs/{job['id']}/rerun", method="POST")
            actions.append(f"RETRIED run={run['id']} job={name!r} once")
    if actions:
        print("\n".join(actions))
    else:
        print(f"MIND OK: {REPO}; no eligible failed scheduled jobs to recover")
    # Report stuck jobs, but never cancel or restart business work automatically.
    now = datetime.now(timezone.utc)
    for run in runs:
        if run.get("event") != "schedule" or run.get("branch") != "main" or run.get("status") != "in_progress":
            continue
        started = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        age = (now - started).total_seconds() / 60
        limit = 360 if ROLE == "coco" else 12
        if age > limit:
            print(f"NEEDS_ATTENTION run={run['id']} age_minutes={int(age)}; left untouched to avoid duplicate external actions")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"MIND CHECK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
