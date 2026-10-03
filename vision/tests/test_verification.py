from conftest import observe

from lab_vision.models import DeviationKind, StepStatus
from lab_vision.verification import ProtocolVerifier


def kinds(deviations):
    return [d.kind for d in deviations]


def correct_run(verifier):
    out = []
    out += verifier.ingest(observe("add-diluent", values={"volume": (900, 0.9)}))
    out += verifier.ingest(observe("transfer-sample", values={"destination": ("tube B", 0.9)}))
    out += verifier.ingest(observe("vortex"))
    return out


def test_correct_run_has_no_deviations(protocol):
    verifier = ProtocolVerifier(protocol)
    assert correct_run(verifier) == []
    assert verifier.finalize() == []
    assert verifier.done


def test_wrong_numeric_value(protocol):
    verifier = ProtocolVerifier(protocol)
    found = verifier.ingest(observe("add-diluent", values={"volume": (500, 0.9)}))
    assert kinds(found) == [DeviationKind.WRONG_VALUE]
    assert found[0].expected == 900.0 and found[0].observed == 500
    assert not found[0].needs_review


def test_numeric_value_within_tolerance_and_with_units_in_text(protocol):
    verifier = ProtocolVerifier(protocol)
    assert verifier.ingest(observe("add-diluent", values={"volume": ("902 uL", 0.9)})) == []


def test_unreadable_numeric_value_needs_review(protocol):
    found = ProtocolVerifier(protocol).ingest(
        observe("add-diluent", values={"volume": ("blurry", 0.9)})
    )
    assert kinds(found) == [DeviationKind.WRONG_VALUE]
    assert found[0].needs_review


def test_accepted_alias_matches(protocol):
    verifier = ProtocolVerifier(protocol)
    verifier.ingest(observe("add-diluent", values={"volume": (900, 0.9)}))
    assert verifier.ingest(observe("transfer-sample", values={"destination": ("B", 0.9)})) == []


def test_repeated_wrong_reading_is_reported_once(protocol):
    verifier = ProtocolVerifier(protocol)
    first = verifier.ingest(observe("add-diluent", StepStatus.IN_PROGRESS, {"volume": (500, 0.9)}))
    again = verifier.ingest(
        observe("add-diluent", StepStatus.IN_PROGRESS, {"volume": (500, 0.9)}, start=5)
    )
    assert len(first) == 1 and again == []


def test_low_confidence_observation_changes_nothing(protocol):
    verifier = ProtocolVerifier(protocol, min_confidence=0.6)
    unsure = observe("add-diluent", values={"volume": (500, 0.9)}, confidence=0.3)
    assert verifier.ingest(unsure) == []
    assert verifier.pending(1)[0].id == "add-diluent"


def test_low_confidence_reading_is_not_treated_as_a_mistake(protocol):
    found = ProtocolVerifier(protocol).ingest(observe("add-diluent", values={"volume": (500, 0.3)}))
    assert kinds(found) == [DeviationKind.UNVERIFIED_CHECK]


def test_skipped_step(protocol):
    verifier = ProtocolVerifier(protocol)
    found = verifier.ingest(observe("transfer-sample", values={"destination": ("tube B", 0.9)}))
    skipped = [d for d in found if d.kind is DeviationKind.SKIPPED_STEP]
    assert [d.step_id for d in skipped] == ["add-diluent"]


def test_skipped_step_performed_later_is_out_of_order(protocol):
    verifier = ProtocolVerifier(protocol)
    verifier.ingest(observe("transfer-sample", values={"destination": ("tube B", 0.9)}))
    found = verifier.ingest(observe("add-diluent", values={"volume": (900, 0.9)}, start=10))
    assert kinds(found) == [DeviationKind.OUT_OF_ORDER]


def test_unconfirmed_check_on_completion_needs_review(protocol):
    found = ProtocolVerifier(protocol).ingest(observe("add-diluent"))
    assert kinds(found) == [DeviationKind.UNVERIFIED_CHECK]
    assert found[0].needs_review


def test_unobserved_steps_flagged_at_end(protocol):
    verifier = ProtocolVerifier(protocol)
    verifier.ingest(observe("add-diluent", values={"volume": (900, 0.9)}))
    found = verifier.finalize()
    assert [d.step_id for d in found] == ["transfer-sample", "vortex"]
    assert all(d.needs_review for d in found)


def test_unexpected_event_deduplicated(protocol):
    verifier = ProtocolVerifier(protocol)
    event = observe(None, StepStatus.UNCLEAR, description="Tube dropped")
    assert kinds(verifier.ingest(event)) == [DeviationKind.UNEXPECTED_EVENT]
    assert verifier.ingest(event) == []


def test_earlier_step_seen_under_way_is_not_asserted_as_skipped(protocol):
    verifier = ProtocolVerifier(protocol)
    verifier.ingest(observe("add-diluent", StepStatus.IN_PROGRESS))
    found = verifier.ingest(
        observe("transfer-sample", values={"destination": ("tube B", 0.9)}, start=10)
    )
    incomplete = [d for d in found if d.kind is DeviationKind.INCOMPLETE_STEP]
    assert [d.step_id for d in incomplete] == ["add-diluent"]
    assert incomplete[0].needs_review
    assert DeviationKind.SKIPPED_STEP not in kinds(found)


def test_started_but_unfinished_step_is_incomplete_at_end(protocol):
    verifier = ProtocolVerifier(protocol)
    verifier.ingest(observe("add-diluent", values={"volume": (900, 0.9)}))
    verifier.ingest(observe("transfer-sample", StepStatus.IN_PROGRESS, start=5))
    found = {d.step_id: d.kind for d in verifier.finalize()}
    assert found == {
        "transfer-sample": DeviationKind.INCOMPLETE_STEP,
        "vortex": DeviationKind.SKIPPED_STEP,
    }
