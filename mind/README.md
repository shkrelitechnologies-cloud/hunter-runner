# MIND — local self-maintenance

This copy of MIND controls **only this repository**: `$GITHUB_REPOSITORY`.

It runs every 15 minutes and can:
- Inspect recent scheduled workflow executions on this repository.
- Retry a failed job at most once, and only on the explicit safe-job allowlist.
- Flag unusually long-running scheduled jobs in its own Actions log.

Safety boundaries:
- No cross-repository access, tokens, secrets, or writes.
- No source-code or workflow auto-editing.
- Never retries production jobs that may have external side effects.
- Never cancels a running business job or alters private state.
- No report artifacts or public report publication.

Configured safe retry jobs: test.

For Hunter, production execution is deliberately not auto-retried because a failed run may already have performed external submissions or changed private state. Fixes requiring code/config changes remain explicit reviewed changes.
