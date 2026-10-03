import re

from lab_vision.models import (
    Check,
    CheckKind,
    Deviation,
    DeviationKind,
    Observation,
    Protocol,
    ProtocolStep,
    StepStatus,
    TimeSpan,
)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def check_matches(check: Check, observed: str | float) -> bool | None:
    """Compare an observed reading with a check. None means it could not be interpreted."""
    if check.kind is CheckKind.NUMERIC:
        if isinstance(observed, str):
            found = _NUMBER.search(observed)
            if found is None:
                return None
            observed = float(found.group())
        return abs(observed - float(check.expected)) <= check.tolerance
    accepted = {_normalise(str(check.expected)), *(_normalise(a) for a in check.accept)}
    return _normalise(str(observed)) in accepted


class ProtocolVerifier:
    """Deterministic comparison of observations against a protocol.

    Models propose observations. This class alone decides whether they are deviations, so the
    same observations always produce the same findings. Findings are append-only: a later
    correct reading does not retract an earlier deviation.
    """

    def __init__(self, protocol: Protocol, min_confidence: float = 0.6, run_id: str | None = None):
        self.protocol = protocol
        self.min_confidence = min_confidence
        self.run_id = run_id
        self._completed: set[str] = set()
        self._skipped: set[str] = set()
        self._started: set[str] = set()
        self._confirmed: set[tuple[str, str]] = set()
        self._flagged: set[tuple[str, str]] = set()
        self._seen: set[tuple] = set()

    def pending(self, limit: int) -> tuple[ProtocolStep, ...]:
        """The next steps still expected, in protocol order."""
        waiting = [
            s
            for s in self.protocol.steps
            if s.id not in self._completed and s.id not in self._skipped
        ]
        return tuple(waiting[:limit])

    @property
    def done(self) -> bool:
        return all(
            s.id in self._completed or s.id in self._skipped or s.optional
            for s in self.protocol.steps
        )

    def ingest(self, observation: Observation) -> list[Deviation]:
        if observation.confidence < self.min_confidence:
            return []
        if observation.step_id is None:
            return self._unexpected(observation)
        step = self.protocol.step(observation.step_id)
        if step is None or observation.status not in (StepStatus.PERFORMED, StepStatus.IN_PROGRESS):
            return []
        self._started.add(step.id)
        found = self._check_values(step, observation)
        if observation.status is StepStatus.PERFORMED:
            found += self._complete(step, observation)
        return found

    def finalize(self, span: TimeSpan | None = None) -> list[Deviation]:
        """Call once the recording ends. Required steps never confirmed are flagged for review."""
        found = []
        for step in self.protocol.steps:
            if step.optional or step.id in self._completed or step.id in self._skipped:
                continue
            self._skipped.add(step.id)
            if step.id in self._started:
                found.append(
                    self._deviation(
                        DeviationKind.INCOMPLETE_STEP,
                        "step_not_completed",
                        f"Step {step.id!r} was started but never seen completed.",
                        step=step,
                        needs_review=True,
                        span=span,
                    )
                )
            else:
                found.append(
                    self._deviation(
                        DeviationKind.SKIPPED_STEP,
                        "step_not_observed",
                        f"Step {step.id!r} was not observed before the recording ended.",
                        step=step,
                        needs_review=True,
                        span=span,
                    )
                )
        return found

    def _unexpected(self, obs: Observation) -> list[Deviation]:
        key = ("unexpected", obs.description)
        if key in self._seen:
            return []
        self._seen.add(key)
        return [
            self._deviation(
                DeviationKind.UNEXPECTED_EVENT,
                "unexpected_event",
                f"Event outside the protocol: {obs.description}",
                observed=obs.description,
                needs_review=True,
                observations=[obs],
            )
        ]

    def _check_values(self, step: ProtocolStep, obs: Observation) -> list[Deviation]:
        found = []
        for reading in obs.values:
            check = step.check(reading.check_id)
            if check is None or reading.value is None:
                continue
            if reading.confidence < self.min_confidence:
                continue
            key = (step.id, check.id)
            matched = check_matches(check, reading.value)
            if matched:
                self._confirmed.add(key)
                continue
            seen_key = ("wrong", *key, str(reading.value))
            if seen_key in self._seen:
                continue
            self._seen.add(seen_key)
            self._flagged.add(key)
            found.append(
                self._deviation(
                    DeviationKind.WRONG_VALUE,
                    "value_mismatch",
                    f"Step {step.id!r}, {check.question} Expected {check.expected}"
                    f"{_unit(check)}, observed {reading.value}.",
                    step=step,
                    check=check,
                    observed=reading.value,
                    needs_review=matched is None,
                    observations=[obs],
                )
            )
        return found

    def _complete(self, step: ProtocolStep, obs: Observation) -> list[Deviation]:
        if step.id in self._completed:
            return []
        found: list[Deviation] = []
        if step.id in self._skipped:
            self._skipped.discard(step.id)
            found.append(
                self._deviation(
                    DeviationKind.OUT_OF_ORDER,
                    "step_out_of_order",
                    f"Step {step.id!r} was performed after later steps had already been done.",
                    step=step,
                    observations=[obs],
                )
            )
        else:
            for earlier in self.protocol.steps[: self.protocol.index_of(step.id)]:
                if earlier.optional or earlier.id in self._completed | self._skipped:
                    continue
                self._skipped.add(earlier.id)
                if earlier.id in self._started:
                    # Seen under way, so not asserted as skipped, but completion was never seen.
                    found.append(
                        self._deviation(
                            DeviationKind.INCOMPLETE_STEP,
                            "step_not_confirmed_complete",
                            f"Step {step.id!r} was performed, but step {earlier.id!r} was only "
                            "seen under way and never confirmed complete.",
                            step=earlier,
                            needs_review=True,
                            observations=[obs],
                        )
                    )
                else:
                    found.append(
                        self._deviation(
                            DeviationKind.SKIPPED_STEP,
                            "step_skipped",
                            f"Step {step.id!r} was performed but earlier step {earlier.id!r} "
                            "was not observed.",
                            step=earlier,
                            observations=[obs],
                        )
                    )
        self._completed.add(step.id)
        for check in step.checks:
            key = (step.id, check.id)
            if key in self._confirmed or key in self._flagged:
                continue
            found.append(
                self._deviation(
                    DeviationKind.UNVERIFIED_CHECK,
                    "check_not_confirmed",
                    f"Step {step.id!r}: {check.question} could not be confirmed from the video.",
                    step=step,
                    check=check,
                    needs_review=True,
                    observations=[obs],
                )
            )
        return found

    def _deviation(
        self,
        kind: DeviationKind,
        rule: str,
        message: str,
        *,
        step: ProtocolStep | None = None,
        check: Check | None = None,
        observed: str | float | None = None,
        needs_review: bool = False,
        observations: list[Observation] | None = None,
        span: TimeSpan | None = None,
    ) -> Deviation:
        observations = observations or []
        return Deviation(
            kind=kind,
            rule=rule,
            step_id=step.id if step else None,
            check_id=check.id if check else None,
            expected=check.expected if check else None,
            observed=observed,
            message=message,
            needs_review=needs_review,
            observation_ids=[o.id for o in observations],
            span=span or (observations[0].span if observations else None),
            run_id=self.run_id,
        )


def _unit(check: Check) -> str:
    return f" {check.unit}" if check.unit else ""
