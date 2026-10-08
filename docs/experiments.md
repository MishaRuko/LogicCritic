# Research to experiment integration

The research workspace and `vision/` share the **Add research → Run experiment** (with optional research checks) flow. Experiment execution analyses a recording or saved observations with the existing perception and deterministic verification pipeline.

## Demo walkthrough

1. Click **DJI_08 · 30 seconds** in the left panel or **Try the full demo**.
2. Inspect the source-backed argument. Research checks are available but optional.
3. Choose **Run experiment**, then **Run sample analysis**. After an agent completes with a protocol, **Run experiment now** in its chat summary opens that procedure directly.
4. The matching 22-step protocol is extracted automatically. Saved model observations from the first 30 seconds are passed through `ProtocolVerifier`. Unreadable reagent concentrations are marked for review; steps outside this partial clip are not reported as skipped.
5. Inspect the video captions, coverage strip, execution timeline and per-step evidence. The Method tab retains source passages. Generate a record to get the reference report, PDF/JSON downloads, hashes and a QR link. Experiment evidence also appears as a source and reported statements in the graph. The methodology and original source passages are available in a collapsed details panel.

No extraction, approval, or experiment configuration is required in the main flow. Uploading a new recording uses live video analysis, which requires `CLAUDE_API_KEY`. Saved sample analysis uses no new model calls. The synthetic colour-study sample remains available in `frontend/public/demo/initial.md` and through the `loadDemo` helper; synthetic observations for arbitrary methodologies are an optional experiment tool.

Choose **Analyse sample live** to run the same 30-second recording through the methodology and video agents. Each stage has an output area above its label: methodology checks fill in as completed source-grounded steps arrive from the model stream, overview frames scroll horizontally, agent inspection requests appear live, and verifiability findings retain their evidence limits. A thin segmented progress bar underneath shows completed stages separately from scientific certainty. Its elapsed time and trace remain available under **How this analysis ran** after completion. Local agent runs took 92–119 seconds; saved-observation replay took approximately one second. Live timing varies with model latency and inspection requests.

Verification records that the current argument has been checked; it does not assert that all scientific claims are correct. Outstanding gaps remain visible when an experiment is prepared.

## Downloadable sample

Served by the app at:

- `/demo/lsv/Splitting-cells-DJI-027.pdf` — matching protocol PDF, also usable as a research upload.
- `/demo/lsv/Splitting-cells-DJI-027.md` — the same numbered procedure with Markdown headings.
- `/demo/lsv/DJI_08-first-30s.mp4` — exactly 30 seconds, 1280 × 960, H.264, approximately 13 MB.
- `/demo/lsv/DJI_08-first-30s.observations.jsonl` — saved observations used for the immediate sample run.

The PDF is a procedure document, rather than a published research paper. The original video and protocol come from [LabSuperVision / LabOS LSV](https://huggingface.co/datasets/cong-lab/lsv), revision `91eaf452d8ff2eeb1d5767d4198d1f6a965183c5`, licensed CC BY-NC 4.0. The video is trimmed from the copy in the sibling `materials-mvp` project. Saved model output from that project is adapted to the existing lab-vision observation schema, retaining only evidence inside 0–30 seconds. Observations are labelled as saved analysis, and unreadable labels are not treated as known values. See `frontend/public/demo/lsv/attribution.json` for provenance.

The short clip covers preparation. The dataset's labelled incubation mistake occurs outside this excerpt; this sample does not claim to detect it. Protocol text is preserved as supplied, including inconsistent quantities in later steps. It is demo input rather than a newly validated lab procedure.

## API

All paths use `/api`.

| Endpoint | Purpose |
| --- | --- |
| `GET /workspaces/{id}/experiments` | Verification state, extracted protocols and run history |
| `POST /workspaces/{id}/protocols` | Optional manual extraction from `{ "source_id": "..." }` |
| `POST /workspaces/{id}/protocols/{protocol_id}/approve` | Optional explicit source review |
| `POST /workspaces/{id}/experiment-runs` | Multipart `source_id` or `protocol_id`, `mode` (`demo`, `replay`, `video`), `file` for video/replay, and optional `partial_recording` |
| `GET /experiment-runs/{id}/recording` | Uploaded video playback with HTTP range support |

Starting a run extracts methodology when no current protocol is supplied. A verification click is not required. Protocol versions retain a research fingerprint and are refreshed automatically when research changes, while run artifacts are excluded so inspecting a result does not invalidate its protocol. Approval remains available for callers who want to record a separate review, but is optional.

Protocols are immutable versions stored by migration `0012_experiments`. Each step retains its original text and excerpt ids. Explicit numbered instructions use deterministic extraction, with numeric checks for named pipette volumes, temperatures and reagent concentrations. Unnumbered methods use `lab_vision.structure_protocol` and must ground every step in a source passage. Missing methods and invalidated sources produce actionable errors.

When the research agent hands over a protocol, its latest procedure is selected by default. The experiment setup previews its steps, basis and original cited research passages, and uses the prepared protocol when it is current. The Method tab also links back to those passages after analysis. Results default to runs for the selected source; older procedures remain available under Previous runs. Choose New experiment to change the protocol source or recording.

## Execution

Runs are durable database jobs. The main worker processes video independently of research-agent and extraction loops; a separate `replay-worker` handles fast samples and replays so a long live video cannot block a demo. Video progress shows methodology extraction, overview frames and the windows the agent requests for closer inspection. Jobs with no heartbeat for ten minutes are marked failed. Results are persisted as temporal excerpts and `reported` statements; uncertain findings remain marked for review.

MP4, MOV, WebM, AVI and M4V uploads support up to 100 MB. Replay files use the existing `observations.jsonl` contract and must match protocol step/check ids in timestamp order. Selecting a video shows a local playback preview before submission and the uploaded recording remains visible during analysis. Formats the browser cannot play still support upload. Selecting **This recording shows only part of the procedure** suppresses end-of-recording skipped-step findings while preserving deviations supported by the available observations. Known `first-30s` demo uploads select this automatically.

`make up` builds API and workers from the repository root and installs both Python packages. `VISION_MODEL`, `VISION_EFFORT` and `VISION_FALLBACKS` configure video analysis independently of research. `VISION_STRATEGY=agent` is the default; `windows` retains the previous lab-vision window pipeline for comparison. The included nginx configuration permits 101 MB requests.

The lab-vision window model's measured limitations in [vision/README.md](../vision/README.md) still apply. A completed analysis does not establish successful scientific reproduction. Failed video windows and review flags remain visible.

## Experiment presentation and reports

The execution, timeline, coverage, status, record and shared-record components are adapted from the adjacent `materials-mvp/src` app. They use the existing research workspace shell and lab-vision runs. Verification and experiment setup use dividers and coloured numbers without cards or uppercase labels. The experiment has Execution, Method and Record tabs, synchronized video and timestamp navigation, a methodology rail, check-level evidence, a frame snapshot, machine-readable JSON, printable PDF, content hashes and a QR link.

Migration `0013_experiment_records` persists immutable reports. `POST /api/records` validates the three hashes, methodology source passages, observation timestamps/confidence and result grounding against the completed run before storing it. `GET /api/records/{hash}` loads the public read-only report at `/r/{hash}`; its hashes are also recomputed in the viewer's browser. `GET /api/experiment-runs/{id}/records` restores generated reports after refresh. The viewer displays one representative observed window per step, while the JSON report retains all raw observations.

A partial recording can leave a full step unverifiable even when an action is visible; missing later steps are labelled outside the excerpt. A displayed verified step describes the available recorded evidence, rather than successful scientific reproduction. Report integrity is distinct from physical authenticity.

## Video agent from the experiment branch

The inspection prompts and strict `view_frames` / `submit_findings` tool definitions are imported from [MishaRuko/LogicCritic, experiment](https://github.com/MishaRuko/LogicCritic/tree/experiment), revision `423841f`. The TypeScript agent loop in `frontend/src/lib/experiment/agent.ts` is adapted in `vision/src/lab_vision/perception/experiment_agent.py` so video decoding, Anthropic credentials and long-running analysis stay in the durable worker. The prompts and tools are retained in `experiment_contract.py`.

The agent derives visible core checks, supporting detail checks and caveats from the already source-grounded protocol. Each original step id, description and source quote is retained. Measured quantities, labels, exact durations/counts and claims about every item are moved into caveats, following the branch's exclusions. Numbered instructions do not need to be extracted again.

The worker samples 8–40 overview frames, then the model requests denser frames from selected windows (up to eight per request, with a total frame budget and 20-turn limit). Findings must cover every source step exactly once, stay inside the recording and cite frames actually supplied. Incomplete or invalid findings are returned to the agent for correction.

The same Execution, Method and Record screens present the per-check findings, timestamped evidence and caveats. Core checks decide whether a located step is established; unseen supporting details do not block it. Below 0.7 confidence, or without timestamped evidence, a step stays unverifiable. Unlocated steps remain unverifiable, including steps outside an excerpt. The stored verdicts and full check-level observations must match a generated report exactly. Existing replay, sample and older window runs remain readable.

## Live sessions (phone as camera)

Start one from Experiments with **Start live session**. The dashboard shows a QR code; scanning it
on a phone opens `/live/<run id>`, a full-screen camera page. The phone films the bench and:

- sends one still frame a second (`POST /api/experiment-runs/{id}/frames`, multipart `frames` with
  matching `timestamps` in seconds since the stream began);
- uploads its own recording in 5-second pieces (`POST /api/experiment-runs/{id}/recording?index=n`,
  raw WebM or MP4 body). Pieces are appended in order and streamed to disk, so no request comes
  near Cloudflare's 100 MB limit; a resent piece is ignored and a gap is refused (409);
- ends with `POST /api/experiment-runs/{id}/stop` (the dashboard has the same button).

The worker's live loop (`mode = "live"`, separate from uploaded-video analysis) feeds the frames
to the window pipeline through `lab_vision.video.LiveFrameSource` as they arrive: 10-second
windows on `VISION_LIVE_MODEL` (default `claude-sonnet-5-5`). Each observation and deviation is
written to `result.live` at once, and the dashboard shows each step as it is recognised, about
10-20 seconds behind the camera. A session stops at 15 minutes (`LIVE_MAX_SECONDS`) or after 60
seconds without frames. The live result is final; the recording is attached for playback.

Phones only allow the camera on https pages, so use the deployed site, or an https tunnel when
testing locally. `GET /api/experiment-runs/{id}` returns one run (the camera page uses it).

