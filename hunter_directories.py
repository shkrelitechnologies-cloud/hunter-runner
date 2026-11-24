"""Retired directory API publisher. HUNTER public submissions are browser-only.

No directory API calls, account creation, credentials, or submissions are
performed here. The production workflow currently runs discovery only; a
site-specific browser adapter must not be enabled without explicit current
permission, no-login access, and independent public readback verification.
"""


class DirectoryError(Exception):
    """Compatibility exception for legacy callers."""


def run_directories(con, cfg, now, **kwargs):
    """Fail closed and report that no authorized browser adapter is installed.

    ``kwargs`` is accepted only for compatibility with old tests/callers; any
    injected client is deliberately ignored and never invoked.
    """
    return {
        "status": "disabled_no_approved_browser_adapter",
        "submitted": 0,
        "published": 0,
        "attempted": 0,
        "failed": 0,
        "pending_verification": 0,
        "existing": 0,
    }
