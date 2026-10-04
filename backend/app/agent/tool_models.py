"""Inputs of the tools a research agent can call.

Each model becomes a strict tool schema, so the API guarantees well-formed calls. Strict mode
cannot enforce numeric bounds or string formats, so the toolbox validates those itself and
answers a bad call with an error the agent can read and correct.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SearchPapersInput(BaseModel):
    query: str = Field(
        description="What to search for in the biomedical literature (PubMed-scale)."
    )
    limit: int = Field(description="How many results, 1 to 15.")
    published_after: str | None = Field(
        description="Earliest publication date as YYYY-MM-DD, or null."
    )
    exclude_retracted: bool = Field(description="True to leave out retracted papers.")


class ReadPaperInput(BaseModel):
    amass_id: str | None = Field(
        description="An Amass ID from search_papers (starts AMBC_), or null."
    )
    pmid: str | None = Field(description="A PubMed ID, or null.")
    doi: str | None = Field(description="A DOI, or null.")


class ReadSourceInput(BaseModel):
    source_id: str = Field(description="The source_id returned by read_paper or fetch_url.")
    offset: int = Field(
        description="Index of the first excerpt to read; use next_offset to continue."
    )


class LinkClaimsInput(BaseModel):
    source_statement_id: str = Field(description="The claim that supports, rebuts or qualifies.")
    target_statement_id: str = Field(description="The claim it bears on; any claim in the graph.")
    relation: Literal["supports", "rebuts", "qualifies"] = Field(
        description="supports = independent evidence for the same proposition; rebuts = directly "
        "incompatible; qualifies = narrows its scope or conditions."
    )
    rationale: str = Field(description="Why the relation holds, from the claims' cited text.")


class GraphSearchInput(BaseModel):
    query: str = Field(description="Words to look for in the claims of the workspace's argument graph.")
    limit: int = Field(description="How many claims, 1 to 25.")


class GraphNodeInput(BaseModel):
    node_id: str = Field(
        description="A statement_id or step_id from the graph: returns its evidence, the reasoning "
        "that derives it, what it supports, its links and open obligations."
    )


class TraceChainInput(BaseModel):
    statement_id: str = Field(description="The claim to trace from.")
    direction: Literal["support", "consequences"] = Field(
        description="support = what the claim rests on; consequences = what rests on the claim."
    )
    depth: int = Field(description="How many reasoning steps to follow, 1 to 6.")


class GraphOverviewInput(BaseModel):
    pass


class FetchUrlInput(BaseModel):
    url: str = Field(description="A public http(s) web page or PDF to read as evidence.")


class ScopeInput(BaseModel):
    population: str | None = Field(description="Who or what the claim is about, or null.")
    model: str | None = Field(
        description="human, mouse, cell line, in vitro, simulation, ... or null."
    )
    endpoint: str | None = Field(description="The outcome measured, or null.")


CAUSAL_DESIGNS = (
    "randomised_trial",
    "meta_analysis_of_randomised_trials",
    "natural_experiment_or_mendelian",
)
NON_CAUSAL_DESIGNS = ("observational", "animal_or_in_vitro", "other")


class CausalSupportInput(BaseModel):
    design: Literal[
        "randomised_trial",
        "meta_analysis_of_randomised_trials",
        "natural_experiment_or_mendelian",
        "observational",
        "animal_or_in_vitro",
        "other",
    ] = Field(description="The study design the cited text describes.")
    justification: str = Field(
        description="What in the cited text shows that design (for example 'randomly assigned')."
    )


class RecordClaimInput(BaseModel):
    text: str = Field(
        description="One atomic claim, worded as the source supports it and no more strongly."
    )
    excerpt_ids: list[str] = Field(
        description=(
            "Excerpt IDs, copied from read_paper/read_source/fetch_url results, that contain this "
            "claim. Use an empty list ONLY for your own inference, which must then be justified by "
            "a reasoning step."
        )
    )
    assertion_mode: Literal["asserted", "reported", "hypothesis", "conditional"] = Field(
        description="reported = a source says it; asserted = you state it."
    )
    role: Literal["premise", "conclusion", "assumption", "objection", "definition"] | None = Field(
        description="The claim's role in your argument, or null."
    )
    claim_strength: Literal["causal", "associative", "descriptive"] | None = Field(
        description=(
            "What the claim asserts: causal (X causes Y), associative (X is linked to Y) or "
            "descriptive. Null if not applicable. Be honest: this is checked."
        )
    )
    causal_support: CausalSupportInput | None = Field(
        description=(
            "REQUIRED when claim_strength is causal, otherwise null. The study design behind a "
            "causal reading and what in the cited text shows it. Only randomised trials, "
            "meta-analyses of them, and natural experiments support a causal claim; observational, "
            "animal and in-vitro evidence do not, so use associative wording for those."
        )
    )
    scope: ScopeInput | None = Field(
        description="The population, model and endpoint it covers, or null."
    )
    criteria_satisfied: list[int] = Field(
        description="Indexes (from 0) of the completion criteria this claim gives evidence for."
    )


class ScopeChangeInput(BaseModel):
    from_scope: str = Field(
        description="The scope of the premises, for example 'mouse, tumour volume'."
    )
    to_scope: str = Field(description="The scope of the conclusion, for example 'human, survival'.")
    justified: bool = Field(
        description="True only if a recorded claim bridges the gap (say which in the explanation)."
    )


class RecordReasoningInput(BaseModel):
    premise_ids: list[str] = Field(
        description="statement_ids of the claims this step relies on (at least one)."
    )
    conclusion_id: str = Field(description="The statement_id this step concludes. Record it first.")
    explanation: str = Field(
        description="Why the premises, taken together, support the conclusion."
    )
    scope_change: ScopeChangeInput | None = Field(
        description="Fill in when the conclusion covers a different population, model or outcome."
    )
    revises_step_id: str | None = Field(
        description="A step_id this one replaces (e.g. after adding a missing premise), or null."
    )
    weighing: str | None = Field(
        default=None,
        description="When the conclusion depends on how you weigh the evidence, state the "
        "principle, for example 'randomised trials outrank observational studies for causal "
        "effects' or 'larger, lower-bias studies outrank smaller, weaker ones'. It must be a "
        "recognised principle of evidence appraisal that fits these premises, not one chosen to "
        "suit the conclusion. Null if the premises settle it without weighing.",
    )


class ReviseClaimInput(BaseModel):
    statement_id: str = Field(
        description="The statement_id of a claim you recorded and now retract."
    )
    reason: str = Field(
        description="Why: contradicted by later evidence, rests on a retracted or unreliable "
        "source, or worded more strongly than its source."
    )
    replaced_by_id: str | None = Field(
        description="The statement_id of the corrected claim, recorded first with record_claim, "
        "or null to withdraw the claim with no replacement."
    )


class ProtocolStepInput(BaseModel):
    action: str = Field(
        description="One action a person performs, as the source reports it, with the exact "
        "volumes, temperatures and times, and the instrument that sets each volume or "
        "temperature (for example 'with a pipette set to 5 µL', 'in a water bath set to "
        "42 °C') when the source says which. Pick the value the source's own procedure uses; do "
        "not write ranges or alternatives. Never add a step the sources do not state."
    )
    excerpt_ids: list[str] = Field(
        description="excerpt_ids you have read that state this step (at least one)."
    )


class RecordProtocolInput(BaseModel):
    title: str = Field(description="What the procedure does, e.g. 'Heat-shock transformation'.")
    steps: list[ProtocolStepInput] = Field(
        description="The physical steps performed at the bench, in order, one action per step. "
        "Leave out data analysis and statistics: the steps are followed by a person and "
        "watched on video."
    )
    basis: str = Field(
        description="Which source(s) this follows and, where sources differ, which you chose "
        "and why."
    )


class CheckConclusionInput(BaseModel):
    statement_id: str = Field(description="The statement_id of the conclusion you intend to give.")


class FinalizeConclusionInput(BaseModel):
    statement_id: str = Field(description="The statement_id of your conclusion.")
    certainty: Literal["established", "conditional", "hypothesis"] = Field(
        description=(
            "established = the evidence settles it; conditional = holds with stated caveats; "
            "hypothesis = a possibility worth testing. 'established' is refused while critical "
            "obligations are open."
        )
    )
    verdict: str = Field(
        default="",
        description=(
            "Your direct answer to exactly what the question asks, in one short sentence (for "
            "example the verdict label it requests). It must agree with the conclusion; the "
            "conclusion carries the evidence and deductions."
        ),
    )
    protocol: Literal["recorded", "none"] = Field(
        default="none",
        description=(
            "'recorded' if you called record_protocol for a procedure your evidence describes; "
            "'none' only if your question involves no procedure someone could carry out. Decide "
            "deliberately: a procedure in the evidence you read should be handed over."
        ),
    )


class AbstainInput(BaseModel):
    reason: str = Field(description="Why the evidence does not support any conclusion.")


# name -> (description, model). The order is the order the agent sees them in.
RESEARCH_TOOLS: dict[str, tuple[str, type[BaseModel]]] = {
    "search_papers": (
        "Search the biomedical literature (Amass BiomedCore). Returns titles, abstracts, dates, "
        "citation counts and whether each paper is retracted. Does not save anything.",
        SearchPapersInput,
    ),
    "read_paper": (
        "Import one paper into the workspace so you can read and cite it. Returns a source_id and "
        "an index of its excerpts. Retracted papers are flagged. Call read_source to read text.",
        ReadPaperInput,
    ),
    "fetch_url": (
        "Fetch a public web page or PDF and save it as a source. Use this to read a page you found "
        "by web search BEFORE relying on it: search snippets are not evidence.",
        FetchUrlInput,
    ),
    "read_source": (
        "Read the text of a source's excerpts, with the excerpt_id of each. Cite only excerpts you "
        "have read.",
        ReadSourceInput,
    ),
}

GRAPH_TOOLS = {
    "graph_overview": (
        "Summarize the workspace's existing argument graph: claim and step counts, sources, "
        "conclusions. The graph holds claims extracted from material and recorded in earlier runs.",
        GraphOverviewInput,
    ),
    "search_graph": (
        "Search the claims already in the workspace's argument graph (lexical match). Returns "
        "statement_ids to inspect with get_graph_node or trace_chain.",
        GraphSearchInput,
    ),
    "get_graph_node": (
        "Read one claim or reasoning step of the graph with its cited evidence, the reasoning "
        "that derives it, what it supports, its cross-source links and open obligations.",
        GraphNodeInput,
    ),
    "trace_chain": (
        "Follow the logical chain through a claim: the premises it rests on, recursively "
        "(support), or the conclusions built on it (consequences).",
        TraceChainInput,
    ),
}

RECORDING_TOOLS: dict[str, tuple[str, type[BaseModel]]] = {
    "link_claims": (
        "Add a supports, rebuts or qualifies link between two claims in the graph, including claims "
        "from added material or earlier runs. The graph is additive: never edit an earlier claim; "
        "record your own claim and link it. Each link is audited against the claims' cited text.",
        LinkClaimsInput,
    ),
    "record_claim": (
        "Record one claim in the evidence graph, with the excerpts that support it. Returns its "
        "statement_id. Every claim you rely on must be recorded; claims you do not record do not "
        "count.",
        RecordClaimInput,
    ),
    "revise_claim": (
        "Withdraw a claim you recorded earlier, optionally replacing it with a corrected one you "
        "have already recorded. Use it when later evidence shows a claim is wrong, rests on a "
        "retracted or unreliable source, or is worded too strongly. A withdrawn claim leaves your "
        "argument: reasoning that used it must then be re-recorded with record_reasoning "
        "(revises_step_id). You can only withdraw claims the research agent recorded, not "
        "uploaded or extracted ones.",
        ReviseClaimInput,
    ),
    "record_protocol": (
        "Record the laboratory or experimental procedure behind your answer as ordered steps, "
        "each citing the excerpts that state it. Call it whenever the evidence you relied on "
        "describes a procedure someone could carry out to reproduce or test the finding, whether "
        "or not the question asked for one. It becomes a source a lab-monitoring system can "
        "follow, so report only what the sources state and never invent a step. Skip it only if "
        "the question involves no procedure.",
        RecordProtocolInput,
    ),
    "record_reasoning": (
        "Record that a conclusion follows from premises, and why. Use scope_change whenever the "
        "conclusion covers a different population, model or outcome than the premises.",
        RecordReasoningInput,
    ),
}

GUARD_TOOLS: dict[str, tuple[str, type[BaseModel]]] = {
    "check_conclusion": (
        "Ask the verifier what still stands between your recorded conclusion and a justified "
        "answer. Returns open obligations (what must be established, not how), the evidence "
        "behind the claim, and what you may do next. Call this BEFORE finalizing.",
        CheckConclusionInput,
    ),
    "finalize_conclusion": (
        "Give your answer. Refused with 'established' while critical obligations are open: then "
        "resolve them, narrow the claim, or finalize as conditional or hypothesis.",
        FinalizeConclusionInput,
    ),
    "abstain": (
        "End the run without a conclusion because the evidence does not support one.",
        AbstainInput,
    ),
}
