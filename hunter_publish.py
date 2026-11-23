"""Retired publisher compatibility module.

HUNTER no longer publishes through an API. This module performs no network
requests and reads no credentials. Browser publishing remains disabled until a
destination-specific browser adapter has documented permission and verification.
"""


class PublishError(Exception):
    """Compatibility exception for the retired publisher hook."""


def check_settings(cfg, now):
    """Reject retired publisher settings so callers cannot mistake them for enabled."""
    return False


def run_publisher(con, cfg, now, **kwargs):
    """Report that no explicitly approved browser publisher is configured."""
    return {"status": "disabled_no_approved_browser_adapter", "published": 0}
