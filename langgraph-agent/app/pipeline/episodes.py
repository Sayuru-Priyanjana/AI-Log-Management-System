"""
Filling in what each elevated stretch actually contained.

`detect_episodes` finds *when* things went wrong across the whole period asked
about; it works entirely from the error histogram, so it knows how loud each
stretch was and nothing about what was in it. An issue list that says "something
was wrong at 09:14 for six minutes" three times over is a better answer than
silence and a worse one than it needs to be.

This adds the missing half: one aggregation per episode naming the services that
carried the errors and their commonest messages. Deliberately *not* a second
investigation each — no baseline comparison, no dependency graph, no signal
engine, no model call. The primary episode already gets all of that; the others
need enough for a reader to recognise whether they are the same failure
recurring or three different ones.

The queries run concurrently and are individually non-fatal: an episode whose
breakdown fails keeps its timing and rate, which came from the histogram and
cannot fail here.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.models.analysis import Episode
from app.models.domain import TimeWindow
from app.models.plan import InvestigationPlan
from app.tools.logs import LogTool

logger = logging.getLogger(__name__)


async def describe_episodes(log_tool: LogTool, plan: InvestigationPlan,
                            episodes: list[Episode], *,
                            limit: int | None = None) -> list[Episode]:
    """Names the services and errors inside each episode, in place.

    `limit` bounds the number of queries this can issue, independently of how
    many episodes were found. The sweep already caps the list, so this is the
    second belt: a pathological histogram that somehow yields fifty stretches
    must not turn into fifty round trips to OpenSearch.
    """
    limit = settings.max_reported_episodes if limit is None else limit
    if not episodes:
        return episodes

    # Worst first, so if the cap bites it is the smallest stretches that go
    # undescribed rather than whichever happened to be last.
    ordered = sorted(episodes,
                     key=lambda e: (not e.primary,
                                    -(e.mean_errors_per_min * e.minutes)))[:limit]

    results = await asyncio.gather(
        *(log_tool.snapshot(
            plan,
            TimeWindow(start=episode.start, end=episode.end, label=episode.id),
            top_services=settings.episode_top_services,
            top_messages=settings.episode_top_errors,
        ) for episode in ordered),
        return_exceptions=True,
    )

    for episode, snapshot in zip(ordered, results):
        if isinstance(snapshot, BaseException):
            logger.warning("Episode breakdown failed for %s: %s", episode.id, snapshot)
            episode.breakdown_status = "unavailable"
            continue
        if snapshot.status != "ok":
            episode.breakdown_status = "unavailable"
            continue
        episode.breakdown_status = "ok"
        episode.services = [
            name for name, _ in sorted(snapshot.errors_by_service.items(),
                                       key=lambda item: -item[1])
        ][: settings.episode_top_services]
        episode.top_errors = snapshot.top_errors[: settings.episode_top_errors]
        # The histogram counts every error bucket in range; the aggregation
        # counts documents. They should agree, and when they do not the
        # aggregation is the more precise of the two — it is not quantised to the
        # bucket edges the sweep had to work with.
        if snapshot.errors:
            episode.total_errors = snapshot.errors
            episode.mean_errors_per_min = round(snapshot.errors / episode.minutes, 2)

    return episodes


def summarise(episodes: list[Episode], sweep_method: str) -> str:
    """The whole period in a few lines, for the prompt and for a Teams card.

    Written so the *shape* of the range is readable without the histogram: how
    many distinct issues, how bad each was, and which one the analysis below is
    actually about.
    """
    if not episodes:
        return f"No elevated stretches were found in the period asked about — {sweep_method}."

    ongoing = [e for e in episodes if e.ongoing]
    head = (f"{len(episodes)} distinct issue(s) were found across the WHOLE period asked "
            f"about, listed earliest first.")
    if ongoing:
        head += (f" {len(ongoing)} of them had not resolved when the range ended.")
    lines = [head, ""]
    lines += [episode.summary_line() for episode in episodes]
    if any(not e.primary for e in episodes):
        lines.append("")
        lines.append(
            "Only the stretch marked [investigated in full below] has signals, a "
            "baseline comparison and a call graph behind it. The others are measured, "
            "not diagnosed — report them as separate issues with their times and "
            "rates, and say plainly that they were not investigated in depth."
        )
    return "\n".join(lines)
