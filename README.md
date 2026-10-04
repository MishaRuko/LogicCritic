# Trial

![The app showing an argument graph and its review panel](docs/app.png)

Trial is a research agent that has to show its working. It turns papers into an argument graph
where every claim points to the passage it came from and every conclusion points to the premises
behind it. When the agent researches a question, a separate model checks the evidence it cites,
and the agent is only allowed to be as certain as that evidence supports. If the evidence is not
there, it says so.

We built it for Track 2 (Originator) of the hackathon: agents that do science and know when they
are wrong.

Watch the [walkthrough](https://www.youtube.com/watch?v=vCM08BDn8cE)

Try it: [trial.misharuko.com](https://trial.misharuko.com)

## How it works

```
Papers or a question
        |
Add material:  PDF or text -> passages -> Claude pulls out claims and reasoning,
               each claim citing the passages it came from
Agent:         searches papers and the web, reads them, and adds its own claims,
               reasoning and links to the same graph
        |
Argument graph (only ever added to)
        |
Checks:  missing evidence, missing premises, causal claims without the right study design,
         conflicts between sources, retracted papers
Guard:   before the agent answers, an independent model reads the cited passages and decides
         whether its criteria are met. The agent may claim "established" only with no open
         problems, otherwise "conditional", "hypothesis", or it abstains
        |
Answer with a verdict and its limits, or a question answered from the graph with the
relevant chain highlighted
        |
Experiments: verified research becomes a step by step protocol, and a video of the
             experiment is checked against it
```

There are three ways to use the chat box:

- **Add material**: paste text or attach PDFs, Markdown or text files (up to 10 MB each). Each new
  paper is added to the same graph, and claims that support, contradict or narrow claims from
  your other papers are linked to them.
- **Ask graph**: ask about the argument, for example "what does this conclusion rest on?". The
  answer comes from the graph alone and the claims it depends on are highlighted.
- **Agent**: ask a question, check a claim or test a hypothesis. You can watch the agent search,
  read, record claims and change its mind, and see how settled its answer is in words rather
  than a percentage.

## Does the guard help?

We ran the same model (Claude Sonnet 5.5) with and without the guard on the same questions, each
in a fresh workspace with only the source text. Every answer was scored on its own against the
source, claim by claim, so a longer answer gets no credit for being longer.

|                                                       | Without guard | With guard |
| ----------------------------------------------------- | ------------: | ---------: |
| Errors per answer, 30 questions on full-text papers   |          1.27 |       0.60 |
| False statements about the source, same questions     |          0.60 |       0.17 |
| Correct verdict, 60 SciFact claims with expert labels |           90% |        90% |
| Unsupported claims per answer, same SciFact claims    |          0.35 |       0.07 |

So the guard roughly halves the errors without getting the verdicts wrong more often. It costs
about three to four times as much per answer. Our first attempt at this evaluation said the
opposite, because it accidentally showed the agents the answer key and rewarded long answers.

## Running it

You need Docker. Add your Anthropic key (and an Amass key if you want the agent to search papers):

```sh
cp .env.example .env
make up
```

Then open [localhost](http://localhost). **Try the full demo** loads a sample protocol and lab
video so you can see the whole flow.

For development you also need Node 20.9+ and pnpm 10.17.1:

```sh
pnpm --dir frontend install --frozen-lockfile
make backend     # API, workers and database in Docker
make frontend    # http://localhost:5173
make test        # frontend tests, typecheck and build
```

Backend tests and other details are in [AGENTS.md](AGENTS.md).

## What's where

```
backend/    FastAPI app: extraction, verification, the agent and its guard, evaluations
frontend/   Next.js app with the graph view and chat
vision/     protocol structuring and lab video analysis
docs/       how it works, experiments, API notes
```

More reading:

- [AGENTS.md](AGENTS.md): notes for developing and deploying
- [API_INTEGRATION.md](API_INTEGRATION.md) and [docs/experiments.md](docs/experiments.md): the API

## Limits

Verification means the checks ran and the gaps are listed, not that a claim is true. The judge is
also a language model, though it only sees the cited text and its criteria are fixed before the
research starts. The paper benchmark is small (six papers) and scored by a model; the SciFact
labels come from experts. Scanned PDFs need OCR, which we do not do yet.

The onboarding video and downloadable protocol are from
[LabSuperVision / LabOS LSV](https://huggingface.co/datasets/cong-lab/lsv), under CC BY-NC 4.0. The
video is a 30-second preparation excerpt.
