# Evaluation Fixtures

`flawed_neuroboost_study.md` is an intentionally synthetic study summary. It is not a real paper, clinical recommendation, or report of actual results.

It is designed to exercise the deterministic verifier after extraction and human/agent annotations:

- `ungrounded_statement`: an unsupported final efficacy claim.
- `causality_overclaim`: an observational association is expressed as causation.
- `scope_leap`: a mouse finding is generalized to people.
- `missing_premise`: a mechanistic inference omits the necessary mediator premise.
- `direct_conflict`: efficacy and null-result claims share a claim key with opposing polarity.
- `invalidated_source`: the source can be marked invalidated to test downstream evidence warnings.

The fixture is deliberately synthetic so it can be committed, redistributed, and asserted against without treating a retracted or fraudulent publication as evidence.

For external regression evaluation, run `python -m app.benchmarks.scifact --output-dir /data/benchmarks/scifact --limit 100`. It downloads the official SciFact archive only on demand and emits a development-set manifest of claims, evidence abstracts, rationale sentence IDs, and gold labels. The benchmark source is https://github.com/allenai/scifact.
