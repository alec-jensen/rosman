"""Background-cheap update checking against GitHub Releases.

Checks the GitHub Releases API for the latest tag, but at most once every
CHECK_INTERVAL -- most `rosman` invocations do nothing here beyond reading
a timestamp out of the already-loaded state file. Any failure (no network,
DNS, timeout, malformed JSON) is swallowed silently: a stale or failed
check must never make an otherwise-working command feel slow or broken.

The notice itself is throttled separately (NOTIFY_INTERVAL), so a user who
runs several rosman commands in one sitting sees it at most once, not on
every command -- "at some point during your session, not obnoxiously
every time," per the actual request this was built for.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from rosman.state import RosmanState

RELEASES_API_URL = "https://api.github.com/repos/alec-jensen/rosman/releases/latest"
REQUEST_TIMEOUT_SECONDS = 2.0
CHECK_INTERVAL = timedelta(hours=24)
NOTIFY_INTERVAL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _due(last: str | None, interval: timedelta) -> bool:
    parsed = _parse(last)
    return parsed is None or _now() - parsed > interval


def check_for_update(state: RosmanState) -> None:
    """Refresh the cached latest-version info if CHECK_INTERVAL has
    elapsed since the last check. Always returns normally -- network
    failures are swallowed, not raised, since this must never be the
    reason a rosman command fails or feels slow."""
    if not _due(state.update_check.last_checked, CHECK_INTERVAL):
        return
    state.update_check.last_checked = _now().isoformat()
    try:
        request = urllib.request.Request(
            RELEASES_API_URL, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read())
        tag = data.get("tag_name") or ""
        state.update_check.latest_version = tag.lstrip("v") or None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        pass
    state.save()


def pending_notice(state: RosmanState, current_version: str) -> str | None:
    """A one-line notice if a different (cached) latest version exists and
    NOTIFY_INTERVAL has elapsed since it was last shown, else None. Callers
    are also expected to gate actually printing this on stderr being a
    real terminal, so scripted/CI usage never sees it."""
    latest = state.update_check.latest_version
    if not latest or latest == current_version:
        return None
    if not _due(state.update_check.last_notified, NOTIFY_INTERVAL):
        return None
    state.update_check.last_notified = _now().isoformat()
    state.save()
    return (
        f"A newer rosman is available: {latest} (you have {current_version}). "
        "Update via your package manager, or see "
        "https://github.com/alec-jensen/rosman#installation."
    )
