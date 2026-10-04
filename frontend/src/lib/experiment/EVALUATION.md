# Trial: evaluation and known errors

**Stage:** 4 October 2026, design frozen at lab-experiment-check commit `0910bc6` (branch `skip-order-rules`; results recorded up to `55599b7`). Copied into LogicCritic from `e9bb2f1` (PR charliepiper/lab-experiment-check#1); LogicCritic is now the source of truth.
**Data:** 17 LabSuperVision (LSV) clips, CC BY-NC 4.0, pinned to revision `91eaf452`.
**Model:** `claude-opus-5-5`, adaptive thinking, high effort.

## Summary

Trial is **safe but not yet a reliable error detector.** No correct run has been falsely flagged across 17 clips. On clips it was not tuned on, though, it caught **0 of 8 labelled skips** and **0 of 8 other labelled errors**. Use it in demos as a careful step checker that shows its evidence, not as proof that an experiment was done right.

## What this branch adds

- **Skipped** status (new): the agent watched where a step belongs and saw it not done, and the step is not a wait over 60 s (long waits are routinely cut from recordings). Among look-alike steps, a skip is reported as "one of" the group.
- **Out of order**: a located step outside the longest run of steps in protocol order, by more than 2 s. Reported as Contradicted.
- **Verification by checks.** A step is Verified when its *core* checks are confirmed with timestamped evidence. *Detail* checks (manner, follow-through, order) that the video can't show are noted on the step ("details unconfirmed") without changing the verdict. Accusations (Contradicted, out of order) still need confidence ≥ 0.8.
- **Only what video can clearly show becomes a check.** Volumes and amounts, label- or contents-dependent identity, exact counts, exact durations and temperatures, and claims about every item go to caveats. This is done in the prompt plus a deterministic filter (`unverifiableReason` in `src/lib/agent.ts`).
- **Agent prompt:**
  - "Near is not done": a tube held over the ice isn't on ice.
  - Steps that blend or interleave are each placed by their own defining action.
  - Motions need closely spaced frames; the frame budget scales with the protocol length.
- **Evaluation harness (`npm run eval:agent`):**
  - Scores each step against curated labels (`data/fixtures/labels.json`) with dev / held-out / fresh splits.
  - Records cost per clip.
  - `--rescore` re-judges saved runs for free.
  - Eval-only clips are listed in `heldout.json` / `extra.json`; their videos are fetched with hash checks into gitignored folders.

Tests: 70 (vitest), plus deliberate mutations of each rule, all caught.

## Results

"Exactly right" means every labelled skip was caught and nothing else was flagged; tip-change errors are not required. "False alarms" means a skipped, contradicted or out-of-order verdict on a step without a labelled error.

| Set | How it was used | Clips | Exactly right | Skips caught | Other errors caught | False alarms |
|---|---|---|---|---|---|---|
| Dev | tuned on, several rounds | 6 | 5/6 | 1/2 | 0/2 | 0 |
| Held-out 1 | run once on the first lock (`06e5d90`) | 7 | 4/7 | 0/5 | 0/2 | 1* |
| **Fresh** | **run once on the current lock (`0910bc6`)** | **4** | **1/4** | **0/3** | **0/6** | **0** |

\* The skip of DJI-099 step 3 surfaced as a contradiction on step 4 ("no aspiration of old medium is seen before this addition"), so it is a real detection on the wrong step.

**Correct runs are never falsely flagged.** That holds for all of them: DJI_10, DJI-092, 093 and 096. Verified steps across the dev clips went from 2 (before this branch) to 30. DJI_23 verifies 7/7 steps, with unconfirmed details noted.

Cost is about $0.25-0.70 per clip (estimated at $4 / $20 per million input / output tokens). All the evaluation runs on this branch cost roughly $15.

### Per clip (current design unless noted)

| Clip | LSV | Protocol | Labelled error | Outcome |
|---|---|---|---|---|
| DJI_17 | 091 | Mock transformation | skipped s3 (ice) | **caught**, s3 Skipped |
| DJI_11 | 035 | CRISPR delivery | skipped reagent 2 | missed (look-alike additions) |
| DJI_10 / 16 / 08 / 23 | 034 / 040 / 027 / 097 | various | none, or wrong vessel / tips | correct; errors missed |
| DJI_12 | 036 | CRISPR delivery | skipped dropwise addition | missed (filed "not visible") |
| DJI_14 | 038 | CRISPR delivery | skipped reagent 3, tips | missed (look-alike; localisation failed) |
| DJI_15 | 039 | CRISPR delivery | reagents straight to plate, no mixing | missed (described correctly, filed cautiously) |
| DJI_09 | 033 | CRISPR delivery | no tip changes | correct (tips not required) |

## Known errors and limitations

1. **The agent describes errors correctly but files them cautiously.** On DJI_15 it wrote "I never see reagents being combined into one EP tube… three times [it] lowers the pipette to a culture dish", which is the labelled error. It filed those steps as `not_visible`, not `not_performed`, at 0.5 confidence, so no rule fired. DJI_12 is the same. *Possible fix:* a separate "possible deviation" flag shown for review, without loosening the verdict rules. Not built.
2. **Look-alike steps can't be told apart.** "Add reagent 1/2/3" from unlabelled tubes looks identical, so a skipped middle addition is absorbed by the others (DJI_11, DJI_14). Even a perfect agent could only report "one of the additions is missing". Telling the agent to count and assign look-alikes made things worse and was removed.
3. **Steps done together in one motion are fragile.** Example: tilting during the aspiration (DJI_08). Counting or grouping produced false skips, so groups now only affect the wording of a skip.
4. **Short excerpts of a different procedure break step localisation** (DJI_14, DJI_15: IoU ≈ 0).
5. **Tip-change errors are never caught** (0/4). The tip change happens in about a second.
6. **Confidence is the bottleneck.** The agent rates most steps 0.3-0.8, so accusations at the 0.8 bar are rare. Lowering the bar was rejected: the agent's only wrong "not performed" report (an edited-out 20 min wait, at 0.55) sat next to a true skip at 0.6.
7. **The protocol can disagree with the footage.** Example: "water bath" in the protocol, a thermal cycler on camera. Checks now accept equivalent means, but any such mismatch can still read as a deviation.
8. **Small evaluation set:** 17 clips, three protocols, with staged errors. The labels are curated from LSV's short error text (`labels.json` documents each one). The dev clips have been tuned on repeatedly, so only the held-out and fresh numbers are honest.

## Reproduce (in LogicCritic)

```bash
cd frontend
pnpm install
# key: CLAUDE_API_KEY in the repo-root .env
pnpm fetch-experiment-data                                   # demo clips (no-op if present)
python3 scripts/experiment/fetch-demo-data.py --heldout && python3 scripts/experiment/fetch-demo-data.py --extra   # eval-only clips, into frontend/data/ (gitignored)
pnpm eval:agent                     # dev (writes src/lib/experiment/fixtures/agent-runs.json, shown at /experiment)
pnpm eval:agent -- --split fresh    # fresh split
pnpm eval:agent -- --rescore        # re-judge saved runs, no API calls
pnpm test
```

Saved runs and scores are in `src/lib/experiment/fixtures/`: `agent-runs.json` (dev, opened by the app), `heldout-runs.json`, `fresh-runs.json` and `agent-eval.json`. Pre-fix runs stay in lab-experiment-check (`data/runs-archive/`).
