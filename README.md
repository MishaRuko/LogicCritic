
![The app showing an argument graph and its review panel](docs/app.png)

Two checks that work on their own or one after the other:

- **Check the argument** (`/research`): turn papers and notes into an argument graph you can inspect. Follow claims back to their source, review the reasoning, and find gaps in the evidence.
- **Check the experiment** (`/experiment`): compare a recording of the experiment with its method. Claude reads the method and watches the video, and each step comes out verified, contradicted, skipped or unverifiable, with timestamps you can jump to.

From a workspace, **Continue to experiment** carries a source's text into the experiment check as the method, and the result is listed under **Checks → Experiment check**. This step is optional.

## Run it

With Docker running:

```sh
cp .env.example .env
make up
```

Open [localhost](http://localhost) and choose a check. In the argument check, **Try the synthetic study demo** works without an API key. The experiment check opens with saved sample analyses of LabSuperVision recordings.

For Claude extraction, argument checks, synthesis and new experiment analyses, add `CLAUDE_API_KEY` to `.env` before starting the app. You can still edit graphs, review statements and run verification without it.

Upload text or Markdown files (`.txt`, `.md`, `.markdown`, up to 10 MB each). Click a node to inspect its source and reasoning, then accept or reject it. Use **Checks** to run verification and **Export** to save a snapshot.

Stop the app with `make down`. Your data stays in Docker volumes.

## Development

You'll need Docker, Node 20.9+ and pnpm 10.17.1.

```sh
pnpm --dir frontend install --frozen-lockfile
make backend
make frontend
```

The frontend runs at [localhost:5173](http://127.0.0.1:5173).

```sh
make test       # Tests, typecheck and build
make test-e2e   # Live API checks; the app must be running at localhost
```

### Experiment check

The experiment check was brought in from [lab-experiment-check](https://github.com/charliepiper/lab-experiment-check) (commit `e9bb2f1`); this repository is now its source of truth.

- **Code:** `frontend/src/lib/experiment` (agent loop and rules), `frontend/src/components/experiment` (screens) and `frontend/src/app/experiment-api` (server routes holding the key).
- **Accuracy:** it is a careful step checker, not yet a reliable error detector. See [EVALUATION.md](frontend/src/lib/experiment/EVALUATION.md) for results and known errors.
- **Samples:** the clips are from [LabSuperVision](https://huggingface.co/datasets/cong-lab/lsv), CC BY-NC 4.0, for non-commercial use.

```sh
pnpm --dir frontend eval:agent -- --rescore   # re-score saved runs, no API calls
```

Built with Next.js, React Flow, Kumo and FastAPI. See the [integration notes](docs/integration.md) for current API limits.
