"""
The tools the ReAct loop can call.

Two principles govern what belongs here:

**Every tool returns evidence IDs.** A tool that returns prose the model can only
paraphrase produces an answer nobody can check. Each observation carries the IDs
(`sig:`, `pat:`, `evt:`, `met:`) the model is expected to cite, and the verifier
later confirms those IDs refer to something that was actually collected.

**Measurements come from the signal engine, not from the model's arithmetic.**
The engine already compares CPU against its own limit, memory against its own
limit, and every rate against a baseline window. Letting the loop re-derive
"ratio > 1.5 means a spike" from raw averages throws that away and reintroduces
exactly the unit and baseline errors the engine exists to prevent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.models.analysis import Candidate, InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan
from app.models.signals import Signal
from app.util.timefmt import clock

ERROR_LEVELS = ("ERROR", "FATAL", "CRITICAL")


def _levels(value: str | list | None) -> set[str]:
    """Parses a level filter leniently.

    The model writes `level="ERROR, WARN"` because that is a reasonable thing to
    mean, and rejecting it cost a whole investigation: the tool reported no logs
    of level "ERROR, WARN", the model believed it, and answered "no log patterns
    matched" while six signals sat uncollected. Accepting the obvious intent is
    better than being right about the schema.
    """
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = re.split(r"[,\s/|]+", str(value))
    return {p.strip().upper() for p in parts if p.strip()}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, str]
    handler: str


class ToolResult:
    """A tool observation plus the evidence IDs it exposed.

    The IDs are tracked separately from the text so the verifier can tell the
    difference between an ID the model was shown and one it invented.
    """

    def __init__(self, text: str, evidence_ids: list[str] | None = None,
                 table: dict | None = None) -> None:
        self.text = text
        self.evidence_ids = evidence_ids or []
        self.table = table

    def __str__(self) -> str:
        return self.text


class ToolBindings:
    def __init__(self, plan: InvestigationPlan, windows: InvestigationWindows,
                 evidence: EvidenceBundle, signals: list[Signal] | None = None,
                 candidates: list[Candidate] | None = None) -> None:
        self.plan = plan
        self.windows = windows
        self.evidence = evidence
        self.signals = signals or []
        self.candidates = candidates or []
        # Every ID the loop has legitimately been shown.
        self.exposed_ids: set[str] = set()
        self.call_log: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------ facts
    def get_signals(self, service_name: str = "all") -> ToolResult:
        """The measured, baseline-relative facts. The most important tool here."""
        matching = [
            s for s in self.signals
            if service_name in ("all", "", None) or s.service == service_name
        ]
        if not matching:
            scoped = service_name not in ("all", "", None)
            scope = f"'{service_name}'" if scoped else "any service"
            message = (f"No signals crossed their thresholds for {scope}. Nothing measured "
                       f"departed from its baseline — that is a finding, not missing data.")
            # A scoped query that finds nothing must say what fired elsewhere.
            # Without this the loop concluded "no root cause found" for payment-db
            # while an ERROR_RATE_SPIKE sat unmentioned on payment-api — it had
            # asked one narrow question and taken the silence as an answer.
            if scoped and self.signals:
                elsewhere = sorted({f"{s.type.value} on {s.service or s.pod or 'system'}"
                                    for s in self.signals})
                message += (f" However {len(self.signals)} signal(s) DID fire on other "
                            f"services: {'; '.join(elsewhere[:5])}. A service can be the "
                            f"victim of a failure that shows up elsewhere — call "
                            f"get_signals('all') before concluding nothing is wrong.")
            return ToolResult(message)

        lines = ["Measured signals (each compared against the baseline window, "
                 "ordered by when it started):"]
        ids = []
        rows = []
        for signal in matching:
            magnitude = f" | {signal.magnitude.describe()}" if signal.magnitude else ""
            onset = clock(signal.first_seen)
            flag = " [PRE-EXISTING: began before the window, cannot be the trigger]" \
                if signal.pre_existing else ""
            lines.append(
                f"- [{signal.id}] {signal.type.value} ({signal.severity.value}) "
                f"service={signal.service or '-'} onset={onset}{magnitude}{flag}\n"
                f"    {signal.description}"
            )
            ids.append(signal.id)
            rows.append([signal.id, signal.type.value, signal.severity.value,
                         signal.service or "-", onset,
                         signal.magnitude.describe() if signal.magnitude else "",
                         signal.description])

        # A signal list is a table of records, and "which spikes happened" is a
        # retrieval question as much as "which log lines matched". Without this
        # the extraction path had no payload to return, the answer was scored as
        # having found nothing, and the UI showed prose where it should show rows.
        table = {
            "columns": ["id", "signal", "severity", "service", "onset",
                        "magnitude", "what was measured"],
            "rows": rows,
            "total_matched": len(rows),
            "truncated": False,
            "query_description": (
                f"signals detected for "
                f"{'all services' if service_name in ('all', '', None) else service_name}"
            ),
        }
        return ToolResult("\n".join(lines), ids, table)

    def get_hypotheses(self, _: str = "") -> ToolResult:
        """Pre-ranked candidate explanations from the rule engine."""
        if not self.candidates:
            return ToolResult("The rule engine produced no candidate explanations.")

        lines = ["Candidate explanations, ranked by deterministic rules. "
                 "These are computed from the signals, not guessed:"]
        ids = []
        for candidate in self.candidates:
            lines.append(
                f"- [{candidate.id}] score={candidate.score:.2f} "
                f"{candidate.category.value} service={candidate.service or '-'}\n"
                f"    {candidate.hypothesis}\n"
                f"    why: {candidate.rationale[:300]}"
            )
            if candidate.contradicting_signals:
                lines.append(f"    argues against: {', '.join(candidate.contradicting_signals)}")
            ids.append(candidate.id)
        return ToolResult("\n".join(lines), ids)

    def get_dependencies(self, service_name: str = "all") -> ToolResult:
        """The call graph, observed from the services' own dependency logs."""
        edges = self.evidence.logs.dependency_edges
        if not edges:
            return ToolResult("No dependency relationships were observed in the logs.")

        if service_name in ("all", "", None):
            lines = ["Observed call graph (caller -> callees). Failures propagate "
                     "UPWARD, so the deepest failing service is the likely root:"]
            for caller, callees in sorted(edges.items()):
                depth = self.evidence.logs.depth_of(caller)
                lines.append(f"- {caller} (depth {depth}) calls: {', '.join(callees)}")
            leaves = {c for callees in edges.values() for c in callees} - set(edges)
            if leaves:
                lines.append(f"- {', '.join(sorted(leaves))} (depth 0) call nothing — "
                             f"bottom of the chain")
            return ToolResult("\n".join(lines))

        callees = edges.get(service_name, [])
        depth = self.evidence.logs.depth_of(service_name)
        callers = [c for c, targets in edges.items() if service_name in targets]
        parts = [f"'{service_name}' sits at depth {depth} in the call graph."]
        parts.append(f"It calls: {', '.join(callees)}" if callees
                     else "It calls nothing — it is at the bottom of the chain.")
        parts.append(f"It is called by: {', '.join(callers)}" if callers
                     else "Nothing observed calls it.")
        return ToolResult(" ".join(parts))

    # ------------------------------------------------------------- raw evidence
    def get_service_logs(self, service_name: str = "all", level: str = "") -> ToolResult:
        logs = self.evidence.logs
        if logs.status != "ok":
            return ToolResult(f"Log data unavailable: {logs.reason or logs.status}")

        wanted = _levels(level)
        patterns = [
            p for p in logs.patterns
            if (service_name in ("all", "", None) or p.service == service_name)
            and (not wanted or p.level in wanted)
        ]
        if not patterns:
            return ToolResult(
                f"No log patterns matched service='{service_name}' level='{level or 'any'}'."
            )

        lines = [f"Log patterns for {service_name} ({len(patterns)} distinct templates):"]
        ids = []
        for pattern in patterns:
            first = clock(pattern.first_seen, "?")
            new = " [NEW: absent from the baseline window]" if pattern.is_new else ""
            growth = (f" [{pattern.growth:.1f}x baseline]"
                      if pattern.growth and pattern.growth > 1.5 else "")
            lines.append(
                f"- [{pattern.id}] {pattern.level} x{pattern.count}{new}{growth} from {first}\n"
                f"    \"{pattern.example[:220]}\""
            )
            ids.append(pattern.id)
        return ToolResult("\n".join(lines), ids)

    def get_service_events(self, service_name: str = "all") -> ToolResult:
        events = self.evidence.events
        if events.status != "ok":
            return ToolResult(f"Event data unavailable: {events.reason or events.status}")

        matching = [
            e for e in events.events
            if (service_name in ("all", "", None)
                or e.service == service_name
                or (e.pod or "").startswith(service_name))
            and e.severity != "info"
        ]
        if not matching:
            return ToolResult(
                f"No warning-level Kubernetes events for '{service_name}'. "
                f"(Routine Pulled/Started/Scheduled events are filtered out.)"
            )

        lines = [f"Kubernetes events for {service_name}:"]
        ids = []
        for event in matching:
            onset = clock(event.onset, "?")
            lines.append(
                f"- [{event.id}] {event.type}/{event.reason} x{event.count} "
                f"pod={event.pod or '-'} from {onset}\n    {event.message[:220]}"
            )
            ids.append(event.id)
        return ToolResult("\n".join(lines), ids)

    def get_service_metrics(self, service_name: str = "all") -> ToolResult:
        metrics = self.evidence.metrics
        if metrics.status == "unavailable":
            return ToolResult(f"Metric data unavailable: {metrics.reason}")

        series = [
            s for s in metrics.series
            if service_name in ("all", "", None)
            or s.service == service_name
            or (s.pod or "").startswith(service_name)
        ]
        if not series:
            return ToolResult(f"No metric series found for '{service_name}'.")

        moved, flat = [], 0
        for item in series:
            ratio = item.ratio_to_baseline()
            if ratio is not None and (ratio >= 1.5 or ratio <= 0.66):
                moved.append((abs(ratio - 1), item, ratio))
            else:
                flat += 1
        if not moved:
            return ToolResult(
                f"All {len(series)} metric series for '{service_name}' are within "
                f"1.5x of their baseline. Note that thresholds against limits "
                f"(CPU/memory saturation) are evaluated by get_signals, not here."
            )

        moved.sort(key=lambda item: -item[0])
        lines = [f"Metrics for {service_name} that moved against baseline "
                 f"({flat} others were flat):"]
        ids = []
        for _, item, ratio in moved[:15]:
            scope = item.pod or item.service or "-"
            base = (f"{item.baseline.average:.4g}"
                    if item.baseline and item.baseline.average is not None else "n/a")
            lines.append(
                f"- [{item.id}] {item.metric} ({scope}): {ratio:.1f}x baseline "
                f"— now {item.incident.average:.4g} {item.unit}, was {base}"
            )
            ids.append(item.id)
        return ToolResult("\n".join(lines), ids)

    def get_timeline(self, _: str = "") -> ToolResult:
        """Everything that happened, in order. Answers 'what happened first'."""
        entries: list[tuple[Any, str]] = []
        for signal in self.signals:
            if signal.first_seen:
                entries.append((signal.first_seen,
                                f"[{signal.id}] {signal.type.value} on "
                                f"{signal.service or signal.pod or 'system'}"))
        for pattern in self.evidence.logs.patterns:
            if pattern.is_new and pattern.level in ERROR_LEVELS and pattern.first_seen:
                entries.append((pattern.first_seen,
                                f"[{pattern.id}] first occurrence of a new error in "
                                f"{pattern.service}: \"{pattern.example[:110]}\""))
        for event in self.evidence.events.events:
            if event.severity != "info" and event.onset:
                entries.append((event.onset,
                                f"[{event.id}] Kubernetes {event.reason} on "
                                f"{event.pod or event.involved_name}"))
        if not entries:
            return ToolResult("No time-ordered events to report.")

        entries.sort(key=lambda item: item[0])
        lines = ["Chronological timeline (earliest first — the top of this list is "
                 "the closest thing to a trigger):"]
        ids = []
        for when, text in entries[:25]:
            lines.append(f"  {clock(when)}  {text}")
            found = re.search(r"\[([a-z]+:[^\]]+)\]", text)
            if found:
                ids.append(found.group(1))
        return ToolResult("\n".join(lines), ids)

    # ------------------------------------------------------ retrieval / counting
    def search_logs(self, query: str = "", service_name: str = "all",
                    level: str = "", limit: int = 20) -> ToolResult:
        """Find actual log lines matching a substring.

        This is what a "show me the errors mentioning timeout" question needs:
        the records themselves, not a summary of them.
        """
        logs = self.evidence.logs
        if logs.status != "ok":
            return ToolResult(f"Log data unavailable: {logs.reason or logs.status}")

        needle = (query or "").lower()
        wanted = _levels(level)
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 20

        rows, ids, sample_ids = [], [], []
        for sample in logs.samples:
            if needle and needle not in sample.message.lower():
                continue
            if wanted and sample.level not in wanted:
                continue
            if service_name not in ("all", "", None) and service_name.lower() not in (sample.service or "").lower():
                continue
            rows.append([clock(sample.timestamp), sample.level,
                         sample.service or "-", sample.message[:160]])
            ids.append(sample.id)
            sample_ids.append(sample.id)

        # Samples are a bounded slice of the window; patterns cover all of it, so
        # they are the honest source for "what matched" even when the individual
        # lines behind them were not retained.
        pattern_rows, pattern_ids = [], []
        for pattern in logs.patterns:
            if needle and needle not in pattern.example.lower():
                continue
            if wanted and pattern.level not in wanted:
                continue
            if service_name not in ("all", "", None) and service_name.lower() not in (pattern.service or "").lower():
                continue
            pattern_rows.append([str(pattern.count), pattern.level,
                                 pattern.service or "-", pattern.example[:160]])
            ids.append(pattern.id)
            pattern_ids.append(pattern.id)

        if not rows and not pattern_rows:
            # An empty result with no explanation makes the loop guess, and it
            # guesses badly — one live run burned five steps re-searching the same
            # term at every log level in turn. Saying what *is* present lets it
            # correct itself in a single step.
            return ToolResult(self._nothing_matched(query, service_name, level))

        table = {
            "columns": ["occurrences", "level", "service", "message"],
            "rows": pattern_rows[:limit],
            "total_matched": sum(int(r[0]) for r in pattern_rows),
            "truncated": len(pattern_rows) > limit,
            "query_description": (f"log patterns matching '{query or 'any'}' "
                                  f"for service '{service_name}' "
                                  f"level '{level or 'any'}'"),
        }
        # Each row leads with its evidence ID. Without it the model has nothing
        # to copy and invents one instead — a live run cited "met:ERROR
        # payment-api", which is not an ID at all, because none was ever shown.
        lines = [f"{len(pattern_rows)} distinct pattern(s) matched, "
                 f"{table['total_matched']} occurrences total. "
                 f"Cite these by the ID in brackets:"]
        for row, pattern_id in zip(pattern_rows[:limit], pattern_ids[:limit]):
            lines.append(f"- [{pattern_id}] x{row[0]} {row[1]} {row[2]}: \"{row[3]}\"")
        if rows:
            lines.append(f"\nExample individual lines ({len(rows[:5])} of {len(rows)}):")
            for row, sample_id in zip(rows[:5], sample_ids[:5]):
                lines.append(f"  [{sample_id}] {row[0]} {row[1]} {row[2]}: {row[3]}")
        return ToolResult("\n".join(lines), ids, table)

    def _nothing_matched(self, query: str, service_name: str, level: str) -> str:
        """Explains an empty result by describing what the window does contain."""
        logs = self.evidence.logs
        services = sorted({p.service for p in logs.patterns if p.service})
        levels = sorted({p.level for p in logs.patterns})

        parts = [f"Nothing matched query='{query or 'any'}' service='{service_name}' "
                 f"level='{level or 'any'}'."]

        # The commonest mistake by far: passing a level name as the text query.
        if query and query.upper() in {"ERROR", "WARN", "WARNING", "INFO", "DEBUG",
                                       "FATAL", "CRITICAL"}:
            parts.append(
                f"NOTE: '{query}' is a log level, but `query` matches message TEXT. "
                f"To filter by level use level='{query.upper()}' and leave query empty."
            )
        if level and level.upper() not in levels:
            parts.append(f"NOTE: no logs of level '{level}' exist here. "
                         f"Levels present: {', '.join(levels) or 'none'}.")
        if service_name not in ("all", "", None) and not any(service_name.lower() in (s or "").lower() for s in services):
            parts.append(f"NOTE: no logs for service containing '{service_name}'. "
                         f"Services present: {', '.join(services) or 'none'}.")

        parts.append(f"This window holds {logs.total_documents} documents across "
                     f"{len(logs.patterns)} patterns "
                     f"(levels: {', '.join(levels) or 'none'}; "
                     f"services: {', '.join(services) or 'none'}). "
                     f"Widen the filter rather than repeating the same search.")
        return " ".join(parts)

    def count_logs(self, group_by: str = "level", service_name: str = "all") -> ToolResult:
        """Counts, grouped. Answers "how many" without inventing arithmetic."""
        logs = self.evidence.logs
        if logs.status != "ok":
            return ToolResult(f"Log data unavailable: {logs.reason or logs.status}")

        field = (group_by or "level").lower()
        counts: dict[str, int] = {}
        for pattern in logs.patterns:
            if service_name not in ("all", "", None) and service_name.lower() not in (pattern.service or "").lower():
                continue
            key = {"level": pattern.level,
                   "service": pattern.service or "unknown",
                   "pattern": pattern.template[:80]}.get(field)
            if key is None:
                return ToolResult(
                    f"Cannot group by '{group_by}'. Available: level, service, pattern."
                )
            counts[key] = counts.get(key, 0) + pattern.count

        if not counts:
            return ToolResult(f"No log data to count for service='{service_name}'.")

        ordered = sorted(counts.items(), key=lambda item: -item[1])
        window_minutes = self.windows.incident.minutes
        lines = [f"Counts by {field} over the incident window "
                 f"({window_minutes:.0f} minutes), from {logs.total_documents} documents:"]
        for key, value in ordered:
            lines.append(f"- {key}: {value} ({value / window_minutes:.1f}/min)")
        if logs.baseline_totals_by_level and field == "level":
            lines.append("Baseline window for comparison: " + ", ".join(
                f"{k}={v}" for k, v in logs.baseline_totals_by_level.items()))
        table = {
            "columns": [field, "count", "per_minute"],
            "rows": [[k, str(v), f"{v / window_minutes:.1f}"] for k, v in ordered],
            "total_matched": sum(counts.values()),
            "truncated": False,
            "query_description": f"log counts grouped by {field} for '{service_name}'",
        }
        return ToolResult("\n".join(lines), [], table)

    def get_investigation_scope(self, _: str = "") -> ToolResult:
        """What was actually examined — window, baseline, and any gaps.

        Exposed as a tool so the loop can discover the limits of its own evidence
        rather than assuming the window covers whatever the user had in mind.
        """
        parts = [
            f"Question: {self.plan.goal}",
            f"System: {self.plan.system_id} / {self.plan.environment}",
            f"Focus service: {self.plan.service or 'whole system'}",
            f"Incident window analysed: {self.windows.incident}",
            f"Baseline window compared against: {self.windows.baseline or 'NONE AVAILABLE'}",
            f"Onset detection: {self.windows.method}",
        ]
        if self.windows.onset_before_window:
            parts.append("WARNING: the incident began before the window examined, so its "
                         "true start was not observed.")
        gaps = self.evidence.gaps()
        parts.append("Evidence gaps: " + ("; ".join(gaps) if gaps else "none"))
        parts.append(f"Documents examined: {self.evidence.logs.total_documents} logs "
                     f"({self.evidence.logs.baseline_documents} in baseline), "
                     f"{len(self.evidence.events.events)} events, "
                     f"{len(self.evidence.metrics.series)} metric series")
        return ToolResult("\n".join(parts))

    # ---------------------------------------------------------------- dispatch
    SPECS: tuple[ToolSpec, ...] = (
        ToolSpec("get_signals",
                 "The measured facts: every threshold crossing, compared against the "
                 "baseline window and against resource limits. START HERE for any "
                 "incident question — these are computed, not estimated.",
                 {"service_name": "service name, or 'all'"}, "get_signals"),
        ToolSpec("get_hypotheses",
                 "Candidate explanations already ranked by deterministic rules, with "
                 "their supporting and contradicting signals.",
                 {}, "get_hypotheses"),
        ToolSpec("get_dependencies",
                 "The observed call graph and each service's depth. Failures propagate "
                 "upward, so use this to tell a root cause from a symptom.",
                 {"service_name": "service name, or 'all'"}, "get_dependencies"),
        ToolSpec("get_timeline",
                 "Everything that happened in chronological order. Use it to establish "
                 "what came first.",
                 {}, "get_timeline"),
        ToolSpec("get_service_logs",
                 "Log patterns for a service, with counts and baseline comparison.",
                 {"service_name": "service name, or 'all'",
                  "level": "optional: ERROR, WARN, INFO"}, "get_service_logs"),
        ToolSpec("get_service_events",
                 "Warning-level Kubernetes events (restarts, OOM kills, probe failures).",
                 {"service_name": "service name, or 'all'"}, "get_service_events"),
        ToolSpec("get_service_metrics",
                 "Metric series that moved against their baseline.",
                 {"service_name": "service name, or 'all'"}, "get_service_metrics"),
        ToolSpec("search_logs",
                 "Find log records matching a substring. Use this when the user asks "
                 "to see, list or extract specific log entries.",
                 {"query": "substring to match", "service_name": "service or 'all'",
                  "level": "optional level filter", "limit": "max rows (default 20)"},
                 "search_logs"),
        ToolSpec("count_logs",
                 "Count log volume grouped by level, service or pattern. Use this when "
                 "the user asks how many, or for a rate.",
                 {"group_by": "level | service | pattern",
                  "service_name": "service or 'all'"}, "count_logs"),
        ToolSpec("get_investigation_scope",
                 "What was actually examined: windows, baseline, evidence gaps. Use it "
                 "before concluding that something is absent.",
                 {}, "get_investigation_scope"),
    )

    def execute(self, action: str, inputs: dict[str, Any]) -> ToolResult:
        spec = next((s for s in self.SPECS if s.name == action), None)
        if spec is None:
            available = ", ".join(s.name for s in self.SPECS)
            return ToolResult(f"Error: unknown tool '{action}'. Available: {available}")

        self.call_log.append((action, dict(inputs or {})))
        handler: Callable[..., ToolResult] = getattr(self, spec.handler)
        allowed = {k: v for k, v in (inputs or {}).items() if k in spec.parameters}
        try:
            result = handler(**allowed) if allowed else handler()
        except TypeError as exc:
            return ToolResult(
                f"Error calling {action}: {exc}. Expected parameters: "
                f"{list(spec.parameters)}"
            )
        except Exception as exc:                       # a tool fault must not end the run
            return ToolResult(f"Tool '{action}' failed: {exc}")

        self.exposed_ids.update(result.evidence_ids)
        return result

    @classmethod
    def schema(cls) -> str:
        return json.dumps(
            [{"name": s.name, "description": s.description, "parameters": s.parameters}
             for s in cls.SPECS],
            indent=2,
        )
