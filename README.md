
![The app showing an argument graph and its review panel](docs/app.png)

Turn papers and notes into an argument graph you can inspect. Follow claims back to their source, review the reasoning, and find gaps in the evidence.

## Run it

With Docker running:

```sh
cp .env.example .env
make up
```

Open [localhost](http://localhost). Choose **Try the synthetic study demo** to get started without an API key.

For Claude extraction, argument checks and synthesis, add `CLAUDE_API_KEY` to `.env` before starting the app. To search and import papers from Amass, add `AMASS_API_KEY`. You can still edit graphs, review statements and run verification without it.

Upload text or Markdown files (`.txt`, `.md`, `.markdown`) or text-based PDFs, up to 10 MB each. Scanned PDFs need OCR, which isn't supported yet. Click a node to inspect its source and reasoning, then accept or reject it. Use **Checks** to run verification and **Export** to save a snapshot.

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

Built with Next.js, React Flow, Kumo and FastAPI. See the [integration notes](docs/integration.md) for current API limits.
