# Hunter runner

Public code and offline tests. Configuration, catalogue facts, account identifiers, inbox, drafts, reports and state remain private.

Research is configured with a six-hour buyer interval and private state persistence. The GitHub runner workflow is scheduled to wake every five minutes (bounded eight-minute job, single concurrency lock); source cooldowns prevent every wake-up from repeating research. GitHub may delay or skip scheduled runs. HN is research-only, with no generated replies. Affiliate applications, payments, unsolicited messages and account registration are not supported.

## Owned-blog publishing connector

`hunter_publish.py` supports only a configured, verified owned Blogger blog. It prepares six distinct practical guides, one per configured Cero product, with clear product promotion and supplied facts. Each product is published once, at most one post in 24 hours, then the finite catalogue stops without repetition. It does not post replies or copy pitches across sites.

Publishing needs an explicit private configuration grant, current product/policy evidence, matching Google OAuth email, and exact blog identity. OAuth credentials are read from the `HUNTER_BLOGGER_OAUTH` environment variable, never from committed configuration. This secret is not included here. Until it is connected and publishing is explicitly enabled in the private configuration, research continues without publishing.

The connector checks remote live, draft and scheduled labels to prevent duplication after an interrupted state write. It confirms each new post by reading it back. Redirects are rejected; ambiguous writes are never blindly retried. API response bodies and credentials are not logged.

Tests: `python -m unittest discover -s tests -v`. Tests use synthetic accounts and mocked APIs, with no live publishing.

## Invited directory listings

`hunter_directories.py` uses only the documented, free Launchpad and DevElif APIs. It checks product/category fit and does not submit arbitrary forms, create accounts, vote or send messages. Launchpad uses URL deduplication and exact listing readback; DevElif allows one website per domain. Responses without an independently verified public listing remain pending, not counted as published. No paid upgrades are used.

The connector processes its small verified queue without an artificial daily delay, then stops rather than repeating the same pitches. New sites need a separate rules and category review before adding a connector. Secrets, private catalogue facts, account identifiers and receipts stay private.


## Independent 666 daily operating target

HUNTER has its own target of **666 independently verified public placements per Asia/Kolkata calendar day** (not 666 shared across the four projects). COCO, HUNTER, MAYA, and SHIV each have a separate 666 target, for 2,664 combined per day. Reset the daily counter at 00:00 IST and preserve lifetime history.

Run discovery, deduplication, eligibility checks, permitted connector work, public readback verification, private-state persistence, and health reporting unattended within existing configured permissions and resource limits. Skip and record CAPTCHA/puzzle, login or unauthorized registration, email/phone verification, payment, human-approval, prohibited-automation, and unsupported-payload barriers; continue with other eligible candidates. Never bypass controls.

Only distinct public listings/posts independently read back from the destination count. Research leads, drafts, attempts, pending moderation, workflow success, and unverified receipts do not. The current connectors are finite and permission-scoped; the 666 figure is the independent daily target, not a claim that present connector capacity already achieves it. The five-minute workflow schedule is a wake-up cadence, not proof of a publication every five minutes.
