# lab-vision

Watches a lab recording against an experimental protocol and flags deviations: a wrong
volume, a skipped step, steps done out of order, or something unexpected. It is the lab-phase
counterpart of the argument graph and follows the same rule: **models propose, deterministic
code judges.**

```
video -> windows -> [detection] -> perception (Claude vision) -> verification (rules) -> sinks
                                         |                              |
                                   Observations                    Deviations
```

## Status (read this first)

**Built and working end to end.** Video in, deviations out. 67 unit tests pass, and the whole
pipeline has been run against real footage with real Claude calls. Nothing in this package
imports from the backend. Graph integration is now implemented through the research workspace (see
[Connecting to the graph](#connecting-to-the-graph)).

**Not yet good enough to trust.** Measured on the three LSV mock-transformation clips (see
[Findings](#findings)):

- With the current code it does **not** catch the one labelled mistake (a skipped step) in any
  run. An earlier version did (see [how the verdicts changed](#how-the-verdicts-changed-as-the-code-changed)).
- It raises **confident false alarms on one of the two correct clips**, for a reason that is
  partly the protocol's fault (it says "water bath"; the lab used a thermal cycler).
- **It cannot read the pipette volumes** (5 uL, 50 uL) in any configuration. Only one value
  (42 degC on a thermal cycler screen) was ever read.
- **Object detection, tracking and close-ups made no measurable difference** for about 8% more
  cost, so do not build further on them (SAM 2 included) before the first two points are fixed.

The model that does all the labelling is **Claude Opus 5.5** (`claude-opus-5-5`, medium
effort). OWLv2 only draws boxes, and the verdicts are plain code. See
[What does what](#what-does-what).

**Recommended next steps** are in [Open problems](#open-problems-and-recommended-next-steps).

## Quick start

```bash
cd vision
python -m venv .venv && source .venv/bin/activate     # use a venv: the extras pull in torch
pip install -e ".[dev]"                               # Python 3.13+
# detection (optional; downloads about 600 MB of OWLv2 weights on first use):
pip install -e ".[detection]"

# Credentials: CLAUDE_API_KEY in the environment, ./.env or ../.env (shared with the backend)

# 1. turn a written protocol into structured YAML, then READ IT and fix it
lab-vision structure --text data/protocol.txt --out protocol.yaml --title "Mock transformation"

# 2. check a video against it (calls Claude; see Cost)
lab-vision run --protocol protocol.yaml --video clip.mp4 --out runs/clip-1 --dump-frames

# 3. watch what the model saw (interactive window, rendered video, or one still)
lab-vision view --run runs/clip-1 --video clip.mp4
lab-vision view --run runs/clip-1 --video clip.mp4 --out runs/clip-1/view.mp4
lab-vision view --run runs/clip-1 --video clip.mp4 --at 41 --png still.png

# 4. score a run against labelled mistakes
lab-vision eval --deviations runs/clip-1/deviations.jsonl --truth truth.json

# re-run verification from recorded observations: FREE, no model calls
lab-vision run --protocol protocol.yaml --video clip.mp4 --out runs/clip-1b \
  --replay runs/clip-1/observations.jsonl

pytest && ruff check .
```

If `lab-vision` is not on your PATH, use `python -m lab_vision.cli` instead.

### Configuration

Environment variables or `.env` (see [config.py](src/lab_vision/config.py)):

| Variable | Default | Notes |
| --- | --- | --- |
| `CLAUDE_API_KEY` | none | shared with the backend. If unset, the Anthropic SDK looks for its own credentials |
| `VISION_MODEL` | `claude-opus-5-5` | **separate from the backend's `CLAUDE_MODEL`** (`claude-sonnet-5`) |
| `VISION_EFFORT` | `medium` | set empty to omit, for models without effort levels |
| `VISION_FALLBACKS` | `true` | server-side fallback if Claude's safety classifiers decline a window |
| `SAMPLE_FPS` | `1.0` | frames sampled from the video |
| `WINDOW_SECONDS` | `5.0` | length of one model call's window |
| `MAX_FRAMES_PER_WINDOW` | `5` | frames sent to the model per window (detection sees all sampled frames) |
| `MAX_IMAGE_SIDE` | `1024` | longest side of frames sent to the model |
| `LOOKAHEAD_STEPS` | `10` | how many still-unconfirmed protocol steps the model is asked about |
| `MIN_CONFIDENCE` | `0.6` | observations below this are ignored by the verifier |
| `DETECTION_MODEL`, `DETECTION_THRESHOLD`, `CROP_MIN_SCORE` | OWLv2 base, `0.15`, `0.3` | detection settings |

### Cost

Measured from token usage (each run writes `usage.json`), at list price $4 / $20 per million
input / output tokens. With detection off: about **$1.0** for the 139 s clip DJI-091 and
**$0.6** for each of the two 87 s clips, so roughly $2.2 for one pass over the three clips. Detection adds
about 8%. Spend is dominated by images: each window sends up to 5 frames (plus up to 2 close-ups
with detection). A/B experiments multiply this, so check the arithmetic first.

## Inputs

**Protocol** (YAML or JSON): an ordered list of steps. List order is the required order. See
[tests/fixtures/protocol.yaml](tests/fixtures/protocol.yaml) and the real one in
[fixtures/lsv_mock_transformation/protocol.yaml](fixtures/lsv_mock_transformation/protocol.yaml).

- `objects`: apparatus expected in the step, described by how it looks (see Detection).
- `checks`: a value to read off the video. Each has a neutral `question`, an `expected` value,
  an optional `tolerance` or accepted spellings, and a `target` (what the value is read from).
  Put the unit in the `question` and in `unit`; values are not converted between units.
- `source_text`: the exact sentence the step came from, for traceability.
- `obligation_ids`: graph proof obligations the step exists to resolve (for the future
  integration).
- Timing ("for exactly 5 seconds") stays in the description but is **not checked**.

`lab-vision structure` writes one from plain text. The model fills the schema in
[structuring.py](src/lab_vision/structuring.py) and its answer is checked against the source
before being accepted: each `source_text` must appear verbatim and in order, each numeric
expected value must appear in that step, and no question may contain its own answer. Failures
are fed back once, then it errors. **The output is a draft.** It becomes the ground truth the
video is judged against, so review it.

**Ground truth** for `eval`: `{"clip_id": "...", "deviations": [{"kind": "skipped_step",
"step_id": "s3", "span": {"start_s": 38, "end_s": 46}}]}`. An empty list marks a correct run,
where any confident finding counts as a false alarm.

## What does what

| Stage | Component | Model? |
| --- | --- | --- |
| Reading frames: step status, confidence, values, unexpected events | Claude Opus 5.5 via the Anthropic API | yes |
| Structuring the protocol text | the same Claude model | yes |
| Finding and boxing objects (`--detect`) | OWLv2 (`google/owlv2-base-patch16-ensemble`), local, boxes only | yes, but it never classifies actions or steps |
| Tracking object identity (`--detect`) | IoU tracker | no |
| Deciding what is a deviation | `ProtocolVerifier` in [verification.py](src/lab_vision/verification.py) | **no, deterministic** |

Each observation records `produced_by.model`. That is the model **requested**; with fallbacks
on, a refused window could be re-run by another model. `usage.json` in each run records the
model that actually served the requests.

## How the verifier decides

`Observation`s are claims about the video (they are the future graph's `reported` statements).
The verifier turns them into `Deviation`s. Observations below `MIN_CONFIDENCE` are ignored.

| Kind | When | Confident or review |
| --- | --- | --- |
| `wrong_value` | a confident reading does not match the check | confident (review if unreadable) |
| `skipped_step` | a later step is confirmed performed, and the earlier step has **no** evidence at all | confident |
| `incomplete_step` | a later step is confirmed performed, but the earlier step was seen **under way** and never confirmed complete (also raised at the end of the video) | review |
| `skipped_step` (end of video) | a required step was never observed | review |
| `out_of_order` | a step flagged skipped is performed later | confident |
| `unexpected_event` | the model reports something outside the protocol | review |
| `unverified_check` | a step completed but a check's value was never read | review |

Design rules:

- **Expected values are never shown to the model.** It reads a value, and code compares.
- **Uncertainty is a result, not an error.** Weak evidence produces `needs_review`, never an
  assertion.
- **Append-only.** A later correct reading does not retract an earlier deviation, matching the
  graph's event model.
- **A bad response never yields partial output.** Failed windows are dropped whole and counted
  in `failed_windows`.
- **Model output cannot invent identifiers.** Unknown step and check ids are discarded.
- **Refusals fall back server-side** (life-science content can trip safety classifiers).

## Detection, tracking and close-ups (`--detect`)

`DetectionProcessor` runs before each model call. It looks for the objects the protocol names
(`ProtocolStep.objects` and `Check.target`) with OWLv2, an open-vocabulary detector, so no
training is needed. Boxes are matched across frames by an IoU tracker. For each value the
protocol asks to read, it cuts the best detection from the full-resolution frame, enlarges it,
and sends it beside the normal frames (at most two per window, none below `CROP_MIN_SCORE`).
The model is told crops and detector notes may be wrong and that the full frames win. If the
detector fails, the window is sent without it.

Queries must be **visual descriptions, not names**: "ice bucket" scored about 0.2 and boxed the
wrong containers, while "green box of crushed ice" scored about 0.6 and was right. OWLv2 reads
at most 16 tokens per query. `lab-vision structure` asks for descriptions of five words or
fewer.

Measured on the LSV clips (head-mounted camera, cluttered bench):

- **Large static objects** (ice bucket, thermal cycler) are found reliably.
- **Small hand-held objects** (tubes, pipettes, petri dishes) are not. Boxes often cover a
  whole hand, a neighbouring object, or an idle pipette instead of the one in use, so a close-up
  can be sharp and still show the wrong thing.
- **Track ids are unreliable except for static objects.** The camera moves with the operator, so
  image-space overlap breaks down. 4 fps instead of 1 helped the ice bucket (6 ids to 5 over four
  times the frames) but not small objects, and took about 6 minutes per 90 s clip.
- **The model notices bad crops** and says so in its notes ("the detector crop shows a blue tube
  holder, not a temperature display"). The "trust the full frames" instruction works.
- **No measurable benefit in the A/B** (below).

`--dump-frames` writes the frames as sent, overlays with boxes and track ids, the crops, and a
`window.json` per window under `OUT/windows/`.

## Viewing a run (`view`)

Shows the footage next to what the model was given and what it said, so you can judge readings
against what is on screen. A green border and "SENT TO MODEL" mark frames that were sent. The
right panel shows, for the current window: the frames and close-ups sent (current one
outlined), detector notes, the model's answer per step with confidence and values read, and any
deviations. A timeline marks windows and deviations (red: deviation, amber: needs review).
Boxes appear when the run used `--detect --dump-frames`; without them you still get the frames
sent, answers and deviations, from the run's `observations.jsonl` and `deviations.jsonl`.

Keys: space pause, `a`/`d` back/forward one second, `w`/`s` previous/next window, `,`/`.` one
frame, `p` save a still, `q` quit. The interactive window needs an OpenCV build with GUI
support (`opencv-python`, not the headless wheel this package depends on); otherwise use `--out`
or `--at`. The interactive window itself is **untested** (only the composer, the render and the
key handling are). Frames shown as "sent" are redrawn from the video at full resolution, not the
downscaled JPEG the model got (those are in `windows/`). OpenCV cannot draw `μ` or `°`, so they
appear as `u` and `deg`.

## Findings

Dataset: three clips of one mock bacterial transformation, from LabSuperVision (LSV), in
`data/` (gitignored; see [data/README.md](data/README.md) to re-fetch). DJI-091 has one labelled
mistake (step 3, ice incubation, skipped); DJI-092 and DJI-093 are labelled correct. The video
files are 960x720 low-resolution proxies. Labels are in
[fixtures/lsv_mock_transformation/](fixtures/lsv_mock_transformation/).

### A/B: detection off vs on (current code)

Same protocol and settings in both arms, 3 clips x 2 repeats each (12 runs). "Conf. FA" =
confident deviation matching no label. Reproduce with `scripts/ab_stream.sh off|on` then
`python scripts/ab_score.py`.

| Clip (label) | Detection off, 2 runs | Detection on, 2 runs |
| --- | --- | --- |
| DJI-091 (skip step 3) | **missed** both; 0 conf. FA; 9-10 review flags | **missed** both; 0 conf. FA; 9-10 review flags |
| DJI-092 (correct) | 0 conf. FA; 7 review flags | 0 conf. FA; 6-7 review flags |
| DJI-093 (correct) | **2 conf. FA** (steps 4, 5 "skipped"); 4 review flags | **3 conf. FA**; 4 review flags |

Usable value readings (value given, confidence 0.6 or more): **4 per arm across all six runs**,
all of them the 42 degC thermal-cycler screen in DJI-091. The pipette volumes (5 uL, 50 uL) were
never read in either arm. Total cost was **$9.21**: $4.42 for the six detection-off runs and
$4.79 for the six detection-on runs (about 8% more). The only consistent difference between
the arms is one extra confident false alarm on DJI-093 with detection on.

The model is nearly deterministic here (identical input tokens and near-identical review counts
across repeats), so repeats add little. More **clips**, not more repeats, are what would add
information.

### How the verdicts changed as the code changed

Single runs on earlier versions, kept because they explain the current numbers:

| Version | DJI-091 | DJI-092 | DJI-093 |
| --- | --- | --- | --- |
| prompt v2, focus 3 steps, no `incomplete_step` rule | not run | 2 conf. FA (steps 1, 4 "skipped"); replaying the same observations with the rule gives 0 | not run |
| prompt v3, focus 3 steps, with the rule | skip **caught**, 0 FA | not rerun | 0 FA, but steps 4-7 never confirmed |
| **current**: prompt v4, focus 10 steps, with the rule | **missed** | 0 FA | 2 conf. FA |

Between the last two rows the focus went from 3 to 10 steps and the prompt from v3 to v4
(tighter rules for unexpected events, plus a note about close-ups). **I did not isolate which of
the two lost the DJI-091 detection.** It is not run-to-run noise: the repeats are nearly
identical. Probable mechanism for the new DJI-093 alarms: with a focus of 3, steps 4 and 5
stalled unconfirmed and step 6 was never put in front of the model; with 10 it is, step 6 gets
confirmed, and steps 4 and 5 are then called skipped (see below). Isolating the two changes needs
one paid run per variant on DJI-091.

### Why DJI-091 is missed

In the runs inspected, at 10-14 s the model reported step 3 ("incubate on ice") as in progress at
confidence 0.60: "the gloved hand is placing the tube into the crushed ice". At that moment the
operator is still in step 1, and keeping the cells on ice is normal handling. The model matched
the **action** to a step without regard to **where the operator was in the protocol**
(stage-blindness). The verifier then counted step 3 as started, so when step 4 looked done it
reported step 3 as "incomplete", not "skipped". Open the run in `view` at 14 s to see it. This is
not an object-recognition failure, so better detection or labelling would not fix it.

### Why DJI-093 gets confident false alarms

The model correctly reports a **thermal cycler and no water bath** for the heat shock, so steps
4 and 5 have no evidence while step 6 is confirmed, and the verifier calls them skipped. The
protocol names a water bath and the dataset labels the clip correct, so the protocol and the
footage genuinely disagree. The verdict is defensible; against the labels it is a false alarm.

### Things that did not work

- **Lowering `MIN_CONFIDENCE` to 0.5** (tested by free replay): DJI-091 was still missed and
  review flags rose from 7 to 13 on DJI-092 and from 7 to 10 on DJI-093.
- **Detection and close-ups**: no gain in readings or verdicts (above).

## Open problems and recommended next steps

In priority order. The first two address what the data shows is wrong.

1. **Make the model stage-aware.** Pass what has already been confirmed ("so far: s1 performed
   at 14 s") so an action is not credited to a later step. Risk: anchoring. Needs a paid re-run;
   compare against the table above.
2. **Allow equivalent apparatus.** The protocol says "water bath" and the lab used a thermal
   cycler. Options: let the protocol list acceptable alternatives per object, or make a mismatch
   an `unexpected_event` and still credit the step. Decide what the product should do.
3. **Reconsider the verifier's skip logic.** One misattributed reading decides skipped versus
   incomplete. Consider requiring evidence ordering (a step cannot start before its predecessor
   has at least been seen), but note step 2 (flicking) is rarely seen, so a strict rule brings
   false alarms back. `--replay` re-judges saved observations for free, which makes this cheap to
   explore. **Beware overfitting to three clips.**
4. **Get more labelled data, especially wrong-value mistakes.** There is one positive example
   (a skipped step) and none for wrong volumes, so wrong-value detection is untested.
5. **Test full-resolution video.** The LSV files here are 960x720 proxies; the full-resolution
   originals (`DJI/DJI-Video/<name>.MP4` on the Hugging Face dataset) may make the pipette dials
   legible. **Untested hypothesis**, and the cheapest way to learn whether reading volumes is
   possible at all.
6. **Check timing.** "For exactly 5 seconds" steps are not verified. It needs denser sampling
   around those steps and a duration rule in the verifier.
7. **If detection is revisited**: let Claude choose among numbered detector boxes from the closed
   object list (set-of-marks style) to fix wrong labels, and consider SAM 2 for tracking. A free
   local feasibility test (prompt SAM 2 with an OWLv2 box on a hand-held tube, track it for 5 s at
   10 fps) was proposed but not run. SAM 2 needs about 10 fps, so expect minutes per clip.
   transformers 4.57 has SAM 2 image and video models but not SAM 3, and no automatic mask
   generator. **Do this only after 1-5**; detection currently buys nothing.

## Reducing cost (ideas, not started)

The video agent (`perception/experiment_agent.py`) is expensive because it runs on Opus 5.5 with
adaptive thinking and must look at the video to find where things happen. It sends 8-40
evenly spaced overview frames, then up to `max(48, 4 x steps)` 1024 px frames over up to 20
turns, and every turn re-sends the conversation (cached, but still billed). Ideas, none built:

**Measure first.** `ClaudeLLM._record` (`llm.py`) counts only `input_tokens` and `output_tokens`,
not cache reads or writes, so the real split between images, re-reads and thinking output is
unknown. Thinking output costs 5x input and may dominate. Fix the accounting, then do one
paid run on a labelled clip before changing anything else.

**Cheaper within the current design:**
- Pick overview frames where something changes (OpenCV frame difference or scene change: free)
  instead of one every 3 s; lab videos have long static stretches.
- Tile overview frames into contact sheets: four 512 px frames in one 1024 px image cost the
  same as one frame.
- Smaller or cropped close-ups: 768 px instead of 1024 px is about 44% fewer image tokens;
  crop to the bench area (OWLv2 is already here).
- Use Sonnet or Haiku for the overview and Opus only for close inspection of uncertain steps;
  lower `VISION_EFFORT`.
- The Batch API halves the price for runs that do not need live progress.

**A different design (the bigger win): find the moments first, then judge only those.**
1. Index the video locally for free: a frame embedding (SigLIP or CLIP) for text search over
   frames, motion and scene-change detection, optionally OWLv2 objects.
2. For each protocol step, search the index with the step's text and take the 2-3 best windows.
3. Ask a model only about those windows ("does this show the step, done correctly?"). Cost then
   scales with the number of steps, not the length of the video. Fall back to a wider look when a
   step has no confident match.

   Variant: a cheap model (Haiku, or a local open model such as Qwen-VL) captions those windows
   into a timestamped text log; a text-only model checks the protocol against it, and Opus is
   called only for ambiguous steps.

   Risk: general embeddings may not separate fine actions (pipetting vs aspirating). A free first
   test: embed a labelled clip's frames and check whether each step's query retrieves the right
   moment. If it does not, use the captioning variant on its own.

**Other signals.** Many steps cannot be seen at all (concentrations, temperatures, durations;
see `UNVERIFIABLE`). Narration ("adding 2 ml trypsin now") through speech-to-text, timer
sounds, or centrifuge and incubator logs give cheap, timestamped evidence for those. Models that
take video natively (Gemini, for example) cost far less per second, but would add a second
provider.

## Extension points

| Interface | Module | Use it to |
| --- | --- | --- |
| `FrameSource` | `video.py` | add a live stream or camera source |
| `Perceiver` | `perception/base.py` | swap the perception approach |
| `StructuredLLM` | `llm.py` | change the model provider for perception and structuring |
| `FrameProcessor` | `perception/base.py` | prepare each window; `DetectionProcessor` is one |
| `ObjectDetector` | `detection/base.py` | swap the detector, for example Grounding DINO |
| `WindowObserver` | `pipeline.py` | inspect each window as the model will see it |
| `RunSink` | `sinks.py` | send results elsewhere, for example the graph |

## Connecting to the graph

The research workspace now connects this package to the full **Add research → Verify research → Run experiment** flow. Protocol extraction uses `structure_protocol` for unnumbered methods and a deterministic parser for explicit numbered instructions. Steps retain their source excerpt ids and are extracted automatically when an experiment starts. Explicit protocol review remains available through the API.

The backend worker runs the adaptive experiment agent for uploaded videos and `ProtocolVerifier` for saved observations or clearly labelled synthetic demo inputs. `VISION_STRATEGY=windows` selects the original `Pipeline`. Results and deviations are stored in the workspace, with time-range excerpts and `reported` statements; uncertain findings remain marked for human review. Research changes invalidate earlier verification and require a current extracted methodology. Partial recordings omit end-of-run skipped-step findings.

See [docs/experiments.md](../docs/experiments.md) for the demo walkthrough, API, deployment, and current integration limits. The model-accuracy findings above are unchanged by this integration.

## Known limits

- A step skipped earlier and performed later is only noticed if the model reports it; skipped
  steps leave the model's focus.
- Frame sampling is fixed-rate, so fast actions can fall between samples.
- Tracking is image-space only and unreliable on moving-camera footage.
- Units are not converted, and timing is not checked.
- The model can describe the same unexpected event several times in different words.
- The interactive viewer window has not been exercised by a human; only its logic is tested.

## Repository layout

```
src/lab_vision/
  models/        Protocol, Observation, Deviation, Provenance (shared contracts)
  perception/    Claude perceiver, prompts, response schema, offline replay
  detection/     detector interface, OWLv2, IoU tracker, DetectionProcessor
  llm.py         structured-output Claude wrapper (usage tracking, fallbacks)
  verification.py   the deterministic rules
  structuring.py    prose protocol -> Protocol, with checks against the source text
  pipeline.py, video.py, sinks.py, evaluation.py, viewer.py, debug.py, cli.py, config.py
fixtures/lsv_mock_transformation/   real protocol and truth labels
scripts/         ab_stream.sh, ab_score.py (the detection A/B)
tests/           67 tests; the Claude API and the detector are faked, no network needed
data/            LSV clips (gitignored)    runs/   run outputs (gitignored)
```

Each run directory holds `observations.jsonl`, `deviations.jsonl`, `summary.json`,
`usage.json`, and `windows/` when `--dump-frames` was used.

Environment notes: the extras install torch and transformers, so use a virtual environment (this
was developed in the global Python, which produced unrelated dependency warnings). The
`opencv-python-headless` dependency has no GUI; see Viewing a run.

## Research workspace video agent

The integrated app now defaults to the adaptive video agent ported from the `experiment` branch. It uses the original inspection prompts and tools, samples overview frames, requests higher-resolution windows, and persists per-step visual checks and evidence in the research workspace. See [docs/experiments.md](../docs/experiments.md). Set `VISION_STRATEGY=windows` to run the original pipeline described above.
