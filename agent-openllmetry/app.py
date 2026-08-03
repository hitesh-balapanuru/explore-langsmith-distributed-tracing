"""agent-openllmetry: the same RAG agent as agent-langsmith, instrumented
instead with OpenLLMetry (Traceloop SDK), exporting via OTLP straight to
LangSmith's OTLP endpoint. This lets the two folders be compared side by side
inside the same LangSmith project.

Distributed tracing: the frontend originates a standard W3C `traceparent`
header (via the OpenTelemetry Node SDK). We extract it with the OTel
propagator and attach it as the current context before invoking the chain, so
every span Traceloop records here is a child of the frontend's span in the
same trace, landing in LANGSMITH_PROJECT (the "distributed" project).

Multi-project fan-out (see tracing_init.py): after the real work finishes, we
manually emit two duplicate spans -- one for the whole request into
LANGSMITH_PROJECT_AGENT, one for just retrieval into
LANGSMITH_PROJECT_VECTORDB. Each gets its own independent trace_id (sharing
the real trace_id was tried and confirmed broken: LangSmith's OTLP ingestion
pins project ownership per trace_id to whichever project's span for that
trace_id it saw first, so a same-trace_id duplicate just gets swept into
that project too instead of its intended one). Correlation with the
connected trace relies on `request_id`/`distributed_trace_id` attributes.

Input/output: Traceloop's own LangChain instrumentation populates LangSmith's
Input/Output UI via the `traceloop.entity.input`/`traceloop.entity.output`
span attributes (JSON-encoded strings -- confirmed by reading
traceloop.sdk.decorators.base's _handle_span_input/_handle_span_output). Our
own manually-created spans (both real and duplicate) need to set these
explicitly, or LangSmith shows them with empty Input/Output despite the
nested LLM/chain spans underneath having it populated correctly.
"""

import json
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from pydantic import BaseModel

from tracing_init import duplicate_span, init_tracing

init_tracing()

from rag_chain import build_answer_chain, build_retriever, format_docs  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-openllmetry")

app = FastAPI(title="agent-openllmetry")
retriever = build_retriever()
answer_chain = build_answer_chain()
tracer = trace.get_tracer("agent-openllmetry")

ENTITY_INPUT = "traceloop.entity.input"
ENTITY_OUTPUT = "traceloop.entity.output"


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    headers = dict(request.headers)
    ctx = extract(headers)
    request_id = headers.get("x-request-id", str(uuid.uuid4()))

    token = otel_context.attach(ctx)
    try:
        answer_start_ns = time.time_ns()
        with tracer.start_as_current_span(
            "agent-openllmetry.answer_question"
        ) as span:
            span.set_attribute("langsmith.metadata.request_id", request_id)
            span.set_attribute(
                ENTITY_INPUT, json.dumps({"question": body.question})
            )
            trace_id = span.get_span_context().trace_id
            distributed_trace_id = format(trace_id, "032x")
            logger.info("Handling request under trace %s", distributed_trace_id)

            retrieve_start_ns = time.time_ns()
            with tracer.start_as_current_span(
                "agent-openllmetry.retrieve"
            ) as retrieve_span:
                retrieve_span.set_attribute(
                    "langsmith.metadata.request_id", request_id
                )
                retrieve_span.set_attribute(
                    ENTITY_INPUT, json.dumps({"question": body.question})
                )
                docs = retriever.invoke(body.question)
                retrieve_output = {
                    "count": len(docs),
                    "documents": [doc.page_content[:200] for doc in docs],
                }
                retrieve_span.set_attribute(
                    ENTITY_OUTPUT, json.dumps(retrieve_output)
                )
            retrieve_end_ns = time.time_ns()

            context = format_docs(docs)
            answer = answer_chain.invoke(
                {"question": body.question, "context": context}
            )
            span.set_attribute(ENTITY_OUTPUT, json.dumps({"answer": answer}))
        answer_end_ns = time.time_ns()
    finally:
        otel_context.detach(token)

    try:
        duplicate_span(
            "agent",
            "agent-openllmetry.answer_question",
            answer_start_ns,
            answer_end_ns,
            {
                ENTITY_INPUT: json.dumps({"question": body.question}),
                ENTITY_OUTPUT: json.dumps({"answer": answer}),
                "langsmith.metadata.request_id": request_id,
                "langsmith.metadata.distributed_trace_id": distributed_trace_id,
            },
        )
        duplicate_span(
            "vectordb",
            "agent-openllmetry.retrieve",
            retrieve_start_ns,
            retrieve_end_ns,
            {
                ENTITY_INPUT: json.dumps({"question": body.question}),
                ENTITY_OUTPUT: json.dumps(retrieve_output),
                "langsmith.metadata.request_id": request_id,
                "langsmith.metadata.distributed_trace_id": distributed_trace_id,
            },
        )
    except Exception:
        logger.exception("Failed to emit duplicate spans")

    return QueryResponse(answer=answer)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
