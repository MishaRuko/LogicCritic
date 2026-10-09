# Open-search evaluation cases

`open_cases_v1.jsonl`: 15 research questions across 14 fields, each answered by a published
review or synthesis. Used by `python -m app.evaluations.cli open-create` (see
`backend/app/evaluations/open_search.py`).

Each case has:

- `question`: written by hand from the review, neutrally, so it does not hint at the answer.
- `reference`: the review's title, DOI, year, and its abstract as the `conclusion` the answer is
  compared with.
- `expected_sources`: the review's most cited on-topic references (from OpenAlex), standing in for
  the papers an expert would expect an answer to rest on. Method references (reporting
  guidelines, statistics, software) and references OpenAlex mislinks across reviews are removed.

Both the reference conclusion and the key papers are proxies, not ground truth: a review can be
dated, and a good answer can rest on other papers. During a run the reference review itself is
withheld from the agent (removed from search results, refused when read or fetched), so
agreement measures research rather than copying. A reference should be independent: case 4
first used a review by the theory's own authors and was replaced (see its `note`). Regenerate the raw material with
`cli review-cases <DOIs> --output raw.jsonl`, then write the questions by hand.

Review DOIs, in order: 10.1136/bmj-2023-075847, 10.1001/jama.2017.19344, 10.2105/ajph.2020.305999,
10.1177/0956797617739704 (case 4, key papers chosen by hand), 10.1177/1745691616652873, 10.1257/jep.35.1.3, 10.1257/app.20140287,
10.4073/csr.2018.10, 10.1007/s11356-017-9240-x, 10.3390/nano11020496, 10.3390/ijerph120404354,
10.1093/jamia/ocaf008, 10.3389/felec.2021.712785, 10.1037/bul0000223, 10.1088/1748-9326/abdae9.

## GRADE calibration cases

`grade_cases_v1.jsonl`: 18 questions (5 high, 5 moderate, 4 low, 4 very low) from Cochrane and
Campbell systematic reviews, each about one outcome whose certainty of evidence the review rates
with GRADE. They test calibration: whether the agent's certainty matches the reviewers'. The
open-search report maps high, moderate, low and very low to Established, Supported, Tentative
and Speculative, and reports exact matches, matches within one level, and the mean difference.

Each case adds `expected_certainty` and `grade_quote`, the abstract's sentence that states the
rating, checked word for word against the abstract on OpenAlex. `reference.conclusion` is the
authors' conclusions section. Candidates came from OpenAlex (Cochrane and Campbell reviews from
2019 on whose abstracts state a certainty level, most cited first); cases were kept only where
the central outcome's rating is unambiguous. Low and very low have 4 cases each because few
abstracts state one central outcome's rating cleanly; Campbell reviews rarely do (2 cases).
Smoking appears twice among the high cases.

Caveats: GRADE rates the evidence for one outcome, and an agent may reasonably answer a slightly
broader question; and a review can be superseded (several COVID-19 cases are from 2020-2022).
These are proxies like the reference conclusions, not ground truth.
