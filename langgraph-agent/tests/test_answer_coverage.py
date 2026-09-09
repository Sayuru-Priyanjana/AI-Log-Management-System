"""
What the answer carries beyond the narrative.

Two facts are measured by the pipeline and attached by the verifier rather than
parsed out of the model's reply: every elevated stretch in the period asked
about, and what the system is doing now. Both are attached *because* the model
may fail to mention them — an answer that silently omits two of the three
failures in a range is the defect this closes, and one that cannot be fixed by
asking the model more nicely.

The transcript bound is here too. It is the same concern from the other side:
every step re-sends the whole investigation log, so an unbounded observation is
paid for on every remaining step and eventually pushes the run into the silent
truncation `PromptTruncated` exists to catch.
"""
from __future__ import annotations

from datetime import timedelta

from app.agents.react import trim_observation
from app.models.answer import AnswerMode, Episode, RecentStatus
from app.models.domain import TimeWindow
from app.pipeline.answer_check import verify_answer
from tests.conftest import T0, at
from tests.test_answer_check import complete_evidence


def episode(number: int, *, start: int, minutes: int, severity: str = "high",
            primary: bool = False) -> Episode:
    return Episode(
        id=f"ep:{number}",
        start=at(start), end=at(start + minutes * 60),
        peak_errors_per_min=40.0, mean_errors_per_min=30.0, total_errors=30 * minutes,
        elevation=10.0, severity=severity, primary=primary,
    )


def recent(status: str = "critical") -> RecentStatus:
    from app.models.domain import utcnow
    return RecentStatus(
        window=TimeWindow(start=utcnow() - timedelta(minutes=30), end=utcnow()),
        minutes=30, status=status, errors_per_min=22.0, errors=660, total_documents=5000,
        status_reason="errors are running at 22.0/min",
        summary="In the last 30 minutes the system is CRITICAL.",
    )


def swept(windows, episodes):
    return windows.model_copy(update={
        "episodes": episodes,
        "scanned": TimeWindow(start=T0, end=at(1800), label="scanned"),
        "sweep_method": f"{len(episodes)} elevated stretch(es) found",
    })


def check(raw, *, windows, mode=AnswerMode.ROOT_CAUSE, recent_status=None):
    return verify_answer(
        raw=raw, mode=mode, signals=[], candidates=[],
        evidence=complete_evidence(), windows=windows,
        exposed_ids=set(), recent_status=recent_status,
    )


def test_every_issue_in_the_range_reaches_the_answer_whatever_the_model_wrote(windows):
    """The list is attached from the measurement, not from the narrative.

    The model is asked to discuss all of them and, being a 7B model reading a
    long transcript, sometimes discusses one. The reader gets the list either
    way; that is the difference between an answer about the question and an
    answer about part of it.
    """
    episodes = [episode(1, start=0, minutes=6, primary=True),
                episode(2, start=900, minutes=5),
                episode(3, start=1500, minutes=4, severity="critical")]
    answer = check({"headline": "payment-db went down at 12:00", "detail": "It did."},
                   windows=swept(windows, episodes))

    assert [e.id for e in answer.window_issues] == ["ep:1", "ep:2", "ep:3"]
    assert sum(1 for e in answer.window_issues if e.primary) == 1


def test_issues_the_narrative_ignored_are_named_in_the_limitations(windows):
    episodes = [episode(1, start=0, minutes=6, primary=True),
                episode(2, start=900, minutes=5)]
    answer = check({"headline": "payment-db went down", "detail": "Only ep:1 is discussed."},
                   windows=swept(windows, episodes))

    unmentioned = [l for l in answer.limitations if "further elevated stretch" in l]
    assert unmentioned, "an unexplained issue must be stated, not silently dropped"
    assert "only the stretch analysed in depth" in unmentioned[0].lower()


def test_an_issue_the_narrative_did_discuss_is_not_flagged_as_ignored(windows):
    """Otherwise the caveat fires on every answer and stops meaning anything."""
    episodes = [episode(1, start=0, minutes=6, primary=True),
                episode(2, start=900, minutes=5)]
    answer = check({
        "headline": "payment-db went down",
        "detail": "A second, separate stretch [ep:2] ran later and was not diagnosed.",
    }, windows=swept(windows, episodes))

    assert not [l for l in answer.limitations if "further elevated stretch" in l]


def test_a_single_issue_produces_no_caveat(windows):
    episodes = [episode(1, start=0, minutes=6, primary=True)]
    answer = check({"headline": "payment-db went down", "detail": ""},
                   windows=swept(windows, episodes))

    assert not [l for l in answer.limitations if "further elevated stretch" in l]
    assert len(answer.window_issues) == 1


def test_a_still_broken_system_is_stated_even_when_the_window_has_closed(windows):
    """A diagnosis of a period that ended is not actionable on its own.

    The reader has to be told, in the answer rather than by starting a second
    investigation, that the thing is still happening.
    """
    answer = check({"headline": "payment-db went down at 12:10", "detail": "It recovered."},
                   windows=swept(windows, []), recent_status=recent("critical"))

    assert answer.recent_status is not None
    assert answer.recent_status.status == "critical"
    assert any("Right now" in l and "CRITICAL" in l for l in answer.limitations)


def test_a_healthy_present_is_carried_without_a_caveat(windows):
    """Nothing to warn about — but the reading is still attached, because
    "it has stopped" is as much of an answer as "it has not"."""
    answer = check({"headline": "payment-db went down", "detail": ""},
                   windows=swept(windows, []), recent_status=recent("healthy"))

    assert answer.recent_status.status == "healthy"
    assert not [l for l in answer.limitations if "Right now" in l]


def test_an_episode_id_can_be_cited_like_any_other_evidence(windows):
    """The sweep measured them before the model saw them, so a claim about the
    third failure in a range is checkable rather than taken on trust."""
    from app.models.answer import CitationStatus

    episodes = [episode(1, start=0, minutes=6, primary=True), episode(2, start=900, minutes=5)]
    answer = verify_answer(
        raw={"headline": "two failures",
             "reasoning": [{"claim": "a second stretch followed",
                            "evidence_ids": ["ep:2", "ep:99"]}]},
        mode=AnswerMode.ROOT_CAUSE, signals=[], candidates=[],
        evidence=complete_evidence(), windows=swept(windows, episodes),
        exposed_ids={"ep:1", "ep:2"},
    )
    by_id = {c.id: c for c in answer.citations}
    assert by_id["ep:2"].status is CitationStatus.RESOLVED
    assert by_id["ep:99"].status is CitationStatus.UNRESOLVED


# ---------------------------------------------------------------- transcript
def test_a_long_observation_is_bounded_at_a_line_edge():
    """Cutting mid-line would leave a half-written `sig:` for the model to
    complete from imagination — worse than the tokens it saved."""
    text = "\n".join(f"[sig:TYPE:service-{i}] something measured" for i in range(200))
    trimmed = trim_observation(text, 400)

    assert len(trimmed) < len(text)
    for line in trimmed.splitlines():
        assert line.startswith("[sig:") or line.startswith("…"), line


def test_a_bounded_observation_says_what_was_dropped():
    """"There were more" must never be mistaken for "that was all" — the loop
    treats an empty result as a finding and would report it as one."""
    text = "\n".join(f"line {i}" for i in range(100))
    trimmed = trim_observation(text, 200)

    assert "more line(s) not shown" in trimmed
    assert "narrow the arguments" in trimmed


def test_a_short_observation_is_left_exactly_as_it_is():
    text = "[sig:OOM_KILL:payment-api] the pod was OOM-killed"
    assert trim_observation(text, 1400) == text


def test_one_enormous_line_is_cut_at_a_word_boundary():
    text = "word " * 500
    trimmed = trim_observation(text, 100)
    assert len(trimmed) <= 120
    assert trimmed.endswith("…[truncated]")
