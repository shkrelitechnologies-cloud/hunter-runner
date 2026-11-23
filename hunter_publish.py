"""Retired publisher compatibility module.

HUNTER no longer publishes through an API. This module performs no network
requests and reads no credentials. A future browser adapter must be separately
implemented for destinations whose current rules expressly permit submissions.
"""


class PublishError(Exception):
    """Compatibility exception for the retired publisher hook."""


def check_settings(cfg, now):
    """Keep legacy configurations from failing while never enabling a write."""
    return True


def run_publisher(con, cfg, now, **kwargs):
    """Explicitly report that no approved browser publisher is configured."""
    return {"status": "disabled_no_approved_browser_adapter", "published": 0}
