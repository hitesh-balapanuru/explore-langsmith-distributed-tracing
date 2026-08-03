"""agent-openllmetry: the same RAG agent as agent-langsmith, instrumented
instead with OpenLLMetry (Traceloop SDK), exporting via OTLP straight to
LangSmith's OTLP endpoint. This lets the two folders be compared side by side
inside the same LangSmith project.

Distributed tracing: the frontend originates a standard W3C `traceparent`
header (via the OpenTelemetry Node SDK). We extract it with the OTel
propagator and attach it as the current context before invoking the chain, so
every span Traceloop records here is a child of the frontend's span in the
same trace, landing in LANGSMITH_PROJECT (the "distributed" project).

Multi-project fan-out (see tracing_init.py): every span is ALSO duplicated
into LANGSMITH_PROJECT_AGENT, and the "agent-openllmetry.retrieve" span
specifically is ALSO duplicated into LANGSMITH_PROJECT_VECTORDB -- all for
free, since it's the same recorded span exported to multiple destinations.
"""

import logging
import os
import uuid

from fastapi import FastAPI, Request
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from pydantic import BaseModel

from tracing_init import init_tracing

init_tracing()

from rag_chain import build_answer_chain, build_retriever, format_docs  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-openllmetry")

app = FastAPI(title="agent-openllmetry")
retriever = build_retriever()
answer_chain = build_answer_chain()
tracer = trace.get_tracer("agent-openllmetry")


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
        with tracer.start_as_current_span(
            "agent-openllmetry.answer_question"
        ) as span:
            span.set_attribute("airmf.question", body.question)
            span.set_attribute("langsmith.metadata.request_id", request_id)
            logger.info(
                "Handling request under trace %s",
                format(span.get_span_context().trace_id, "032x"),
            )

            with tracer.start_as_current_span(
                "agent-openllmetry.retrieve"
            ) as retrieve_span:
                retrieve_span.set_attribute(
                    "langsmith.metadata.request_id", request_id
                )
                docs = retriever.invoke(body.question)
                retrieve_span.set_attribute("airmf.retrieved_count", len(docs))

            context = format_docs(docs)
            answer = answer_chain.invoke(
                {"question": body.question, "context": context}
            )
    finally:
        otel_context.detach(token)

    return QueryResponse(answer=answer)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
