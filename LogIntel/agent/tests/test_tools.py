from __future__ import annotations

from app.agents.tool_bindings import ToolBindings
from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, MetricEvidence
from app.models.signals import Magnitude, Severity, Signal, SignalType
from tests.conftest import at, event, pattern, series


def bindings(*, logs=None, events=None, metrics=None, signals=None,
             plan=None, windows=None) -> ToolBindings:
    return ToolBindings(
        plan, windows,
        EvidenceBundle(logs=logs or LogEvidence(),
                       events=events or EventEvidence(),
                       metrics=metrics or MetricEvidence()),
        signals or [], [],
    )


def sample_logs() -> LogEvidence:
    return LogEvidence(
        patterns=[
            pattern("payment-api failed to process the request",
                    service="payment-api", count=37, baseline_count=0),
            pattern("Upstream dependency payment-db failed",
                    service="payment-api", count=14, baseline_count=2),
            pattern("Payment processed successfully", service="payment-api",
                    count=900, baseline_count=880, level="INFO"),
        ],
        totals_by_level={"ERROR": 51, "INFO": 900},
        total_documents=951,
        dependency_edges={"checkout-api": ["payment-api"], "payment-api": ["payment-db"]},
    )


# --------------------------------------------------------------------------
# Every ID a tool tracks must appear in the text it returns.
# --------------------------------------------------------------------------
def test_every_tracked_id_is_visible_in_the_observation(plan, windows):
    """Regression: search_logs tracked pattern IDs but never printed them, so the
    model had nothing to copy and fabricated `met:ERROR payment-api` instead.

    An ID the loop is expected to cite but is never shown is worse than no ID at
    all — it guarantees an unverifiable answer.
    """
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    for action, args in (
        ("search_logs", {"level": "ERROR"}),
        ("get_service_logs", {"service_name": "payment-api"}),
    ):
        result = tools.execute(action, args)
        assert result.evidence_ids, f"{action} returned no IDs to cite"
        for evidence_id in result.evidence_ids:
            assert evidence_id in result.text, (
                f"{action} tracked '{evidence_id}' but never showed it to the model"
            )


def test_signals_are_reported_with_their_measured_magnitude(plan, windows):
    signals = [Signal(
        id="sig:MEMORY_PRESSURE:payment-api-abc12", type=SignalType.MEMORY_PRESSURE,
        severity=Severity.HIGH, service="payment-api", first_seen=at(600),
        description="payment-api reached 96% of its memory limit",
        magnitude=Magnitude(baseline=0.2, incident=0.96, unit="of memory limit"),
    )]
    result = bindings(signals=signals, plan=plan, windows=windows).execute("get_signals", {})

    assert "sig:MEMORY_PRESSURE:payment-api-abc12" in result.text
    assert "of memory limit" in result.text, "the unit must survive to the model"
    assert "baseline" in result.text.lower(), "the comparison must be visible"


def test_a_pre_existing_signal_is_flagged_as_unable_to_be_the_trigger(plan, windows):
    signals = [Signal(
        id="sig:READINESS_FAILURE:loadgen", type=SignalType.READINESS_FAILURE,
        severity=Severity.MEDIUM, service="loadgen", first_seen=at(600),
        description="probe failing", pre_existing=True,
    )]
    result = bindings(signals=signals, plan=plan, windows=windows).execute("get_signals", {})
    assert "PRE-EXISTING" in result.text
    assert "cannot be the trigger" in result.text


# --------------------------------------------------------------------------
# An empty result must explain itself.
# --------------------------------------------------------------------------
def test_a_level_passed_as_a_text_query_is_diagnosed(plan, windows):
    """Observed live: the loop called search_logs(query='ERROR', level='WARN') and
    then spent four more steps re-searching every level in turn. The tool now
    says what went wrong so it can correct itself in one step."""
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("search_logs", {"query": "ERROR", "level": "WARN"})

    assert "is a log level" in result.text
    assert "level='ERROR'" in result.text
    assert "matches message TEXT" in result.text


def test_an_empty_result_lists_what_is_actually_present(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("search_logs", {"service_name": "billing-api"})

    assert "no logs for service 'billing-api'" in result.text.lower()
    assert "payment-api" in result.text, "should name the services that do exist"


def test_a_nonexistent_level_is_named_as_such(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("search_logs", {"level": "TRACE"})
    assert "no logs of level 'TRACE'" in result.text
    assert "ERROR" in result.text


# --------------------------------------------------------------------------
# Retrieval and counting
# --------------------------------------------------------------------------
def test_search_returns_a_table_for_extraction_answers(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("search_logs", {"level": "ERROR"})

    assert result.table is not None
    assert result.table["total_matched"] == 51
    assert result.table["columns"] == ["occurrences", "level", "service", "message"]


def test_counting_reports_a_rate_not_just_a_total(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("count_logs", {"group_by": "level"})

    assert "per_minute" in (result.table or {}).get("columns", [])
    assert "/min" in result.text


def test_counting_rejects_an_unknown_grouping_with_the_valid_options(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("count_logs", {"group_by": "pod"})
    assert "Cannot group by 'pod'" in result.text
    assert "level, service, pattern" in result.text


# --------------------------------------------------------------------------
# Dependency direction
# --------------------------------------------------------------------------
def test_the_call_graph_states_which_way_failures_travel(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("get_dependencies", {"service_name": "all"})

    assert "upward" in result.text.lower() or "UPWARD" in result.text
    assert "depth" in result.text.lower()
    assert "payment-db" in result.text


def test_a_leaf_service_is_identified_as_the_bottom_of_the_chain(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("get_dependencies", {"service_name": "payment-db"})
    assert "depth 0" in result.text
    assert "bottom of the chain" in result.text


# --------------------------------------------------------------------------
# Dispatch safety
# --------------------------------------------------------------------------
def test_an_unknown_tool_lists_the_real_ones(plan, windows):
    result = bindings(plan=plan, windows=windows).execute("get_everything", {})
    assert "unknown tool" in result.text
    assert "get_signals" in result.text


def test_unexpected_parameters_are_ignored_rather_than_crashing(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    result = tools.execute("get_service_logs",
                           {"service_name": "payment-api", "nonsense": 42})
    assert "payment-api" in result.text
    # the log messages themselves contain the word "failed", so check the marker
    # the dispatcher would actually emit
    assert not result.text.startswith("Error calling")
    assert "Tool 'get_service_logs' failed" not in result.text


def test_a_tool_fault_is_reported_not_raised(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    # limit is coerced; a non-numeric value must not end the investigation
    result = tools.execute("search_logs", {"level": "ERROR", "limit": "lots"})
    assert result.text
    assert "Traceback" not in result.text


def test_exposed_ids_accumulate_across_calls(plan, windows):
    tools = bindings(logs=sample_logs(), plan=plan, windows=windows)
    tools.execute("search_logs", {"level": "ERROR"})
    tools.execute("get_service_logs", {"service_name": "payment-api"})
    assert len(tools.exposed_ids) >= 2
    assert all(i.startswith(("pat:", "log:")) for i in tools.exposed_ids)
