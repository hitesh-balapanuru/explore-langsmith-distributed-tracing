# explore-langsmith-distributed-tracing

A sample multi-container agent used to explore LangSmith's distributed
tracing capability, by instrumenting the *same* RAG agent two different ways
and sending both into LangSmith:

- **`agent-langsmith/`** — traced natively with the LangSmith Python SDK
  (`@traceable`, `RunTree`).
- **`agent-openllmetry/`** — traced with [OpenLLMetry](https://github.com/traceloop/openllmetry)
  (Traceloop SDK), exporting spans via OTLP directly to
  [LangSmith's OTLP ingestion endpoint](https://docs.smith.langchain.com/observability/how_to_guides/trace_with_opentelemetry).

Both land traces in the same LangSmith project, so you can compare what a
"native" LangSmith trace looks like versus a standard OpenTelemetry trace
ingested through OTLP.

The **`frontend/`** is a TypeScript/Express service and is the deliberate
**trace origin**: it's the only place holding API keys (nothing is ever
shipped to the browser), and it originates a distinct trace for each request
using two different propagation mechanisms:

- for `agent-langsmith`, it creates a LangSmith `RunTree` and sends its
  `langsmith-trace` / `baggage` headers;
- for `agent-openllmetry`, it creates a real OpenTelemetry span and sends a
  standard W3C `traceparent` header.

Each agent extracts the corresponding header and continues the *same* trace
server-side — so in LangSmith you'll see one trace per request spanning the
Node frontend and the Python agent, not two disconnected traces.

## Architecture

```
                        ┌─────────────────────────┐
   browser  ──────────► │ frontend (TS/Express)   │
                        │  - originates trace      │
                        │  - holds API keys        │
                        └─────────────┬───────────┘
                     langsmith-trace  │  traceparent
                        headers ◄─────┴─────► headers
                            │                    │
                            ▼                    ▼
                ┌────────────────────┐  ┌─────────────────────┐
                │ agent-langsmith    │  │ agent-openllmetry    │
                │ LangChain RAG      │  │ LangChain RAG        │
                │ + ChatAnthropic    │  │ + ChatAnthropic      │
                │ traced via         │  │ traced via Traceloop │
                │ LangSmith SDK      │  │ SDK → OTLP           │
                └─────────┬──────────┘  └──────────┬───────────┘
                          │                          │
                          ▼                          ▼
                  ┌───────────────────────────────────────┐
                  │      FAISS index (shared volume)       │
                  │   built once by vector-init from docs/ │
                  └───────────────────────────────────────┘
                          │                          │
                          └──────────► LangSmith ◄────┘
                              (native SDK ingestion)
                              (OTLP ingestion)
```

## Services

| Service | Language | Role |
|---|---|---|
| `vector-init` | Python | One-shot job: embeds `docs/*.md` (via local `fastembed` model, no external embeddings API) into a FAISS index on a shared volume, then exits |
| `agent-langsmith` | Python / FastAPI / LangChain | RAG agent, `ChatAnthropic`, traced with the LangSmith SDK |
| `agent-openllmetry` | Python / FastAPI / LangChain | Same RAG agent, traced with OpenLLMetry (Traceloop SDK) → OTLP → LangSmith |
| `frontend` | TypeScript / Express | Trace origin + minimal chat UI, proxies to both agents |

## Setup

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY and LANGSMITH_API_KEY in .env

docker compose up --build
```

Then open http://localhost:3000, ask a question about the NIST AI Risk
Management Framework (AI RMF) docs in `docs/`, and pick which backend
answers it.

Each response includes a `traceId` — look it up in your LangSmith project
(`LANGSMITH_PROJECT` in `.env`, default `explore-langsmith-tracing`) to see
the full frontend → agent trace.

## Why two folders instead of one

The point isn't the RAG agent (it's intentionally the same trivial agent in
both folders) — it's comparing *how a trace looks* depending on whether it's
produced by LangSmith's own SDK versus a standards-based OpenTelemetry
pipeline pointed at LangSmith's OTLP endpoint. That's directly useful if
you're deciding whether to adopt LangSmith via its native SDK, via an
existing OTel pipeline, or both.

## Extending

- The frontend's OpenTelemetry span is exported to LangSmith directly, but a
  more realistic browser-based version would run OTel Web SDK in the browser
  and export through a backend proxy (to avoid ever exposing
  `LANGSMITH_API_KEY` client-side) — left as an exercise.
- `docs/` currently holds a small corpus on the NIST AI RMF (overview, FAQ,
  core functions); swap in your own documents and the same `vector-init`
  ingestion will pick them up (any `*.md` file under `docs/`).
- Package versions pinned in each `requirements.txt` / `package.json` were
  current as of this repo's creation — the LangSmith and Traceloop SDKs move
  quickly, so check their docs if an API (e.g. `RunTree.from_headers`,
  `Traceloop.init`) has changed shape.
