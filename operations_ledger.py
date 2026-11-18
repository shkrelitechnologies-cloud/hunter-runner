#!/usr/bin/env python3
"""Export HUNTER's durable, repository-local IST activity and publication ledgers."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
PUBLICATION_FIELDS = [
    "publication_id", "occurred_at_utc", "timestamp_ist", "ist_date",
    "connector", "site_domain", "publication_url", "product_id",
    "status", "verified", "evidence_type",
]
ACTIVITY_FIELDS = [
    "event_id", "occurred_at_utc", "timestamp_ist", "ist_date",
    "operation", "source_id", "status",
]
SUMMARY_FIELDS = [
    "ist_date", "target", "verified_publications", "unique_verified_domains",
    "directory_attempts", "directory_errors", "publisher_events",
    "discovery_events", "operation_events", "remaining_to_target",
    "lifetime_verified_publications",
]


def _ist(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def _read(path: Path, fields: list[str]) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _atomic_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def export_ledgers(con, state_dir: Path, target: int = 666) -> dict:
    """Append stable DB events, rebuild IST summaries, and return the current snapshot."""
    ops = state_dir / "operations"
    ops.mkdir(parents=True, exist_ok=True)

    # Append activity by immutable SQLite row ID. The CSV survives DB retention.
    activity_path = ops / "ACTIVITY_LEDGER.csv"
    activity = _read(activity_path, ACTIVITY_FIELDS)
    activity_ids = {row.get("event_id") for row in activity}
    for event_id, occurred, operation, source_id, status in con.execute(
        "SELECT id,occurred_at,operation,source_id,status FROM execution_logs ORDER BY id"
    ):
        key = str(event_id)
        if key in activity_ids:
            continue
        stamp = _ist(occurred)
        activity.append({
            "event_id": key, "occurred_at_utc": occurred,
            "timestamp_ist": stamp.isoformat(timespec="seconds"),
            "ist_date": stamp.date().isoformat(), "operation": operation,
            "source_id": source_id or "", "status": status,
        })
        activity_ids.add(key)
    activity.sort(key=lambda row: (row.get("occurred_at_utc", ""), int(row.get("event_id") or 0)))
    _atomic_csv(activity_path, ACTIVITY_FIELDS, activity)

    # published_content is populated only after a public listing/readback or
    # owned-blog API readback has confirmed the live destination.
    publication_path = ops / "PUBLICATION_LEDGER.csv"
    publications = _read(publication_path, PUBLICATION_FIELDS)
    publication_ids = {row.get("publication_id") for row in publications}
    for publication_id, payload_raw, occurred in con.execute(
        "SELECT id,payload,occurred_at FROM published_content ORDER BY occurred_at,id"
    ):
        key = str(publication_id)
        if key in publication_ids:
            continue
        try:
            payload = json.loads(payload_raw)
        except (TypeError, ValueError):
            payload = {}
        url = str(payload.get("url", "") or "")
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        stamp = _ist(occurred)
        connector = str(payload.get("connector", "unknown"))
        publications.append({
            "publication_id": key, "occurred_at_utc": occurred,
            "timestamp_ist": stamp.isoformat(timespec="seconds"),
            "ist_date": stamp.date().isoformat(), "connector": connector,
            "site_domain": host, "publication_url": url,
            "product_id": str(payload.get("product_id", "")),
            "status": str(payload.get("status", "verified_readback")),
            "verified": "true" if host and url.startswith(("https://", "http://")) else "false",
            "evidence_type": "public_readback",
        })
        publication_ids.add(key)
    publications.sort(key=lambda row: (row.get("occurred_at_utc", ""), row.get("publication_id", "")))
    _atomic_csv(publication_path, PUBLICATION_FIELDS, publications)

    today = datetime.now(timezone.utc).astimezone(TZ).date().isoformat()
    days = {row.get("ist_date", "") for row in activity + publications if row.get("ist_date")}
    days.add(today)
    summary = []
    verified_all = {
        row.get("publication_id") for row in publications
        if row.get("verified", "").lower() == "true" and row.get("publication_url")
    }
    for day in sorted(days):
        pubs = [
            row for row in publications
            if row.get("ist_date") == day and row.get("verified", "").lower() == "true"
            and row.get("publication_url")
        ]
        domains = {row.get("site_domain") for row in pubs if row.get("site_domain")}
        events = [row for row in activity if row.get("ist_date") == day]
        count = len({row.get("publication_id") for row in pubs})
        directory_attempts = sum(row.get("operation") == "directory_attempt" for row in events)
        directory_errors = sum(
            row.get("operation") == "directory_attempt"
            and any(token in row.get("status", "").lower() for token in ("error", "failed", "rate_limited", "blocked"))
            for row in events
        )
        publisher_events = sum(row.get("operation") == "publisher" for row in events)
        discovery_events = sum(row.get("operation") in ("discovery_summary", "source_checked") for row in events)
        summary.append({
            "ist_date": day, "target": target,
            "verified_publications": count, "unique_verified_domains": len(domains),
            "directory_attempts": directory_attempts, "directory_errors": directory_errors,
            "publisher_events": publisher_events, "discovery_events": discovery_events,
            "operation_events": len(events), "remaining_to_target": max(0, target - count),
            "lifetime_verified_publications": len(verified_all),
        })
    _atomic_csv(ops / "DAILY_SUMMARY.csv", SUMMARY_FIELDS, summary)

    current = next((row for row in summary if row["ist_date"] == today), summary[-1])
    return current
