"""The lab's real protocol for the recorded demo footage, switched on with DEMO_PROTOCOL=true.

The demo recordings (LSV mock transformation) were filmed against this protocol before the
research agent produced one of its own. With the switch on, every protocol prepared for an
experiment is this one, whatever source was selected, and it is labelled as a demo fixture.
It is off by default and never used otherwise.
"""

from lab_vision.models import Protocol

from app.services.experiments import numbered_protocol

LAB_PROTOCOL_TITLE = "Mock bacterial transformation (lab protocol, demo)"
LAB_PROTOCOL = """\
1. Using a pipette set to 5 μL, add plasmid DNA to the competent E. coli cells.

2. Mix gently by flicking the tube 4-5 times.

3. Incubate the DNA-cell mixture on ice for 5 seconds.

4. Rapidly transfer the tube to a thermal cycler pre-heated to 42°C and keep it there for exactly 5 seconds.

5. Immediately return the tube to ice for 5 seconds.

6. Using a pipette set to 50 μL, spread culture onto an LB agar plate containing the appropriate selective antibiotic.

7. Invert the plates.
"""  # noqa: E501


def lab_protocol(protocol_id: str) -> tuple[Protocol, str, dict]:
    """The protocol, how it was obtained, and an (empty) citation list per step."""
    protocol = numbered_protocol(LAB_PROTOCOL, protocol_id, LAB_PROTOCOL_TITLE)
    assert protocol is not None
    return protocol, "demo_fixture", {step.id: [] for step in protocol.steps}

