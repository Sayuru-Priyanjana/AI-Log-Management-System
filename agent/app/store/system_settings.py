"""
Per-cluster settings: where its detections get reported, and whether to notify
automatically.

Keyed by system id rather than kept as a single process-wide document, because
a Teams channel and an auto-scan cadence are properties of *that* cluster, not
of the agent process. Two systems must be able to point at two different
channels; a global value would either be wrong for every system but one, or
would silently route every cluster's alerts to whichever channel was set last.
"""
from __future__ import annotations

import re

from app.sources.opensearch import OpenSearchClient

INDEX = "logintel-system-config"

# "Scan" is the agent investigating this system, not a separate detection
# engine — there is no other kind of scan. What each flag controls:
#   auto_scan_enabled              run an investigation against this system
#                                   once a day, at scan_time
#   scan_time                      when that daily run happens, "HH:MM"
#   notify_on_alert_enabled        post to the integrations above when an
#                                   alert/detection is recorded for this system
#   notify_on_scan_result_enabled  post the scheduled scan's own answer to the
#                                   integrations above once it finishes
#   auto_investigate_alerts_enabled automatically run an investigation on new alerts
DEFAULTS: dict = {
    "teams_channel_name": "",
    "teams_webhook_url": "",
    "auto_scan_enabled": False,
    "scan_time": "03:00",
    "notify_on_alert_enabled": False,
    "auto_investigate_alerts_enabled": False,
    "notify_on_scan_result_enabled": False,
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate(values: dict) -> dict:
    """Rejects an obviously-wrong value before it is saved.

    A malformed webhook URL means the next ping fails with a confusing error
    instead of a clear one, so the cheap check happens here.
    """
    cleaned = dict(values)
    if "teams_webhook_url" in cleaned:
        url = str(cleaned["teams_webhook_url"] or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL must start with http:// or https://")
        cleaned["teams_webhook_url"] = url
    if "teams_channel_name" in cleaned:
        cleaned["teams_channel_name"] = str(cleaned["teams_channel_name"] or "").strip()
    if "scan_time" in cleaned:
        text = str(cleaned["scan_time"] or "").strip()
        if not _TIME_RE.match(text):
            raise ValueError("Scan time must be in 24-hour HH:MM form, e.g. 03:00")
        cleaned["scan_time"] = text
    for flag in ("auto_scan_enabled", "notify_on_alert_enabled", "auto_investigate_alerts_enabled", "notify_on_scan_result_enabled"):
        if flag in cleaned and not isinstance(cleaned[flag], bool):
            cleaned[flag] = str(cleaned[flag]).strip().lower() in ("1", "true", "yes", "on")
    unknown = set(cleaned) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    return cleaned


class SystemSettingsStore:
    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    def rebind(self, client: OpenSearchClient) -> None:
        self._client = client

    async def get(self, system_id: str) -> dict:
        try:
            stored = await self._client.get_document(INDEX, system_id)
        except Exception:                   # noqa: BLE001 - degraded, not fatal
            stored = None
        # Filtered to the current schema. A document saved under an earlier
        # field name (scan_interval_minutes, before it became scan_time)
        # otherwise leaks that stale key back out — and since the UI round-trips
        # whatever `get` returns on the next `save`, an unrecognised key would
        # then make every future save to that system fail validation.
        known = {k: v for k, v in (stored or {}).items() if k in DEFAULTS}
        return {**DEFAULTS, **known}

    async def save(self, system_id: str, values: dict) -> bool:
        current = await self.get(system_id)
        current.update(values)
        try:
            await self._client.index_document(INDEX, current, doc_id=system_id)
            return True
        except Exception:                   # noqa: BLE001
            return False
