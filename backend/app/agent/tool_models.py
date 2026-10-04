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

RECORDING_TOOLS: dict[str, tuple[str, type[BaseModel]]] = {
    "record_claim": (
        "Record one claim in the evidence graph, with the excerpts that support it. Returns its "
        "statement_id. Every claim you rely on must be recorded; claims you do not record do not "
        "count.",
        RecordClaimInput,
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
