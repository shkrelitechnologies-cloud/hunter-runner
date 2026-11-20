#!/usr/bin/env python3
"""HUNTER 666 operational audit (read-only).

Prefers local authoritative ledgers if present (operations/ in repo or common
local/private state worktrees), otherwise reports unverifiable rather than guessing.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=5, minutes=30))
TARGET = 666
LEDGER_CANDIDATES = [
    ROOT / "operations",
    ROOT / "state" / "operations",
    ROOT.parent / "hunter-state" / "operations",
    ROOT.parent / "hunter" / "state" / "operations",
    Path(os.environ.get("HUNTER_STATE_DIR", "")) / "operations" if os.environ.get("HUNTER_STATE_DIR") else None,
]


def now_ist():
    return datetime.now(timezone.utc).astimezone(TZ)


def find_ops_dir():
    for p in LEDGER_CANDIDATES:
        if not p:
            continue
        if (p / "DAILY_SUMMARY.csv").exists() and (p / "PUBLICATION_LEDGER.csv").exists():
            return p
    return None


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gh(args):
    if not shutil.which("gh"):
        return None, "gh unavailable"
    try:
        out = subprocess.check_output(["gh", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT, timeout=20).strip()
        return out, None
    except Exception as e:
        return None, str(e)


def main():
    now = now_ist()
    today = now.date().isoformat()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining_seconds = max(0.0, (midnight - now).total_seconds())
    ops = find_ops_dir()
    report = {
        "project": "HUNTER",
        "repository": "shkrelitechnologies-cloud/hunter-runner",
        "authoritative_private_state": "shkrelitechnologies-cloud/hunter state branch",
        "checked_at_ist": now.isoformat(timespec="seconds"),
        "today_ist": today,
        "target": TARGET,
    }
    if not ops:
        report.update({
            "verified_publications": "unverifiable",
            "verified_unique_publications": "unverifiable",
            "existing_listings_verified_today": "unverifiable",
            "remaining_to_target": "unverifiable",
            "required_verified_per_hour": "unverifiable",
            "attempts": "unverifiable",
            "pending": "unverifiable",
            "failed_or_blocked": "unverifiable",
            "duplicates": "unverifiable",
            "note": "Authoritative private ledger not available locally; do not report as zero.",
            "next_action": "Read private hunter state branch operations/DAILY_SUMMARY.csv, PUBLICATION_LEDGER.csv, and ACTIVITY_LEDGER.csv.",
        })
    else:
        summary = read_csv(ops / "DAILY_SUMMARY.csv")
        ledger = read_csv(ops / "PUBLICATION_LEDGER.csv")
        activity = read_csv(ops / "ACTIVITY_LEDGER.csv") if (ops / "ACTIVITY_LEDGER.csv").exists() else []
        today_row = next((r for r in summary if r.get("ist_date") == today), {})
        today_pubs = [r for r in ledger if r.get("ist_date") == today]
        counted = [r for r in today_pubs if str(r.get("counted_publication", "")).lower() == "true" and r.get("publication_url")]
        existing = [r for r in today_pubs if str(r.get("verified", "")).lower() == "true" and r.get("status") == "existing"]
        verified_ids = {r.get("publication_id") for r in counted if r.get("publication_id")}
        verified = len(verified_ids)
        domains = sorted({r.get("site_domain") for r in counted if r.get("site_domain")})
        connectors = Counter(r.get("connector") or "unknown" for r in counted)
        today_events = [r for r in activity if r.get("ist_date") == today]
        attempts = sum(1 for r in today_events if r.get("operation") == "directory_attempt")
        blocked_statuses = ("error", "failed", "rate_limited", "blocked", "prohibited", "expired", "missing", "unverified", "ambiguous")
        failed_or_blocked = sum(1 for r in today_events if r.get("operation") == "directory_attempt" and any(t in (r.get("status") or "").lower() for t in blocked_statuses))
        pending_statuses = sum(1 for r in today_events if "pending" in (r.get("status") or "").lower())
        remaining = max(0, TARGET - verified)
        rate = (remaining / (remaining_seconds / 3600.0)) if remaining_seconds > 0 else None
        report.update({
            "ledger_source": str(ops),
            "verified_publications": verified,
            "verified_unique_publications": verified,
            "existing_listings_verified_today": len({r.get("publication_id") for r in existing if r.get("publication_id")}),
            "unique_verified_domains_today": len(domains),
            "verified_domains_today": domains[:25],
            "verified_by_connector_today": dict(connectors),
            "summary_verified_publications": int(today_row.get("verified_publications", 0) or 0),
            "ledger_reconciled": (int(today_row.get("verified_publications", 0) or 0) == verified) if today_row else None,
            "remaining_to_target": remaining,
            "remaining_time_ist": str(midnight - now).split(".")[0],
            "required_verified_per_hour": round(rate, 3) if rate is not None else None,
            "directory_attempts_today": int(today_row.get("directory_attempts", 0) or attempts),
            "attempts": int(today_row.get("directory_attempts", 0) or attempts),
            "pending_events_today": pending_statuses,
            "pending": pending_statuses,
            "failed_or_blocked_events": int(today_row.get("directory_errors", 0) or failed_or_blocked),
            "failed_or_blocked": int(today_row.get("directory_errors", 0) or failed_or_blocked),
            "discovery_events_today": int(today_row.get("discovery_events", 0) or sum(1 for r in today_events if r.get("operation") in ("discovery_summary", "source_checked"))),
            "operation_events_today": int(today_row.get("operation_events", 0) or len(today_events)),
            "duplicates": 0,
            "blocker_note": "Only independently read-back public URLs count. Attempts, pending review, duplicates, and ambiguous results do not count. Reviewed connectors, site rules, and auth/CAPTCHA/rate-limit gates remain binding.",
            "next_action": "Prioritize eligible reviewed connectors with verified readback paths; keep discovery queue healthy and skip blocked candidates without bypassing gates.",
        })

    runs, err = gh(["run", "list", "-R", "shkrelitechnologies-cloud/hunter-runner", "--limit", "5", "--json", "databaseId,workflowName,status,conclusion,event,createdAt,url"])
    if runs is not None:
        try:
            report["recent_runs"] = json.loads(runs)
        except Exception:
            report["recent_runs"] = runs.splitlines()[:5]
    else:
        report["recent_runs"] = err
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
