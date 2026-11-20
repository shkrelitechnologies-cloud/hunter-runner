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
- Transient discovery/directory read failures use bounded retries/backoff where safe (idempotent reads only); ambiguous writes are never blindly retried and must be reconciled before reattempt.
- Systemic blockers such as missing OAuth/deploy secrets, repository permission failures, or private-state restore failures must remain visible and must not be reported as healthy publishing.

## Source of truth
The private `hunter` repository's `state` branch contains the authoritative `operations/PUBLICATION_LEDGER.csv`, daily summary, and run report. The runner's watchdog artifact `hunter-watchdog-<run>` records each health check. Do not copy private content or credentials into this repository.

## Required health report
Every check records timestamp, production workflow state, active/stale run IDs, recovery action, errors/blockers, and next retry condition. Daily reporting shows verified publications / 666, target gap, and separate attempted, pending, rejected, failed, and blocked counts.

## Operator shorthand: `666`
When the operator requests `666`, include HUNTER in the four-project audit (COCO, HUNTER, MAYA, SHIV). Read the private `hunter` repository's `state` branch, reconcile `operations/DAILY_SUMMARY.csv` against `operations/PUBLICATION_LEDGER.csv` (and `operations/ACTIVITY_LEDGER.csv` when needed), report verified unique publications / 666, remaining target, separate attempts/pending/failed/blocked/skipped/duplicates, and calculate required throughput to midnight IST. A successful HUNTER Actions run is not proof of a new publication. If private state cannot be read, report the count as **unverifiable**, never as zero. Existing verified listings and today's new publications must be shown separately.

**Local audit command:** `python ops_666.py` (read-only). It uses local authoritative ledgers when available (checked-out private `state` operations directory, local state worktree, or `HUNTER_STATE_DIR`) and reports `unverifiable` if private evidence is not accessible. When ledgers are available it reports reconciliation, remaining time/rate to midnight IST, unique domains/connectors for today's counted publications, attempts/pending/failed-blocked event counts, and recent workflow runs (via `gh` when available).

## Operational reference
- **Purpose:** Permission-gated research, reviewed directory submissions, and limited owned-blog publishing (when explicitly enabled/authorized) with independently verified public readback.
- **Entry points/workflows:** `.github/workflows/hunter.yml` (scheduled bounded worker, `workflow_dispatch`), `.github/workflows/hunter-watchdog.yml` and `.github/workflows/watchdog.yml` (health/recovery), `.github/workflows/fixer.yml` (repository fixer). See workflow files for cron schedules and concurrency groups.
- **Daily target/reset:** 666 verified unique public publications/day; IST (`Asia/Kolkata`) day boundary 00:00; history retained.
- **Configuration/credentials:** Private config and state in `shkrelitechnologies-cloud/hunter` (secrets: `HUNTER_DEPLOY_KEY`, optional `HUNTER_BLOGGER_OAUTH` in runner). Never expose secrets.
- **Ledgers/queues:** Private state branch `operations/PUBLICATION_LEDGER.csv`, `operations/ACTIVITY_LEDGER.csv`, `operations/DAILY_SUMMARY.csv`, SQLite state.
- **Discovery/evaluation:** Read-only, robots-aware, worldwide/multilingual browser seed discovery and candidate classification feed a deduped domain queue with priority hints; candidates are evidence only and never grant permission or enable unreviewed connectors.
- **Verification:** Counts only independently opened/read-back public listing/post URL. Attempts, drafts, queued, accepted-for-review, moderation-pending, duplicates, and uncertain results do not count.
- **Error handling:** Blocked/failed destinations recorded and skipped; systemic blockers (missing secrets/permissions/restore failures) surfaced as blockers, not healthy publishing. Bounded retries/backoff apply only to safe idempotent reads. No bypass of site rules/auth/CAPTCHA/moderation/rate limits.
- **Resource limits:** Bounded workflow timeouts, single production concurrency lock, incremental state commits, conflict-safe push behavior, and bounded discovery/checkpoint budgets.
- **Manual intervention:** Required for missing/expired secrets, private-state conflicts/corruption, connector policy/permission review, OAuth/account approval, or platform restrictions. Resume by resolving blocker and dispatching `hunter.yml`/watchdog via `workflow_dispatch` after verifying state safety.
