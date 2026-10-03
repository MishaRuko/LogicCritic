import pytest

from lab_vision.llm import LLMError
from lab_vision.models import CheckKind
from lab_vision.protocol import load_protocol, save_protocol
from lab_vision.structuring import (
    CheckDraft,
    ProtocolDraft,
    StepDraft,
    StructuringError,
    structure_protocol,
)

TEXT = """1. Add 5 μL of plasmid DNA to the competent E. coli cells.
2. Mix gently by flicking the tube 4-5 times.
3. Incubate on ice for 5 seconds.
4. Transfer the tube to a pre-heated 42°C water bath."""


def step(n, quote, checks=(), **kw):
    return StepDraft(
        id=f"s{n}",
        source_text=quote,
        description=quote,
        objects=[],
        checks=list(checks),
        optional=False,
        **kw,
    )


def check(**kw):
    base = dict(
        id="volume",
        question="What volume is set on the pipette?",
        kind="numeric",
        expected="5",
        unit="μL",
        target="handheld pipette with digital display",
        tolerance=None,
        accept=[],
    )
    return CheckDraft(**{**base, **kw})


def good_draft():
    return ProtocolDraft(
        steps=[
            step(1, "Add 5 μL of plasmid DNA to the competent E. coli cells.", [check()]),
            step(2, "Mix gently by flicking the tube 4-5 times."),
            step(3, "Incubate on ice for 5 seconds."),
            step(
                4,
                "Transfer the tube to a pre-heated 42°C water bath.",
                [
                    check(
                        id="temp",
                        question="What temperature does the bath show?",
                        expected="42",
                        unit="°C",
                    )
                ],
            ),
        ]
    )


class ScriptedLLM:
    model = "fake"

    def __init__(self, *drafts):
        self.drafts, self.prompts = list(drafts), []

    def generate(self, *, system, content, output):
        self.prompts.append(content[0]["text"])
        return self.drafts.pop(0)


def test_valid_draft_becomes_a_protocol():
    protocol = structure_protocol(TEXT, ScriptedLLM(good_draft()), "mock", "Mock")

    assert [s.id for s in protocol.steps] == ["s1", "s2", "s3", "s4"]
    volume = protocol.steps[0].checks[0]
    assert volume.kind is CheckKind.NUMERIC and volume.expected == 5.0 and volume.unit == "μL"
    assert protocol.steps[0].source_text.startswith("Add 5 μL")
    assert protocol.steps[2].checks == []


def test_target_description_is_carried_into_the_protocol():
    protocol = structure_protocol(TEXT, ScriptedLLM(good_draft()), "mock", "Mock")
    assert protocol.steps[0].checks[0].target == "handheld pipette with digital display"


def test_invented_quantity_is_rejected_then_repaired():
    bad = good_draft()
    bad.steps[0].checks[0].expected = "50"
    llm = ScriptedLLM(bad, good_draft())

    protocol = structure_protocol(TEXT, llm, "mock", "Mock")

    assert protocol.steps[0].checks[0].expected == 5.0
    assert "expected value '50' is not in the step" in llm.prompts[1]


def test_rewritten_source_text_is_rejected():
    bad = good_draft()
    bad.steps[1].source_text = "Gently flick the tube."
    with pytest.raises(StructuringError, match="verbatim"):
        structure_protocol(TEXT, ScriptedLLM(bad, bad), "mock", "Mock")


def test_question_that_leaks_the_answer_is_rejected():
    bad = good_draft()
    bad.steps[0].checks[0].question = "Is the pipette set to 5?"
    with pytest.raises(StructuringError, match="gives away"):
        structure_protocol(TEXT, ScriptedLLM(bad, bad), "mock", "Mock")


def test_steps_out_of_order_are_rejected():
    bad = good_draft()
    bad.steps[1], bad.steps[2] = bad.steps[2], bad.steps[1]
    with pytest.raises(StructuringError, match="order"):
        structure_protocol(TEXT, ScriptedLLM(bad, bad), "mock", "Mock")


def test_llm_errors_propagate():
    class Failing:
        model = "fake"

        def generate(self, **_):
            raise LLMError("declined")

    with pytest.raises(LLMError):
        structure_protocol(TEXT, Failing(), "mock", "Mock")


def test_saved_yaml_round_trips(tmp_path):
    protocol = structure_protocol(TEXT, ScriptedLLM(good_draft()), "mock", "Mock")
    path = tmp_path / "protocol.yaml"
    save_protocol(protocol, path)

    assert "μL" in path.read_text(encoding="utf-8")  # not escaped
    assert load_protocol(path) == protocol
