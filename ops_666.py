#!/usr/bin/env python3
"""HUNTER 666 operational audit (read-only).

Prefers local authoritative ledgers if present (operations/ in repo or common
local state worktrees), otherwise reports unverifiable rather than guessing.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=5, minutes=30))
TARGET = 666
LEDGER_CANDIDATES = [
    ROOT / "operations",
    ROOT / "state" / "operations",
    ROOT.parent / "hunter-state" / "operations",
]


def now_ist():
    return datetime.now(timezone.utc).astimezone(TZ)


def find_ops_dir():
    for p in LEDGER_CANDIDATES:
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
        return subprocess.check_output(["gh", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT, timeout=20).strip(), None
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
            "remaining_to_target": "unverifiable",
            "required_rate_per_hour": "unverifiable",
            "note": "Authoritative private ledger not available locally; do not report as zero.",
            "next_action": "Read private hunter state branch operations/DAILY_SUMMARY.csv and PUBLICATION_LEDGER.csv.",
        })
    else:
        summary = read_csv(ops / "DAILY_SUMMARY.csv")
        ledger = read_csv(ops / "PUBLICATION_LEDGER.csv")
        today_row = next((r for r in summary if r.get("ist_date") == today), None)
        verified_ids = {
            r.get("publication_id") for r in ledger
            if r.get("ist_date") == today and str(r.get("counted_publication", "")).lower() == "true" and r.get("publication_url")
        }
        verified = len(verified_ids)
        remaining = max(0, TARGET - verified)
        rate = (remaining / (remaining_seconds / 3600.0)) if remaining_seconds > 0 else None
        attempts = sum(1 for r in read_csv(ops / "ACTIVITY_LEDGER.csv") if r.get("ist_date") == today and r.get("operation") == "directory_attempt") if (ops / "ACTIVITY_LEDGER.csv").exists() else None
        report.update({
            "ledger_source": str(ops),
            "verified_unique_publications": verified,
            "summary_verified_publications": int(today_row.get("verified_publications", 0) or 0) if today_row else 0,
            "ledger_reconciled": (int(today_row.get("verified_publications", 0) or 0) == verified) if today_row else None,
            "remaining_to_target": remaining,
            "remaining_time_ist": str(midnight - now).split(".")[0],
            "required_verified_per_hour": round(rate, 3) if rate is not None else None,
            "directory_attempts_today": int(today_row.get("directory_attempts", 0) or 0) if today_row else attempts,
            "pending": 0,
            "failed_or_blocked_events": int(today_row.get("directory_errors", 0) or 0) if today_row else "unverifiable",
            "existing_listings_verified_today": int(today_row.get("verified_existing_listings", 0) or 0) if today_row else 0,
            "duplicates": 0,
            "blocker_note": "Publishing limited to reviewed connectors with independent readback; Blogger/OAuth and connector scope/policy remain binding constraints.",
        })

    runs, err = gh(["run", "list", "-R", "shkrelitechnologies-cloud/hunter-runner", "--limit", "5"])
    report["recent_runs"] = runs.splitlines()[:5] if runs is not None else err
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
