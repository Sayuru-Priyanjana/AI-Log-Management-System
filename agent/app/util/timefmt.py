"""
One clock for the whole system.

Every timestamp is stored and reasoned about in UTC — that is not negotiable,
because windows, onsets and baselines are compared arithmetically and a local
offset that shifts twice a year would corrupt the comparison. But nothing the
*reader* sees should be in UTC unless they asked for it: an investigation whose
answer says "the departure began at 05:12" while the dashboard beside it says
10:42 is two facts the reader has to reconcile by hand, every time.

So display formatting goes through here, on both sides of the wire. The agent
renders its own prose — window descriptions, signal onsets, timeline entries —
in the configured zone, and the UI formats the ISO strings it receives in the
same one. `set_zone` lets the running process follow a change made from the
configuration page without a restart.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo

try:                                    # tzdata is present in the image
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - 3.8 and below
    ZoneInfo = None                     # type: ignore[assignment]

_OFFSET = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{1,2}):?(?P<minutes>\d{2})$")

DEFAULT_ZONE = "+05:30"

_zone: tzinfo = timezone(timedelta(hours=5, minutes=30))
_label: str = DEFAULT_ZONE


def parse_zone(value: str) -> tuple[tzinfo, str]:
    """Accepts either a fixed offset ("+05:30") or an IANA name ("Asia/Colombo").

    Returns the zone and the label to show beside a time. A name is resolved to
    a real zone rather than a fixed offset so daylight saving is handled where it
    applies; the label stays the name, because "Europe/London" tells the reader
    more than "+01:00" does in July.
    """
    text = (value or "").strip()
    if not text or text.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc, "UTC"

    match = _OFFSET.match(text)
    if match:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        if hours > 23 or minutes > 59:
            raise ValueError(f"offset out of range: {value}")
        delta = timedelta(hours=hours, minutes=minutes)
        if match.group("sign") == "-":
            delta = -delta
        sign = "-" if delta < timedelta(0) else "+"
        total = abs(delta)
        label = f"{sign}{total.seconds // 3600:02d}:{(total.seconds % 3600) // 60:02d}"
        return timezone(delta), label

    if ZoneInfo is None:
        raise ValueError("named time zones are unavailable; use an offset like +05:30")
    try:
        return ZoneInfo(text), text
    except Exception as exc:            # noqa: BLE001 - any lookup failure is the same answer
        raise ValueError(f"unknown time zone '{value}'") from exc


def set_zone(value: str) -> str:
    """Switches the display zone for this process. Returns the label."""
    global _zone, _label
    _zone, _label = parse_zone(value)
    return _label


def label() -> str:
    return _label


def local(moment: datetime) -> datetime:
    """The same instant, expressed in the display zone.

    A naive datetime is taken as UTC: everything in this system is stored in UTC,
    and guessing the host's zone instead would make the output depend on where
    the container happens to run.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_zone)


def clock(moment: datetime | None, fallback: str = "unknown") -> str:
    """`10:42:00` — for the many places a time appears inside a sentence."""
    return local(moment).strftime("%H:%M:%S") if moment else fallback


def stamp(moment: datetime | None, fallback: str = "unknown") -> str:
    """`2026-08-12 10:42:00 +05:30` — where the date and zone both matter."""
    return f"{local(moment):%Y-%m-%d %H:%M:%S} {_label}" if moment else fallback
