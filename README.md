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

This is a deliberate trade-off, not a LangSmith feature you get for free:
**a trace belongs to exactly one project**, decided by its root run. Getting
a per-service view *and* a connected view means posting the data twice, and
the two sides of this repo do that two different ways — one native, one
hand-rolled, which is itself an interesting comparison point.

### `agent-openllmetry` and `frontend`'s OTel path: hand-rolled duplication

OpenTelemetry has no first-class "write to multiple projects" primitive, so
this side manually emits a **second span with a fresh, unrelated trace_id**
after the real one ends (an explicit empty/root context — `Context()` in
Python, `ROOT_CONTEXT` in JS — forces the SDK to mint a new one instead of
inheriting the real trace), through a dedicated single-exporter
`TracerProvider` pointed at the per-service project. Two things that seem
like they should work instead don't, confirmed against a real account:

- Registering a *second* `SpanProcessor` on the same `TracerProvider` and
  re-exporting the exact same span to a second project's OTLP endpoint gets
  rejected with `409 Run create payload already received` — LangSmith's OTLP
  ingestion treats `(trace_id, span_id)` as globally unique, not scoped per
  project.
- Emitting a *separate* span with a fresh span_id but the SAME trace_id as
  the real trace (via a fake parent context) *is* accepted — no error — but
  it silently lands in whichever project's span for that trace_id LangSmith's
  backend saw first, ignoring this new span's own `Langsmith-Project`
  header. LangSmith appears to pin **project ownership per trace_id**, not
  per span. This one took direct `list_runs()` queries against a real
  account to catch, since nothing in the logs indicates a misroute — the
  export just "succeeds."

### `agent-langsmith` and `frontend`'s LangSmith path: native `replicas`

The native LangSmith SDK *does* have a first-class mechanism for this:
`tracing_context(replicas=[...])` in Python, and a `replicas` field directly
on `RunTree`/`traceable()` in both languages. A `WriteReplica` entry is
`{"project_name": ..., "primary": bool, "reroot": bool, ...}` (JS:
`projectName`/`reroot`, same idea) — `primary=True` keeps the real run/trace
ids as-is; without it, the SDK computes a **deterministic secondary run_id**
from the primary's (via `compute_run_id_for_secondary_replica`), so
correlating a duplicate back to its primary doesn't need any metadata hack
at all. Passing a list of replicas **replaces** the default single
destination entirely, so the primary project has to be listed explicitly.

`reroot: True` matters specifically because forcing an explicit `trace_id`
onto a fresh, unparented run gets rejected with `400 invalid dotted_order`
(the API needs a `dotted_order` you don't have unless you compute it
yourself) — `reroot` strips the parent link and makes that replica its own
independent trace root in its project, sidestepping the problem entirely.

One non-obvious gotcha, found by reading `langsmith/run_helpers.py` +
`run_trees.py`: a `@traceable` call that has a parent (via
`langsmith_extra={"parent": ...}`, or an ambient current run) builds its
`RunTree` via `parent.create_child(...)`, which sets
`replicas=self.replicas` — it inherits from the **parent object's own
`.replicas` attribute**, not from the ambient `tracing_context(replicas=
...)` contextvar. Since a `RunTree` reconstructed from cross-service headers
has `replicas=None`, `answer_question`'s own run would otherwise silently
fall back to single-destination posting even inside a `tracing_context`
block — LangChain's callback-based auto-tracing (the `ChatAnthropic`/
`ChatPromptTemplate`/etc spans) goes through a different path that *does*
read the contextvar fresh, so those spans replicate correctly on their own.
The fix (see `agent-langsmith/app.py`) is to set `.replicas` directly on the
relevant parent `RunTree` object before calling the child.

**Net result, confirmed live**: `agent`/`vector_database` projects now get
the *full* nested run tree (not a flat summary) as their own independently
rooted copy — richer than the hand-rolled version ever produced, and about
a third of the code.

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
