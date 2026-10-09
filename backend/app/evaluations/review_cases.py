"""Turn published reviews into open-search evaluation cases, using OpenAlex (free, no key).

A review's most cited references stand in for "the papers an expert would expect an answer to
rest on", and its abstract carries its conclusion. The question itself is written by a person
(or a model) from the review: that step is deliberately not automated here.
"""

import re

import httpx

from app.services.http_retry import send
from app.services.literature import METHOD_REFERENCE, openalex_params

OPENALEX = "https://api.openalex.org/works"
FIELDS = "id,doi,display_name,publication_year,abstract_inverted_index,referenced_works"
REF_FIELDS = "id,doi,display_name,publication_year,cited_by_count,ids,type"
# References every review cites for its method, not its evidence: reporting guidelines, bias and
# heterogeneity statistics, software. An agent should never be expected to "find" these.
# Words every research abstract uses; they say nothing about the topic.
GENERIC = set(
    "about across after among analyses analysis association associated based between change "
    "changes compared control data design effect effects evidence factors findings health "
    "however impact included including increase literature major method methods model models "
    "number outcome outcomes overall people potential present provide randomised randomized "
    "reported research results review reviews significant studies study systematic therefore "
    "these those three trial trials using whether which while within years".split()
)
# A conclusion shorter than this is usually a title or a question, not a finding.
MIN_CONCLUSION_CHARS = 400


def abstract_text(inverted: dict | None) -> str:
    order = sorted((i, word) for word, positions in (inverted or {}).items() for i in positions)
    return " ".join(word for _, word in order)


def _stems(text: str | None) -> set[str]:
    words = re.findall(r"[a-z]{5,}", (text or "").lower())
    return {word[:6] for word in words if word not in GENERIC}


def _doi(value: str | None) -> str | None:
    return (value or "").removeprefix("https://doi.org/") or None


async def review_case(client: httpx.AsyncClient, doi: str, key_papers: int = 8) -> dict:
    review = (
        await send(
            client, "GET", f"{OPENALEX}/doi:{doi}", params=openalex_params({"select": FIELDS})
        )
    ).json()
    references = [ref.rsplit("/", 1)[-1] for ref in review.get("referenced_works", [])]
    cited = []
    for start in range(0, len(references), 50):  # OpenAlex filters take up to 50 ids at a time
        batch = "|".join(references[start : start + 50])
        page = await send(
            client,
            "GET",
            OPENALEX,
            params=openalex_params(
                {"filter": f"openalex:{batch}", "select": REF_FIELDS, "per_page": 50}
            ),
        )
        cited += page.json().get("results", [])
    cited = [
        work
        for work in cited
        if work.get("display_name")
        and work.get("type") in ("article", "review", "preprint", "book-chapter")
        and not METHOD_REFERENCE.search(work["display_name"])
    ]
    conclusion = abstract_text(review.get("abstract_inverted_index"))
    topic = _stems(f"{review.get('display_name')} {conclusion}")
    # On-topic references first (sharing a topic word with the review), then by citations.
    cited.sort(
        key=lambda work: (
            bool(_stems(work["display_name"]) & topic),
            work.get("cited_by_count") or 0,
        ),
        reverse=True,
    )
    return {
        # A case without a stated conclusion cannot be judged; pick another review.
        "usable": len(conclusion) >= MIN_CONCLUSION_CHARS,
        "question": None,  # written from the review before the case is used
        "domain": None,
        "reference": {
            "title": review.get("display_name"),
            "doi": _doi(review.get("doi")),
            "year": review.get("publication_year"),
            "conclusion": conclusion,
        },
        "expected_sources": [
            {
                "title": work.get("display_name"),
                "doi": _doi(work.get("doi")),
                "pmid": ((work.get("ids") or {}).get("pmid") or "").rsplit("/", 1)[-1] or None,
                "year": work.get("publication_year"),
            }
            for work in cited[:key_papers]
        ],
    }
