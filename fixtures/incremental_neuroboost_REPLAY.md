# Incremental Replay Contract

1. Create one workspace session.
2. Ingest `incremental_neuroboost_initial.md` and create an excerpt-grounded statement annotated as `claim_key={"key":"neuroboost:maze_time","polarity":"supports"}`.
3. Run verification: no `direct_conflict` issue should exist.
4. Ingest `incremental_neuroboost_update.md` into the same workspace and create a second excerpt-grounded statement annotated as `claim_key={"key":"neuroboost:maze_time","polarity":"refutes"}`.
5. Run verification: each statement should have an open `direct_conflict` issue and obligation.
6. The original source, statement, and graph patch remain present; the later update is appended rather than overwriting them.
