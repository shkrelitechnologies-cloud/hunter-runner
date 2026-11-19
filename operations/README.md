# HUNTER Operations Record

HUNTER's unattended worker and watchdog are owned by this repository. Private campaign state and the publication ledger remain in the separate private `hunter` repository's `state` branch.

## Target and counting
- Daily target: 666 verified public publications.
- Clock: Asia/Kolkata (IST); daily counters reset at 00:00 IST and history is retained.
- Count only a unique public publication URL independently opened/read back.
- Attempts, drafts, queued items, accepted-for-review, moderation-pending, duplicate URLs, and uncertain results do not count.

## Recovery
- `.github/workflows/hunter-watchdog.yml` checks every 15 minutes.
- The watchdog re-enables the worker workflow if disabled, avoids duplicate active production runs, cancels stale active/queued runs, and dispatches one recovery run when production execution is overdue.
- Push-triggered offline tests are not production success and must never satisfy the production heartbeat.
- A blocked directory/site or candidate must be recorded and skipped so the next eligible candidate can proceed. Never bypass site rules, authentication, CAPTCHA, moderation, rate limits, or permission gates.
- Systemic blockers such as missing OAuth/deploy secrets, repository permission failures, or private-state restore failures must remain visible and must not be reported as healthy publishing.

## Source of truth
The private `hunter` repository's `state` branch contains the authoritative `operations/PUBLICATION_LEDGER.csv`, daily summary, and run report. The runner's watchdog artifact `hunter-watchdog-<run>` records each health check. Do not copy private content or credentials into this repository.

## Required health report
Every check records timestamp, production workflow state, active/stale run IDs, recovery action, errors/blockers, and next retry condition. Daily reporting shows verified publications / 666, target gap, and separate attempted, pending, rejected, failed, and blocked counts.

## Operator shorthand: `666`
When the operator requests `666`, include HUNTER in the four-project audit (COCO, HUNTER, MAYA, SHIV). Read the private `hunter` repository's `state` branch, reconcile `operations/DAILY_SUMMARY.csv` against `operations/PUBLICATION_LEDGER.csv`, report verified unique publications / 666, remaining target, separate attempts/pending/failed/blocked/skipped/duplicates, and calculate required throughput to midnight IST. A successful HUNTER Actions run is not proof of a new publication. If private state cannot be read, report the count as **unverifiable**, never as zero. Existing verified listings and today's new publications must be shown separately.
