"""How much certainty the evidence behind a conclusion can carry.

Certainty used to mean only "the verifier found no gap", so an answer resting on one paper
could claim as much as one resting on many. Here it is earned. The certainty a conclusion may
claim is the lowest of three ceilings, each read off the argument graph and the research record:

- **soundness**: the verifier's obligations (unchanged rules; a retracted source allows nothing),
- **evidence**: the independent sources the conclusion actually rests on, through recorded claims
  and reasoning (its upstream cone), and whether a review or meta-analysis is among them,
- **breadth**: how widely the evidence was looked for (searches, or material a person supplied),
- **quality**: how good the evidence is, rated by the guard's judge the GRADE way (start from the
  designs, downgrade for risk of bias, inconsistency, indirectness, imprecision, publication
  bias): high allows Established, moderate Supported, low Tentative, very low Speculative. The
  other ceilings count how much evidence there is; a thorough run always finds plenty, so without
  this low-quality evidence came out one level too high.

A source the agent read but never connected to the conclusion adds nothing, so a higher
certainty means a richer, better connected graph. Each ceiling says what would raise it, which
is what the agent is told to go and find: thorough research is how certainty is earned.
"""

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentEvent, AgentRun, Excerpt, Source, SourceValidity, StatementExcerpt

# Lowest to highest. "none" means even a speculative answer may not rest on this evidence.
LEVELS = ["none", "speculative", "tentative", "supported", "established"]
LABELS = {
    "established": "Established",
    "supported": "Supported",
    "tentative": "Tentative",
    "speculative": "Speculative",
}
MEANING = {
    "established": "strong, independent and consistent evidence, found by a broad search",
    "supported": "evidence from more than one independent source that holds after weighing, but "
    "is thinner or less consistent than Established",
    "tentative": "sound, but resting on thin evidence or with gaps still open",
    "speculative": "reasoning that goes beyond the direct evidence",
}
# A GRADE rating of the evidence, as the certainty it can carry.
GRADE = {
    "high": "established",
    "moderate": "supported",
    "low": "tentative",
    "very_low": "speculative",
}
# Earlier runs used three levels; they are read as the nearest new one.
LEGACY = {"conditional": "tentative", "hypothesis": "speculative"}

SYSTEMATIC = re.compile(r"systematic review|meta-?analys|umbrella review|cochrane", re.IGNORECASE)
REVIEW = re.compile(r"\breview\b|\bsurvey\b|\boverview\b|state of the art", re.IGNORECASE)
GIVEN = {"upload", "evaluation"}  # material a person supplied rather than the agent found
SCHOLARLY_ORIGINS = {"amass", "semantic_scholar", "upload", "evaluation"}
# Official sources: governments, international bodies and regulators. Their guidelines,
# statistics and evaluations count as strong evidence, unlike other web pages.
OFFICIAL_HOST = re.compile(
    r"(^|\.)(gov|gov\.[a-z]{2}|gc\.ca|gouv\.fr|europa\.eu|who\.int|oecd\.org|un\.org|"
    r"worldbank\.org|imf\.org|ipcc\.ch|iea\.org|nice\.org\.uk|nhs\.uk|ema\.europa\.eu)$",
    re.IGNORECASE,
)
LOOKING = ("search_papers", "follow_citations")  # tool calls that look for evidence


@dataclass
class EvidenceSource:
    source_id: str
    title: str | None
    kind: str  # systematic_review, review, official, scholarly, web
    given: bool


@dataclass
class Ceiling:
    level: str
    by: dict[str, str]  # dimension -> its ceiling
    to_raise: list[str] = field(default_factory=list)
    sources: list[EvidenceSource] = field(default_factory=list)
    searches: int = 0

    def allowed(self) -> list[str]:
        """The certainties a conclusion may be finalized at, highest first."""
        top = LEVELS.index(self.level)
        return [level for level in reversed(LEVELS[1 : top + 1])]

    def summary(self) -> str:
        """Why the certainty is what it is, in one line for the reader."""
        independent = len(self.sources)
        reviews = sum(s.kind in ("systematic_review", "review") for s in self.sources)
        parts = [f"{independent} independent source{'s' if independent != 1 else ''}"]
        parts.append(
            f"including {reviews} review{'s' if reviews != 1 else ''}"
            if reviews
            else "no review among them"
        )
        official = sum(s.kind == "official" for s in self.sources)
        if official:
            parts.append(f"{official} official source{'s' if official != 1 else ''}")
        if self.searches:
            parts.append(f"{self.searches} searches")
        return ", ".join(parts)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "label": LABELS.get(self.level),
            "meaning": MEANING.get(self.level),
            "by": self.by,
            "to_raise": self.to_raise,
            "evidence": self.summary(),
        }


def _independence_key(source: Source) -> str:
    ids = source.external_ids or {}
    for name in ("doi", "pmid", "arxiv_id"):
        if ids.get(name):
            return f"{name}:{str(ids[name]).lower()}"
    title = re.sub(r"[^a-z0-9]", "", (source.title or "").lower())
    return f"title:{title}" if len(title) >= 20 else f"id:{source.id}"


def _kind(source: Source) -> str:
    title = source.title or ""
    if SYSTEMATIC.search(title):
        return "systematic_review"
    if REVIEW.search(title):
        return "review"
    ids = source.external_ids or {}
    url = str(ids.get("url") or (source.metadata_ or {}).get("url") or "")
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if host and OFFICIAL_HOST.search(host):
        return "official"
    if (
        source.origin in SCHOLARLY_ORIGINS
        or any(ids.get(name) for name in ("doi", "pmid", "arxiv_id"))
        or source.mime_type == "application/pdf"
    ):
        return "scholarly"
    return "web"


async def cone_sources(session: AsyncSession, cone: set[uuid.UUID]) -> list[EvidenceSource]:
    """The independent, valid sources whose cited passages the conclusion's claims rest on."""
    rows = list(
        await session.scalars(
            select(Source)
            .join(Excerpt, Excerpt.source_id == Source.id)
            .join(StatementExcerpt, StatementExcerpt.excerpt_id == Excerpt.id)
            .where(StatementExcerpt.statement_id.in_(cone))
            .distinct()
        )
    )
    independent: dict[str, EvidenceSource] = {}
    for source in rows:
        if (source.metadata_ or {}).get("parser") == "agent_protocol_v1":
            continue  # the agent's own protocol note is not evidence
        latest = await session.scalar(
            select(SourceValidity.status)
            .where(SourceValidity.source_id == source.id)
            .order_by(SourceValidity.created_at.desc())
            .limit(1)
        )
        if latest == "invalidated":
            continue  # a retracted source counts for nothing
        key = _independence_key(source)
        given = source.origin in GIVEN
        candidate = EvidenceSource(str(source.id), source.title, _kind(source), given)
        # The same paper twice (say a preprint and its journal version) is one source; keep the
        # stronger description of it.
        if key not in independent or _rank(candidate) > _rank(independent[key]):
            independent[key] = candidate
    return list(independent.values())


def _rank(source: EvidenceSource) -> int:
    return ["web", "scholarly", "official", "review", "systematic_review"].index(source.kind)


@dataclass
class Effort:
    searches: int  # paper searches, citation follows and web searches
    against: bool  # whether any was declared a search for evidence against the conclusion


async def search_effort(session: AsyncSession, workspace_id: uuid.UUID) -> Effort:
    """How every agent run in the workspace looked for evidence: a follow-up builds on them."""
    runs = select(AgentRun.id).where(AgentRun.workspace_id == workspace_id)
    calls = list(
        await session.scalars(
            select(AgentEvent.payload).where(
                AgentEvent.run_id.in_(runs),
                AgentEvent.type == "tool_call",
                AgentEvent.payload["name"].astext.in_(LOOKING),
            )
        )
    )
    web = await session.scalar(
        select(func.count())
        .select_from(AgentEvent)
        .where(AgentEvent.run_id.in_(runs), AgentEvent.type == "web_search")
    )
    against = any((call.get("input") or {}).get("purpose") == "against" for call in calls)
    return Effort(len(calls) + (web or 0), against)


def ceiling(
    soundness: str,
    sources: list[EvidenceSource],
    searches: int,
    against: bool = False,
    quality: tuple[str, str] | None = None,
) -> Ceiling:
    """The highest certainty allowed, and what would raise each dimension that holds it down."""
    scholarly = [s for s in sources if s.kind != "web"]
    systematic = any(s.kind == "systematic_review" for s in sources)
    to_raise: list[str] = []

    if len(scholarly) >= 3 or (systematic and len(scholarly) >= 2):
        evidence = "established"
    elif len(sources) >= 2:
        evidence = "supported"
        to_raise.append(
            "Evidence, for Established: a systematic review or meta-analysis plus another "
            "independent study, or a third independent scholarly source, each recorded as "
            "claims and connected to the conclusion by reasoning."
        )
    elif sources:
        evidence = "tentative"
        to_raise.append(
            "Evidence, for Supported: a second independent source, recorded as claims and "
            "connected to the conclusion by reasoning. A source you read but did not connect "
            "does not count."
        )
    else:
        evidence = "speculative"
        to_raise.append(
            "Evidence: no cited source supports the conclusion yet. Record the claims it rests "
            "on, citing what you read, and reason from them to the conclusion."
        )

    given = sum(s.given for s in sources)  # material a person supplied counts as looked-for
    effort = searches + given
    if effort >= 4 and against:
        breadth = "established"
    elif effort >= 2:
        breadth = "supported"
        needed = []
        if effort < 4:
            needed.append(f"{4 - effort} more search(es) or citation follows")
        if not against:
            needed.append(
                "a search_papers call with purpose 'against', looking for evidence that "
                "would contradict the conclusion"
            )
        to_raise.append("Breadth, for Established: " + " and ".join(needed) + ".")
    else:
        breadth = "tentative"
        to_raise.append(
            f"Breadth, for Supported: {2 - effort} more search(es); one search is not a "
            "survey of the evidence."
        )

    # The judge's GRADE rating of the evidence: how good it is, where the rest counts how much.
    rating, why = quality or ("high", "")
    graded = GRADE.get(rating, "established")
    if graded != "established":
        better = LEVELS[LEVELS.index(graded) + 1]
        to_raise.append(
            f"Quality, for {LABELS[better]}: the reviewer rates this body of evidence "
            f"{rating.replace('_', ' ')} certainty ({why or 'no reason given'}). Stronger "
            "designs, lower risk of bias, and consistent, precise results that address the "
            "question directly raise it."
        )

    if soundness != "established":
        to_raise.insert(
            0,
            "Soundness: resolve the critical obligations listed, or narrow the conclusion.",
        )
    by = {
        "soundness": soundness,
        "evidence": evidence,
        "breadth": breadth,
        "quality": graded,
    }
    level = min(by.values(), key=LEVELS.index)
    return Ceiling(level, by, to_raise, sources, searches)


def readable(certainty: str | None) -> str | None:
    """A stored certainty (new or legacy) as the reader sees it."""
    if certainty is None:
        return None
    return LABELS.get(LEGACY.get(certainty, certainty), certainty)
