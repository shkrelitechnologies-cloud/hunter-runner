# Hunter runner

Public code and offline tests. Configuration, catalogue facts, account identifiers, inbox, drafts, reports and state remain private.

Research runs every six hours. It collects bounded public-source research and saves state privately. HN is research-only, with no generated replies. Affiliate applications, payments, unsolicited messages and account registration are not supported.

## Owned-blog publishing connector

`hunter_publish.py` supports only a configured, verified owned Blogger blog. It prepares six distinct practical guides, one per configured Cero product, with clear product promotion and supplied facts. Each product is published once, at most one post in 24 hours, then the finite catalogue stops without repetition. It does not post replies or copy pitches across sites.

Publishing needs an explicit private configuration grant, current product/policy evidence, matching Google OAuth email, and exact blog identity. OAuth credentials are read from the `HUNTER_BLOGGER_OAUTH` environment variable, never from committed configuration. This secret is not included here. Until it is connected and publishing is explicitly enabled in the private configuration, research continues without publishing.

The connector checks remote live, draft and scheduled labels to prevent duplication after an interrupted state write. It confirms each new post by reading it back. Redirects are rejected; ambiguous writes are never blindly retried. API response bodies and credentials are not logged.

Tests: `python -m unittest discover -s tests -v`. Tests use synthetic accounts and mocked APIs, with no live publishing.
