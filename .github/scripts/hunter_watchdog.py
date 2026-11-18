#!/usr/bin/env python3
"""Recover HUNTER's scheduled read-only research worker when schedules go stale."""
from __future__ import annotations
import json, os, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
WORKFLOW = "hunter.yml"
STALE_MINUTES = 20
STALE_ACTIVE_MINUTES = 12
FAILURE_COOLDOWN_MINUTES = 15
REPORT = Path("state/hunter-watchdog.json")

def request(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "HUNTER-Actions-Watchdog",
    })
    with urllib.request.urlopen(req, timeout=20) as res:
        body = res.read()
        return json.loads(body) if body else {}

def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def main():
    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    now = datetime.now(timezone.utc)
    report = {"checked_at_utc": now.isoformat(), "repository": repo, "workflow": WORKFLOW,
              "status": "unknown", "actions": [], "errors": [],
              "mode": "read-only discovery and private draft/state maintenance; no publishing"}
    try:
        if not token or "/" not in repo:
            raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_REPOSITORY")
        base = f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}"
        wf = request("GET", base, token)
        if wf.get("state") != "active":
            request("PUT", base + "/enable", token)
            report["actions"].append("Re-enabled HUNTER worker workflow")
        else:
            report["actions"].append("HUNTER worker workflow is enabled")
        all_runs = request("GET", base + "/runs?branch=main&per_page=30", token).get("workflow_runs", [])
        # Push-triggered runs only execute the offline test job; the publisher is skipped.
        # They must never be mistaken for a healthy scheduled production cycle.
        runs = [r for r in all_runs if r.get("event") in {"schedule", "workflow_dispatch"}]
        active = [r for r in runs if r.get("status") in {"queued", "in_progress"}]
        report["active_runs"] = len(active)
        stale_active = []
        for run in active:
            age = (now - dt(run.get("created_at"))).total_seconds() / 60 if run.get("created_at") else 0
            limit = STALE_ACTIVE_MINUTES if run.get("status") == "in_progress" else 45
            if age > limit:
                stale_active.append(run)
        if stale_active:
            for run in stale_active:
                request("POST", API + "/repos/" + repo + "/actions/runs/" + str(run["id"]) + "/cancel", token)
            report["status"] = "stale_run_cancel_requested"
            report["actions"].append("Requested cancellation of stale worker run(s); next watchdog cycle will dispatch after GitHub releases the concurrency lock")
            report["stale_run_ids"] = [r["id"] for r in stale_active]
        elif active:
            report["status"] = "healthy_active"
            report["actions"].append("A worker is already active; avoided duplicate dispatch")
        else:
            ordered = sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)
            latest = ordered[0] if ordered else None
            success = next((r for r in ordered if r.get("conclusion") == "success"), None)
            age_latest = (now - dt(latest.get("created_at"))).total_seconds()/60 if latest and latest.get("created_at") else None
            age_success = (now - dt(success.get("updated_at") or success.get("completed_at") or success.get("created_at"))).total_seconds()/60 if success else None
            report["latest_run"] = {k: latest.get(k) for k in ("id","event","status","conclusion","created_at","html_url")} if latest else None
            report["last_success_age_minutes"] = round(age_success, 1) if age_success is not None else None
            recent_failure = latest and latest.get("conclusion") not in (None, "success", "cancelled") and age_latest is not None and age_latest < FAILURE_COOLDOWN_MINUTES
            if recent_failure:
                report["status"] = "cooldown"
                report["actions"].append("Recent failure detected; waiting for cooldown before retry")
            elif age_success is None or age_success > STALE_MINUTES:
                request("POST", base + "/dispatches", token, {"ref": "main"})
                report["status"] = "recovery_dispatched"
                report["actions"].append("Dispatched one HUNTER worker because its last success is stale")
            else:
                report["status"] = "healthy"
                report["actions"].append("Recent successful HUNTER worker run found")
    except urllib.error.HTTPError as exc:
        report["status"] = "blocked"
        report["errors"].append(f"GitHub API HTTP {exc.code}: {exc.read().decode(errors='replace')[:1200]}")
    except Exception as exc:
        report["status"] = "blocked"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if report["status"] == "blocked" else 0

if __name__ == "__main__":
    raise SystemExit(main())
