#!/usr/bin/env python3
"""Bounded GitHub Actions watchdog; never edits application code or data."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
REPORT = Path("state/watchdog.json")


def request(method: str, url: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "Project-Workflow-Watchdog",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read()
        return json.loads(body) if body else {"http_status": response.status}


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("TARGET_BRANCH", "main")
    workflow = os.environ.get("TARGET_WORKFLOW", "")
    active_limit = int(os.environ.get("STALE_ACTIVE_MINUTES", "30"))
    queued_limit = int(os.environ.get("STALE_QUEUED_MINUTES", "30"))
    success_limit = int(os.environ.get("STALE_SUCCESS_MINUTES", "60"))
    now = datetime.now(timezone.utc)
    result = {
        "checked_at_utc": now.isoformat(),
        "repository": repo,
        "workflow": workflow,
        "status": "unknown",
        "actions": [],
        "errors": [],
    }

    try:
        if not token or "/" not in repo or not workflow.endswith(".yml"):
            raise RuntimeError("Missing token, repository, or valid workflow filename")
        base = f"{API}/repos/{repo}/actions/workflows/{workflow}"
        metadata = request("GET", base, token)
        if metadata.get("state") != "active":
            request("PUT", base + "/enable", token)
            result["actions"].append("Requested enable for disabled target workflow")
        else:
            result["actions"].append("Target workflow is enabled")

        runs = request("GET", base + f"/runs?branch={branch}&per_page=30", token).get("workflow_runs", [])
        active = [r for r in runs if r.get("status") in {"queued", "in_progress"}]
        successful = []
        for run in runs:
            if run.get("conclusion") != "success":
                continue
            stamp = run.get("updated_at") or run.get("completed_at") or run.get("created_at")
            if stamp:
                successful.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
        last_success = max(successful) if successful else None
        age = (now - last_success).total_seconds() / 60 if last_success else None
        result["last_success_age_minutes"] = round(age, 1) if age is not None else None
        result["active_runs"] = len(active)

        stale = []
        for run in active:
            stamp = run.get("created_at")
            if not stamp:
                continue
            run_age = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 60
            limit = active_limit if run.get("status") == "in_progress" else queued_limit
            if run_age > limit:
                stale.append(run)

        if stale:
            for run in stale:
                request("POST", f"{API}/repos/{repo}/actions/runs/{run['id']}/cancel", token)
            result["status"] = "stale_run_cancel_requested"
            result["actions"].append("Requested cancellation only for runs beyond configured stale threshold; recovery waits for next watchdog cycle")
            result["stale_run_ids"] = [r["id"] for r in stale]
        elif active:
            result["status"] = "healthy"
            result["actions"].append("Target run is active; no duplicate dispatch")
        elif age is None or age > success_limit:
            request("POST", base + "/dispatches", token, {"ref": branch})
            result["status"] = "recovery_dispatched"
            result["actions"].append("Dispatched one bounded recovery run because no recent successful run exists")
        else:
            result["status"] = "healthy"
            result["actions"].append("Recent successful target run found")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1500]
        result["status"] = "blocked"
        result["errors"].append(f"GitHub API HTTP {exc.code}: {body}")
    except Exception as exc:
        result["status"] = "blocked"
        result["errors"].append(f"{type(exc).__name__}: {exc}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
