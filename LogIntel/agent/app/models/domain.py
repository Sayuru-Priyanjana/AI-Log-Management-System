from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from app.util.timefmt import clock, stamp

_DURATION = re.compile(r"^(\d+)([smhdw])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(value: str) -> timedelta:
    """'30m' -> timedelta(minutes=30). Raises on anything else."""
    match = _DURATION.match((value or "").strip())
    if not match:
        raise ValueError(f"invalid duration {value!r}; expected forms like '30m', '2h', '7d'")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def ensure_utc(value: datetime) -> datetime:
    """Naive datetimes are assumed UTC. Mixing naive and aware values silently
    shifts every correlation by the local offset, so nothing is left naive."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class TimeWindow(BaseModel):
    start: datetime
    end: datetime
    label: str = ""

    def model_post_init(self, _context) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start))
        object.__setattr__(self, "end", ensure_utc(self.end))

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def seconds(self) -> float:
        return max(self.duration.total_seconds(), 1.0)

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    def contains(self, moment: datetime) -> bool:
        return self.start <= ensure_utc(moment) <= self.end

    def shifted_back(self, by: timedelta, length: timedelta | None = None) -> "TimeWindow":
        end = self.start - by
        return TimeWindow(start=end - (length or self.duration), end=end)

    def as_iso(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    def __str__(self) -> str:
        return f"{stamp(self.start)} .. {clock(self.end)} ({self.minutes:.0f}m)"

    @classmethod
    def last(cls, duration: str | timedelta, *, now: datetime | None = None, label: str = "") -> "TimeWindow":
        delta = parse_duration(duration) if isinstance(duration, str) else duration
        end = now or utcnow()
        return cls(start=end - delta, end=end, label=label)


class ServiceDescriptor(BaseModel):
    name: str
    namespaces: list[str] = Field(default_factory=list)
    log_count: int = 0
    tier: str | None = None


class SystemDescriptor(BaseModel):
    """What the agent knows exists. The orchestrator may only choose from here,
    which is what stops it inventing a service name that matches nothing."""

    id: str
    name: str
    environments: list[str] = Field(default_factory=list)
    namespaces: list[str] = Field(default_factory=list)
    services: list[ServiceDescriptor] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=utcnow)

    @property
    def service_names(self) -> list[str]:
        return [service.name for service in self.services]

    def resolve_service(self, candidate: str | None) -> str | None:
        """Maps a model-supplied service name onto a real one, or returns None.

        Exact match first, then case-insensitive, then unambiguous substring.
        A near-miss like 'payment' for 'payment-api' is worth accepting; an
        ambiguous one that matches two services is not.
        """
        if not candidate:
            return None
        candidate = candidate.strip()
        names = self.service_names
        if candidate in names:
            return candidate

        lowered = candidate.lower()
        exact = [n for n in names if n.lower() == lowered]
        if exact:
            return exact[0]

        partial = [n for n in names if lowered in n.lower() or n.lower() in lowered]
        return partial[0] if len(partial) == 1 else None
