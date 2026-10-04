from types import SimpleNamespace

import anthropic
import pytest

from lab_vision.llm import ClaudeLLM, LLMError
from lab_vision.models import StepStatus, TimeSpan
from lab_vision.perception import ClaudePerceiver, PerceptionError, PerceptionRequest
from lab_vision.perception.prompts import build_user_content
from lab_vision.perception.schema import EventOut, PerceptionOut, StepOut, ValueOut
from lab_vision.video import Frame, FrameWindow


@pytest.fixture
def request_(protocol):
    window = FrameWindow(TimeSpan(start_s=0, end_s=4), (Frame(0, 0.0, b"\xff\xd8x"),))
    return PerceptionRequest(window, tuple(protocol.steps[:2]), run_id="run-1")


class FakeLLM:
    model = "fake-model"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def generate(self, *, system, content, output):
        self.calls.append((system, content, output))
        if self.error:
            raise self.error
        return self.result


GOOD = PerceptionOut(
    observations=[
        StepOut(
            step_id="add-diluent",
            status=StepStatus.PERFORMED,
            confidence=0.8,
            values=[
                ValueOut(check_id="volume", value="900", confidence=0.9),
                ValueOut(check_id="invented", value="1", confidence=0.9),
            ],
            notes=None,
        ),
        StepOut(
            step_id="made-up-step",
            status=StepStatus.PERFORMED,
            confidence=0.9,
            values=[],
            notes=None,
        ),
    ],
    unexpected_events=[EventOut(description="Glove removed", confidence=0.7)],
)


def test_maps_response_to_observations_with_provenance(request_):
    observations = ClaudePerceiver(FakeLLM(GOOD)).observe(request_)

    assert [o.step_id for o in observations] == ["add-diluent", None]
    first = observations[0]
    assert first.status is StepStatus.PERFORMED
    assert [v.check_id for v in first.values] == ["volume"]  # unknown check dropped
    assert first.produced_by.model == "fake-model"
    assert first.produced_by.run_id == "run-1"
    assert first.span.end_s == 4 and first.frame_indices == [0]
    assert observations[1].description == "Glove removed"


def test_llm_failure_drops_the_window(request_):
    with pytest.raises(PerceptionError):
        ClaudePerceiver(FakeLLM(error=LLMError("declined"))).observe(request_)


def test_out_of_range_confidence_drops_the_window(request_):
    bad = PerceptionOut(
        observations=[
            StepOut(
                step_id="add-diluent",
                status=StepStatus.PERFORMED,
                confidence=7,
                values=[],
                notes=None,
            )
        ],
        unexpected_events=[],
    )
    with pytest.raises(PerceptionError):
        ClaudePerceiver(FakeLLM(bad)).observe(request_)


def test_prompt_has_frames_but_not_expected_values(request_):
    content = build_user_content(request_)
    text = "\n".join(p["text"] for p in content if p["type"] == "text")
    images = [p for p in content if p["type"] == "image"]

    assert "What volume is set" in text and "900" not in text
    assert len(images) == 1
    assert images[0]["source"]["media_type"] == "image/jpeg"


# ClaudeLLM, against a stand-in for the SDK client.


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def reply(parsed="ok", stop_reason="end_turn", category=None):
    return SimpleNamespace(
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        parsed_output=parsed,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category),
    )


def llm_with(messages, **kwargs):
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
    return ClaudeLLM(client=client, **kwargs)


def test_request_shape_with_fallbacks():
    messages = FakeMessages(reply("answer"))
    out = llm_with(messages, effort="low").generate(
        system="sys", content=[{"type": "text", "text": "hi"}], output=PerceptionOut
    )
    sent = messages.kwargs
    assert out == "answer"
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_format"] is PerceptionOut
    assert sent["output_config"] == {"effort": "low"}
    assert sent["fallbacks"] == "default"
    assert sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "temperature" not in sent and "thinking" not in sent and "tool_choice" not in sent


def test_effort_can_be_omitted_for_models_without_it():
    messages = FakeMessages(reply())
    llm_with(messages, effort=None).generate(system="s", content=[], output=PerceptionOut)
    assert "output_config" not in messages.kwargs


def test_fallbacks_can_be_turned_off():
    messages = FakeMessages(reply())
    llm_with(messages, fallbacks=False).generate(system="s", content=[], output=PerceptionOut)
    assert "fallbacks" not in messages.kwargs and "betas" not in messages.kwargs


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (reply(stop_reason="refusal", category="bio"), "bio"),
        (reply(stop_reason="max_tokens"), "cut off"),
        (reply(parsed=None), "no structured output"),
    ],
)
def test_unusable_responses_raise(response, message):
    with pytest.raises(LLMError, match=message):
        llm_with(FakeMessages(response)).generate(system="s", content=[], output=PerceptionOut)


def test_api_errors_become_llm_errors():
    error = anthropic.APIConnectionError(request=SimpleNamespace())
    with pytest.raises(LLMError):
        llm_with(FakeMessages(error=error)).generate(system="s", content=[], output=PerceptionOut)


def test_usage_and_served_model_are_tracked():
    llm = llm_with(FakeMessages(reply("a")))
    llm.generate(system="s", content=[], output=PerceptionOut)
    llm.generate(system="s", content=[], output=PerceptionOut)
    assert llm.usage == {"requests": 2, "input_tokens": 2000, "output_tokens": 400}
    assert llm.served_models == {"claude-opus-5-5"}


@pytest.mark.parametrize("fallbacks", [False, True])
def test_streamed_output_publishes_text_and_retains_validation_and_usage(fallbacks):
    class Stream:
        text_stream = iter(['{"observations":', ' []}'])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_final_message(self):
            return reply("validated")

    class Messages(FakeMessages):
        def stream(self, **kwargs):
            self.kwargs = kwargs
            return Stream()

    messages = Messages()
    llm = llm_with(messages, fallbacks=fallbacks)
    updates = []
    result = llm.generate_streamed(
        system="s", content=[], output=PerceptionOut, on_text=updates.append
    )
    assert updates == ['{"observations":', '{"observations": []}']
    assert result == "validated"
    assert messages.kwargs["output_format"] is PerceptionOut
    assert ("fallbacks" in messages.kwargs) == fallbacks
    assert llm.usage == {"requests": 1, "input_tokens": 1000, "output_tokens": 200}
