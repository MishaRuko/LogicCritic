"""How settled the agent's answer is, as a named level rather than a number.

A percentage would claim a precision nothing here has. The level is read off the guardrail's own
state, so it moves only when the argument changes: it rises as the agent closes what the verifier
found, falls when it finds a flaw, and reaches `settled` only when an `established` answer has
been accepted. `holding_back` says in words what stands in the way.
"""

from dataclasses import dataclass

# Ordered from most to least uncertain. A UI draws one segment per entry, in this order.
SCALE = ["unexplored", "exploring", "contested", "provisional", "well_supported", "settled"]

LABELS = {
    "unexplored": "No position yet",
    "exploring": "A position, not yet tested",
    "contested": "A flaw in the argument",
    "provisional": "Evidence gaps remain",
    "well_supported": "Nothing found against it",
    "settled": "Established",
}

# Obligations that mean the argument itself is unsound, not merely incomplete.
UNSOUND = {
    "invalidated_source",
    "ungrounded_statement",
    "withdrawn_premise",
    "unresolved_conflict",
    "direct_conflict",
    "unreasoned_conclusion",
    "causal_design_not_shown",
    "causality_overclaim",
    "scope_leap",
}

MAX_REASONS = 5
REASON_CHARS = 220


@dataclass(frozen=True)
class Assurance:
    level: str
    holding_back: list[dict]

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "label": LABELS[self.level],
            "scale": SCALE,
            "holding_back": self.holding_back,
        }


def from_check(obligations: list, established: bool = False, ceiling=None) -> Assurance:
    """The level after the verifier looked at a conclusion. `obligations` are guardrail ones;
    `ceiling` is the evidence ceiling (app.agent.evidence), when it was measured."""
    critical = [o for o in obligations if o.severity == "critical"]
    reasons = [
        {"kind": o.kind, "description": o.description[:REASON_CHARS]}
        for o in critical[:MAX_REASONS]
    ]
    if not critical:
        evidence = ceiling.by.get("evidence") if ceiling is not None else None
        thin = evidence is not None and evidence not in ("supported", "established")
        if thin:
            # Nothing against it, but too little for it: one source is not "well supported".
            return Assurance(
                "provisional",
                [
                    {"kind": "thin_evidence", "description": text[:REASON_CHARS]}
                    for text in ceiling.to_raise[:MAX_REASONS]
                ],
            )
        return Assurance("settled" if established else "well_supported", [])
    if any(o.kind in UNSOUND for o in critical):
        return Assurance("contested", reasons)
    return Assurance("provisional", reasons)


def exploring() -> Assurance:
    return Assurance("exploring", [])


def unexplored() -> Assurance:
    return Assurance("unexplored", [])
