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

(This diagram shows the connected "distributed" trace only. Each service
also duplicates its own work into a per-service project — see "Multi-project
fan-out" below.)

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

Each response includes a `traceId` and a `requestId` — look the `traceId` up
in the `LANGSMITH_PROJECT` project (default `distributed`) to see the full
frontend → agent trace, or see "Multi-project fan-out" below for how to find
this same request's copies in the other three projects.

## Multi-project fan-out

Beyond the one connected trace in the `distributed` project, every service
also posts a duplicate of its own work into a **per-service LangSmith
project** — `frontend`, `agent` (used by whichever agent handled the
request), and `vector_database` (retrieval only). That's 4 projects total,
configured via `LANGSMITH_PROJECT`, `LANGSMITH_PROJECT_FRONTEND`,
`LANGSMITH_PROJECT_AGENT`, and `LANGSMITH_PROJECT_VECTORDB` in `.env` — set
these to match whatever you've already created in LangSmith if the names
differ.

This is a deliberate trade-off, not a LangSmith feature: **a trace belongs to
exactly one project**, decided by its root run, and (confirmed against a
real LangSmith account) **a run's identity can't be transplanted into
another project's trace** either. Two things that seem like they should work
don't:

- Registering a *second* `SpanProcessor` on the same `TracerProvider` and
  re-exporting the exact same span to a second project's OTLP endpoint gets
  rejected with `409 Run create payload already received` — LangSmith's OTLP
  ingestion treats `(trace_id, span_id)` as globally unique, not scoped per
  project.
- Passing an explicit `trace_id` on a fresh, unparented run/RunTree (native
  SDK) gets rejected with `400 invalid dotted_order` — the API requires
  `dotted_order` you don't have unless you also compute it yourself.

So every duplicate is its own real, independent run, and the two sides of
the repo get there differently (itself an interesting SDK-comparison point):

- **`agent-openllmetry` and `frontend`** (OpenTelemetry-based): after the
  real span ends, a *second*, dedicated single-exporter `TracerProvider`
  (pointed at the per-service project) manually emits a **new span that
  shares the real trace_id but gets a fresh span_id** — done by wrapping the
  real span's `SpanContext` as a fake parent (`trace.wrapSpanContext` /
  Python's `NonRecordingSpan`) so the new span inherits `trace_id` without
  colliding on `span_id`. One extra OTLP POST per duplicate, no
  re-execution.
- **`agent-langsmith`** (native LangSmith SDK): after the single chain
  execution completes, the code posts *flat* summary runs (question in,
  answer out, no nested retriever/model sub-spans) to the `agent` and
  `vector_database` projects via `Client.create_run`/`update_run` — each as
  its **own root run with its own trace_id**, since a matching trace_id
  isn't achievable here without a hand-computed `dotted_order`.

**Correlating a request across all four projects**: the OTel-based
duplicates (`agent-openllmetry`, `frontend`) do share the real trace_id, so
those can be found by trace ID. The native-SDK duplicates (`agent-langsmith`
and frontend's LangSmith-path copy) cannot share it, so use the
`request_id` metadata field instead — it's stamped on every run in every
project regardless of instrumentation, making it the one correlation key
that works everywhere. The real distributed trace_id is also echoed into
those runs' metadata (`distributed_trace_id`) for reference. There's no
single UI that shows all four at once — this is manual, cross-project
correlation, not native distributed tracing.

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
